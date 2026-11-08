"""Adversarial workload-runner tests; these are not live model/UI qualification."""

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import time

import pytest

from assistant_workbench.app import Application
from assistant_workbench.util import WorkbenchError


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("scenario_runner", ROOT / "scripts/run_scenarios.py")
scenarios = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scenarios)


@pytest.fixture(scope="module")
def completed_control(tmp_path_factory):
    root = tmp_path_factory.mktemp("scenario-control")
    home, output = root / "home", root / "results.json"
    observed = []

    def checkpoint(step_id, result):
        saved = json.loads(output.read_text())
        assert saved["steps"][-1]["id"] == step_id
        assert saved["steps"][-1]["status"] == result["status"]
        snapshot = json.loads(Path(result["snapshot"]).read_text())
        assert len(snapshot["public_snapshot"]["projects"]) == 3
        observed.append(step_id)
        return {"kind": "test_callback", "notice": "Persistence observation, not a native terminal screenshot"}

    result = scenarios.run_scenarios(home, output, run_id="test-control", on_step=checkpoint)
    return home, output, result, observed


def test_thirty_instructions_persist_real_state_without_fake_products(completed_control):
    home, output, result, observed = completed_control
    assert result["counts"] == {"passed": 30}
    assert result["bookkeeping_status"] == "passed" and result["durable_prompts"] == 22
    assert observed == [f"S{i:02d}" for i in range(1, 31)]
    app = Application(home)
    assert app.dispatch("outcome.list") == []
    assert app.store.rows("SELECT * FROM reviews") == []
    assert all(row["status"] != "accepted" for row in app.dispatch("snapshot")["requests"])
    assert result["delivery"]["status"] == "blocked" and not result["delivery"]["qualified"]
    journal = [json.loads(line) for line in output.with_suffix(".jsonl").read_text().splitlines()]
    assert len([row for row in journal if row["type"] == "step"]) == 30
    assert journal[-1]["type"] == "run_finished"


def test_conflicting_requirements_group_identity_and_stale_rejection_are_observed(completed_control):
    home, _, result, _ = completed_control
    app = Application(home)
    aliases = result["request_aliases"]
    website = app.dispatch("request.get", {"request_id": aliases["website"]})
    assert "tracking disabled" in website["body"] and "optional aggregate usage report" in website["body"]
    mobile = app.dispatch("request.get", {"request_id": aliases["mobile"]})
    assert "No account should be required" in mobile["body"] and "optional encrypted cloud sync" in mobile["body"]
    old = app.dispatch("request.get", {"request_id": aliases["web_filters"]})
    reopened = app.dispatch("request.get", {"request_id": aliases["web_filters_reopened"]})
    assert old["status"] == "cancelled" and old["body"] == reopened["body"] and old["id"] != reopened["id"]
    steps = {row["id"]: row for row in result["steps"]}
    assert steps["S20"]["details"]["receipt"]["duplicate"]
    assert "Revision changed" in steps["S21"]["details"]["expected_rejection"]
    assigned = app.dispatch("request.get", {"request_id": aliases["ambiguous"]})
    assert assigned["project_id"] == result["projects"]["EvidenceReview"]["id"]
    assert app.store.rows("SELECT * FROM grants") == []


def test_scope_and_context_budget_checks_do_not_claim_execution_rollover(completed_control):
    _, _, result, _ = completed_control
    steps = {row["id"]: row for row in result["steps"]}
    assert steps["S27"]["details"]["foreign_read"] == "denied"
    large = steps["S28"]["details"]
    assert large["prompt_chars"] > 64000
    assert large["context_packet"]["used_chars"] <= 1000 and large["context_packet"]["omitted"]
    assert large["execution_preflight"]["status"] == "blocked"
    paused = json.loads(Path(steps["S25"]["snapshot"]).read_text())
    research_id = result["projects"]["EvidenceReview"]["id"]
    assert next(row for row in paused["audit"]["project_policy"] if row["project_id"] == research_id)["paused"] == 1


@pytest.mark.parametrize("damage", ["duplicate_id", "forward_reference", "unknown_action", "invalid_feature", "oversized_input"])
def test_invalid_manifest_is_rejected_before_execution(damage):
    manifest = json.loads((ROOT / "evals/scenarios-30.json").read_text())
    if damage == "duplicate_id":
        manifest["scenarios"][1]["id"] = "S01"
    elif damage == "forward_reference":
        manifest["scenarios"][0]["target"] = "future-request"
    elif damage == "unknown_action":
        manifest["scenarios"][0]["action"] = "execute_arbitrary_shell"
    elif damage == "invalid_feature":
        manifest["scenarios"][0]["feature_ids"] = ["PF99"]
    else:
        manifest["scenarios"][27]["payload"]["target_characters"] = 10000001
    with pytest.raises(scenarios.ScenarioError):
        scenarios.validate_manifest(manifest)


def test_prior_evidence_run_ids_and_foreign_projects_are_not_overwritten(completed_control, tmp_path):
    home, output, result, _ = completed_control
    saved = output.read_bytes()
    with pytest.raises(scenarios.ScenarioError, match="Evidence already exists"):
        scenarios.run_scenarios(home, output)
    with pytest.raises(scenarios.ScenarioError, match="Run ID already"):
        scenarios.run_scenarios(home, tmp_path / "new.json", run_id=result["run_id"])
    assert output.read_bytes() == saved
    foreign_home = tmp_path / "foreign-home"
    app = Application(foreign_home)
    foreign_repo = tmp_path / "real-project"
    foreign_repo.mkdir()
    app.dispatch("project.add", {"name": "HarborWeb", "repository": str(foreign_repo)})
    original_config = (foreign_home / "config.json").read_bytes()
    with pytest.raises(scenarios.ScenarioError, match="isolated scenario home"):
        scenarios.run_scenarios(foreign_home, tmp_path / "foreign.json")
    assert len(app.dispatch("project.list")) == 1
    assert (foreign_home / "config.json").read_bytes() == original_config


def test_evidence_paths_cannot_overwrite_new_runtime_state_or_their_journal(tmp_path):
    home = tmp_path / "home"
    with pytest.raises(scenarios.ScenarioError, match="outside the runtime home"):
        scenarios.run_scenarios(home, home / "config.json")
    assert not home.exists()
    with pytest.raises(scenarios.ScenarioError, match="distinct paths"):
        scenarios.run_scenarios(home, tmp_path / "evidence.jsonl")
    assert not home.exists()


def test_capture_failure_is_recorded_and_does_not_erase_remaining_steps(tmp_path):
    def capture(step_id, result):
        if step_id == "S08":
            raise RuntimeError("Controlled screenshot failure")

    result = scenarios.run_scenarios(tmp_path / "home", tmp_path / "report.json", on_step=capture)
    assert len(result["steps"]) == 30 and result["bookkeeping_status"] == "passed"
    assert result["capture_failures"] == [{"step": "S08", "error_type": "RuntimeError", "error": "Controlled screenshot failure"}]
    assert result["steps"][7]["observation"]["status"] == "failed"


def test_injected_stale_acceptance_defect_is_detected(tmp_path, monkeypatch):
    original = Application.dispatch

    def regressed(self, action, args=None):
        args = dict(args or {})
        if action == "prompt.submit" and args.get("text", "").startswith("Try to add a paid booking"):
            current = original(self, "request.get", {"request_id": args["reply_to"]})
            args["expected_revision"] = current["revision"]
        return original(self, action, args)

    monkeypatch.setattr(Application, "dispatch", regressed)
    result = scenarios.run_scenarios(tmp_path / "home", tmp_path / "report.json")
    assert result["bookkeeping_status"] == "failed"
    assert result["steps"][20]["status"] == "failed"
    assert "stale amendment was accepted" in result["steps"][20]["error"]


def test_missing_initial_request_blocks_dependents_without_hiding_other_projects(tmp_path, monkeypatch):
    original = scenarios.ScenarioRunner.submit

    def unavailable(self, step, text=None):
        if step["id"] == "S01":
            raise WorkbenchError("Controlled intake failure")
        return original(self, step, text)

    monkeypatch.setattr(scenarios.ScenarioRunner, "submit", unavailable)
    result = scenarios.run_scenarios(tmp_path / "home", tmp_path / "report.json")
    assert result["steps"][0]["status"] == "failed"
    assert result["steps"][3]["status"] == "blocked"
    assert result["steps"][1]["status"] == result["steps"][2]["status"] == "passed"
    assert result["delivery"]["projects"]["website"]["missing"] == ["Initial request was not created"]


def test_fixture_accepted_text_cannot_certify_live_delivery(tmp_path):
    app = Application(tmp_path / "home")
    project = app.dispatch("project.add", {"name": "Test"})
    request = app.dispatch("prompt.submit", {"text": "Create a website", "client_key": "fixture", "project": project["id"]})
    output = app.dispatch("outcome.propose", {"request_id": request["request_id"], "body": "FIXTURE: finished website", "claims": [], "author": "fixture-author", "revision": 1})
    app.dispatch("outcome.review", {"outcome_id": output["id"], "reviewer": "fixture-reviewer", "verdict": "accept", "notes": "Controlled fixture only"})
    result = scenarios.inspect_delivery(app, {"website": request["request_id"]})
    assert result["status"] == "blocked" and not result["qualified"]
    assert result["projects"]["website"]["execution_candidate_outcomes"] == []


def test_live_probe_timeout_preserves_uncertainty(tmp_path, monkeypatch):
    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout)
    result = scenarios.coordinator_probe(tmp_path, "coordinator.tick", tmp_path / "probe.json", .1)
    assert result["status"] == "blocked" and "uncertain effect" in result["reason"]


def test_live_mode_with_unconfigured_coordinator_is_bounded_and_blocked(tmp_path):
    started = time.monotonic()
    result = scenarios.run_scenarios(tmp_path / "home", tmp_path / "report.json", live=True, max_wait_seconds=.5)
    assert time.monotonic() - started < 10
    assert result["bookkeeping_status"] == "passed"
    assert result["live_probes"]
    assert all(row["status"] in {"observed", "blocked"} for row in result["live_probes"])
    # A successful local cancellation may consume this short deadline before
    # model admission is attempted. Neither that nor an empty observation is
    # assistant execution; the product-delivery verdict must remain blocked.
    assert result["delivery"]["status"] == "blocked"
    assert not result["delivery"]["qualified"]
    assert all(not row.get("execution_candidate_outcomes") for row in result["delivery"]["projects"].values())


@pytest.mark.parametrize("wait", [0, -1, 301, True, float("nan")])
def test_invalid_live_deadline_is_rejected(tmp_path, wait):
    with pytest.raises(scenarios.ScenarioError, match="bounded live wait"):
        scenarios.run_scenarios(tmp_path / "home", tmp_path / "report.json", max_wait_seconds=wait)

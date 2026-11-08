"""Adversarial tests for evidence accounting, not live feature qualification."""

import copy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("feature_coverage", ROOT / "scripts/evaluate_feature_coverage.py")
coverage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(coverage)


@pytest.fixture
def evaluation_input(tmp_path):
    """A disposable catalog deliberately used only to test the evaluator."""
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    (root / "tests/test_sample.py").write_text("def test_example():\n    assert True\n")
    features = [
        {"id": ident, "name": "Evaluator fixture " + ident, "status": "implemented", "remaining": ""}
        for ident in coverage.FEATURE_IDS
    ]
    manifest = {
        "schema": 1,
        "gates": {"observed": {"description": "Evaluator fixture only", "kinds": ["local_runtime"]}},
        "features": [
            {
                "feature_id": ident, "scenario_id": "E-" + ident,
                "scenario": "Evaluator fixture scenario", "happy_path": "Record a known passing observation",
                "failure_path": "Retain a deliberate failure", "edge_case": "Do not hide absent data",
                "automated_scope": "Evaluator fixture only", "limit": "No product execution",
                "prerequisites": ["Disposable fixture repository"],
                "automated_tests": ["tests/test_sample.py::test_example"],
                "required_gates": ["observed"], "journey_tags": ["fixture"],
            }
            for ident in coverage.FEATURE_IDS
        ],
    }
    stamp = datetime.now(timezone.utc)
    junit = {"cases": {"tests/test_sample.py::test_example": {"status": "passed"}}, "suite_timestamps": [stamp.timestamp()]}
    fingerprint = coverage.source_fingerprint(root)
    metadata = {"source_fingerprint": fingerprint, "started_at": stamp.isoformat()}
    (root / "evidence").mkdir()
    artifact = root / "evidence/fixture.txt"
    artifact.write_text("DETERMINISTIC EVALUATOR TEST FIXTURE. Not a live feature receipt.")
    receipts = {
        "schema": 1,
        "results": [
            {
                "feature_id": ident, "scenario_id": "E-" + ident, "gate": "observed",
                "status": "passed", "kind": "local_runtime", "observer": "evaluator-test-fixture",
                "notes": "Synthetic input to exercise evidence accounting only",
                "acceptance": {"happy_path": "passed", "failure_path": "passed", "edge_case": "passed"},
                "source_fingerprint": fingerprint, "observed_at": stamp.isoformat(),
                "artifacts": [{"path": "evidence/fixture.txt", "sha256": coverage.sha256_file(artifact)}],
            }
            for ident in coverage.FEATURE_IDS
        ],
    }
    return dict(features=features, manifest=manifest, junit=junit, root=root, run_metadata=metadata, receipts=receipts, now=stamp.timestamp())


def test_repository_matrix_has_exactly_71_explicit_scenarios_and_real_test_functions():
    features = json.loads((ROOT / "features.json").read_text())
    manifest = json.loads((ROOT / "evals/feature-evaluations.json").read_text())
    cases = coverage.validate_manifest(manifest, features, ROOT)
    assert len(cases) == 71
    assert all(c["happy_path"] != c["failure_path"] != c["edge_case"] for c in cases)


def test_missing_feature_or_undefined_gate_invalidates_manifest(evaluation_input):
    data = evaluation_input
    broken = copy.deepcopy(data["manifest"])
    broken["features"].pop()
    with pytest.raises(coverage.EvidenceError, match="exactly once"):
        coverage.validate_manifest(broken, data["features"])
    broken = copy.deepcopy(data["manifest"])
    broken["features"][0]["required_gates"] = ["invented"]
    with pytest.raises(coverage.EvidenceError, match="undefined"):
        coverage.validate_manifest(broken, data["features"])


def test_green_automated_tests_cannot_replace_live_observations(evaluation_input):
    data = {**evaluation_input, "receipts": None}
    result = coverage.evaluate(**data)
    assert result["local_counts"] == {"passed": 71}
    assert not result["qualified"] and result["counts"]["blocked"] == 71


def test_complete_fixture_input_exercises_positive_accounting_only(evaluation_input):
    result = coverage.evaluate(**evaluation_input)
    assert result["qualified"] and result["counts"]["passed"] == 71
    assert "not proof" in result["notice"]


def test_missing_or_skipped_mapped_case_never_passes(evaluation_input):
    for status in ("missing", "skipped"):
        data = copy.deepcopy(evaluation_input)
        data["junit"]["cases"]["tests/test_sample.py::test_example"]["status"] = status
        result = coverage.evaluate(**data)
        assert result["counts"]["blocked"] == 71 and not result["qualified"]


def test_fixture_label_cannot_be_promoted_to_observed_live_evidence(evaluation_input):
    evaluation_input["receipts"]["results"][0]["kind"] = "fixture"
    result = coverage.evaluate(**evaluation_input)
    assert result["features"][0]["status"] == "blocked"
    assert "Wrong evidence kind" in str(result["features"][0]["reasons"])


def test_tampered_or_external_artifact_blocks_evidence(evaluation_input, tmp_path):
    data = evaluation_input
    (data["root"] / "evidence/fixture.txt").write_text("changed")
    result = coverage.evaluate(**data)
    assert result["counts"]["blocked"] == 71
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    data["receipts"]["results"][0]["artifacts"] = [{"path": str(outside), "sha256": coverage.sha256_file(outside)}]
    result = coverage.evaluate(**data)
    assert "outside" in str(result["features"][0]["reasons"])


def test_old_results_or_changed_sources_cannot_certify_new_implementation(evaluation_input):
    data = evaluation_input
    data["junit"]["suite_timestamps"] = [data["now"] - 60]
    result = coverage.evaluate(**data)
    assert result["counts"]["blocked"] == 71
    data["junit"]["suite_timestamps"] = [data["now"]]
    (data["root"] / "tests/test_sample.py").write_text("def test_example():\n    assert False\n")
    result = coverage.evaluate(**data)
    assert result["counts"]["blocked"] == 71
    assert "fingerprint" in str(result["features"][0]["reasons"])


def test_stale_future_and_unattributed_observations_are_blocked(evaluation_input):
    data = evaluation_input
    data["receipts"]["results"][0]["observed_at"] = "2000-01-01T00:00:00+00:00"
    data["receipts"]["results"][1]["observed_at"] = "2100-01-01T00:00:00+00:00"
    data["receipts"]["results"][2]["observer"] = ""
    result = coverage.evaluate(**data)
    assert all(r["status"] == "blocked" for r in result["features"][:3])


def test_incomplete_catalog_or_no_direct_tests_do_not_become_full_passes(evaluation_input):
    data = evaluation_input
    data["features"][0]["status"] = "partial"
    data["features"][1]["status"] = "not-implemented (optional)"
    data["manifest"]["features"][2]["automated_tests"] = []
    result = coverage.evaluate(**data)
    assert [r["status"] for r in result["features"][:3]] == ["blocked", "not_implemented", "blocked"]


def test_duplicate_or_unmatched_observations_require_reconciliation(evaluation_input):
    data = evaluation_input
    data["receipts"]["results"].append(copy.deepcopy(data["receipts"]["results"][0]))
    with pytest.raises(coverage.EvidenceError, match="Duplicate"):
        coverage.evaluate(**data)
    data["receipts"]["results"][-1]["scenario_id"] = "wrong-scenario"
    with pytest.raises(coverage.EvidenceError, match="does not match"):
        coverage.evaluate(**data)


def test_junit_failure_cannot_be_hidden_by_a_second_pass(tmp_path):
    paths = []
    for number, status in enumerate(("failure", "pass", "skipped")):
        suite = ET.Element("testsuite", timestamp=datetime.now(timezone.utc).isoformat())
        case = ET.SubElement(suite, "testcase", classname="tests.test_sample", name="test_example")
        if status != "pass":
            ET.SubElement(case, status)
        path = tmp_path / f"{number}.xml"
        ET.ElementTree(suite).write(path)
        paths.append(path)
    result = coverage.parse_junit(paths)
    assert result["cases"]["tests/test_sample.py::test_example"]["status"] == "failed"
    # Installed-artifact CI legitimately runs an initially skipped case later.
    result = coverage.parse_junit(paths[1:])
    assert result["cases"]["tests/test_sample.py::test_example"]["status"] == "passed"


def test_observed_or_automated_failure_stays_a_failure(evaluation_input):
    data = evaluation_input
    data["receipts"]["results"][0].update(status="failed", notes="Observed failure retained")
    result = coverage.evaluate(**data)
    assert result["features"][0]["status"] == "failed"
    data["junit"]["cases"]["tests/test_sample.py::test_example"]["status"] = "failed"
    result = coverage.evaluate(**data)
    assert result["counts"]["failed"] == 71


def test_timezone_free_and_invalid_evidence_are_rejected(evaluation_input):
    with pytest.raises(coverage.EvidenceError, match="timezone"):
        coverage.parse_time("2026-11-08T10:00:00")
    evaluation_input["receipts"] = {"schema": 2, "results": []}
    with pytest.raises(coverage.EvidenceError, match="schema 1"):
        coverage.evaluate(**evaluation_input)


def test_happy_path_alone_and_failed_edge_case_cannot_qualify(evaluation_input):
    data = evaluation_input
    data["receipts"]["results"][0]["acceptance"] = {"happy_path": "passed"}
    data["receipts"]["results"][1]["acceptance"]["edge_case"] = "failed"
    data["receipts"]["results"][2]["acceptance"]["failure_path"] = "blocked"
    result = coverage.evaluate(**data)
    assert [row["status"] for row in result["features"][:3]] == ["blocked", "failed", "blocked"]


def test_numeric_metrics_prevent_short_soaks_and_mislabelled_fleets(evaluation_input):
    data = evaluation_input
    gate = data["manifest"]["gates"]["observed"]
    gate["minimum_metrics"] = {"duration_seconds": 72 * 3600, "worker_hosts": 2}
    gate["maximum_metrics"] = {"lost_prompts": 0}
    for row in data["receipts"]["results"]:
        row["metrics"] = {"duration_seconds": 72 * 3600, "worker_hosts": 2, "lost_prompts": 0}
    data["receipts"]["results"][0]["metrics"]["duration_seconds"] = 60
    data["receipts"]["results"][1]["metrics"]["worker_hosts"] = 1
    data["receipts"]["results"][2]["metrics"]["lost_prompts"] = 1
    data["receipts"]["results"][3]["metrics"]["worker_hosts"] = True
    data["receipts"]["results"][4]["metrics"]["duration_seconds"] = float("nan")
    del data["receipts"]["results"][5]["metrics"]["lost_prompts"]
    result = coverage.evaluate(**data)
    assert [row["status"] for row in result["features"][:7]] == ["failed"] * 3 + ["blocked"] * 3 + ["passed"]


def test_missing_suite_timestamp_cannot_hide_beside_fresh_suite(tmp_path):
    suites = ET.Element("testsuites")
    fresh = ET.SubElement(suites, "testsuite", timestamp=datetime.now(timezone.utc).isoformat())
    ET.SubElement(fresh, "testcase", classname="tests.test_sample", name="test_fresh")
    stale = ET.SubElement(suites, "testsuite")
    ET.SubElement(stale, "testcase", classname="tests.test_sample", name="test_stale")
    path = tmp_path / "junit.xml"
    ET.ElementTree(suites).write(path)
    with pytest.raises(coverage.EvidenceError, match="own timestamp"):
        coverage.parse_junit([path])


def test_invalid_metric_threshold_is_rejected(evaluation_input):
    data = evaluation_input
    for value in (True, "72 hours", float("inf")):
        data["manifest"]["gates"]["observed"]["minimum_metrics"] = {"duration_seconds": value}
        with pytest.raises(coverage.EvidenceError, match="finite numeric"):
            coverage.validate_manifest(data["manifest"], data["features"])


def test_unmapped_failure_prevents_whole_suite_qualification(evaluation_input):
    data = evaluation_input
    data["junit"]["cases"]["tests/test_sample.py::test_unmapped"] = {"status": "failed"}
    result = coverage.evaluate(**data)
    assert not result["qualified"]
    assert result["unmapped_test_failures"] == ["tests/test_sample.py::test_unmapped"]


def test_future_junit_blocks_source_binding(evaluation_input):
    data = evaluation_input
    data["junit"]["suite_timestamps"] = [data["now"] + 3600]
    result = coverage.evaluate(**data)
    assert not result["qualified"] and result["evidence_binding_status"] == "blocked"
    assert "future" in str(result["binding_errors"])


def test_missing_function_and_unknown_maturity_are_rejected(evaluation_input):
    data = evaluation_input
    data["manifest"]["features"][0]["automated_tests"] = ["tests/test_sample.py::test_invented"]
    with pytest.raises(coverage.EvidenceError, match="does not exist"):
        coverage.validate_manifest(data["manifest"], data["features"], data["root"])
    data["features"][0]["status"] = "probably fine"
    with pytest.raises(coverage.EvidenceError, match="Unknown implementation maturity"):
        coverage.validate_manifest(data["manifest"], data["features"])


def test_journey_references_must_match_existing_feature_assignments(evaluation_input):
    data = evaluation_input
    (data["root"] / "evals").mkdir()
    journey_path = data["root"] / "evals/scenarios-30.json"
    journey_path.write_text(json.dumps({"scenarios": [{"id": f"S{i:02d}", "feature_ids": ["PF01"]} for i in range(1, 31)]}))
    data["manifest"]["journey_manifest"] = "evals/scenarios-30.json"
    data["manifest"]["features"][0]["journey_scenarios"] = ["S01"]
    coverage.validate_manifest(data["manifest"], data["features"], data["root"])
    data["manifest"]["features"][1]["journey_scenarios"] = ["S01"]
    with pytest.raises(coverage.EvidenceError, match="does not match"):
        coverage.validate_manifest(data["manifest"], data["features"], data["root"])
    journey_path.unlink()
    with pytest.raises(coverage.EvidenceError, match="missing"):
        coverage.validate_manifest(data["manifest"], data["features"], data["root"])

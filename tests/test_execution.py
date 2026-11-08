import concurrent.futures
import json
import pytest
from assistant_workbench.engine import Engine
from assistant_workbench.policy import compile_plan, validate_plan, select_model
from assistant_workbench.continuation import Continuation
from assistant_workbench.lifecycle import Lifecycle
from assistant_workbench.util import WorkbenchError, digest
from assistant_workbench.paperclip import CoordinatorError
from conftest import FakePaperclip


def test_parallel_team_has_distinct_peers_and_review_dependencies(
    app, project, configured
):
    app.store.submit("Research the project", "a", project["id"])
    api = FakePaperclip()
    result = Engine(app.store, api).tick()
    assert result["status"] == "delivered" and len(api.issues_) == 4
    issues = list(api.issues_.values())
    assert issues[0]["assigneeAgentId"] != issues[1]["assigneeAgentId"]
    assert len(issues[2]["blockedByIssueIds"]) == 2 and issues[3][
        "blockedByIssueIds"
    ] == [issues[2]["id"]]
    # Pinned Paperclip resolves the run workspace from executionWorkspaceSettings;
    # executionWorkspacePreference alone left live runs in the shared checkout.
    assert all(
        i["executionWorkspaceSettings"] == {"mode": "isolated_workspace"} for i in issues
    )


def test_bridge_replay_is_idempotent(app, project, configured):
    r = app.store.submit("Fix bug", "a", project["id"])["request_id"]
    api = FakePaperclip()
    engine = Engine(app.store, api)
    engine.tick()
    with app.store.transaction() as db:
        db.execute("UPDATE outbox SET status='retry' WHERE request_id=?", (r,))
    engine.tick()
    assert len(api.issues_) == 2 and len(api.agents_) == 2 and len(api.projects_) == 1


def test_uncertain_agent_create_does_not_duplicate(app, project, configured):
    app.store.submit("Simple request", "a", project["id"])
    api = FakePaperclip()

    def uncertain(body):
        raise CoordinatorError("Lost response", True)

    api.create_agent = uncertain
    engine = Engine(app.store, api)
    assert engine.tick()["status"] == "uncertain"
    engine.retry(app.store.one("SELECT * FROM requests")["id"])
    assert engine.tick() is None


def test_cancel_reaches_remote_issues(app, project, configured):
    r = app.store.submit("Fix bug", "a", project["id"])["request_id"]
    api = FakePaperclip()
    engine = Engine(app.store, api)
    engine.tick()
    app.store.cancel(r)
    engine.tick()
    assert all(i["status"] == "cancelled" for i in api.issues_.values())


def test_paused_project_does_not_block_other_projects(app, project, configured):
    other = app.store.add_project("Birch")
    app.store.submit("Fix bug", "a", project["id"])
    app.store.submit("Say hello", "b", other["id"])
    app.dispatch("workflow.project_policy", {"project": project["id"], "paused": True})
    api = FakePaperclip()
    Engine(app.store, api).tick()
    assert (
        len(api.issues_) == 1
        and "Say hello" in next(iter(api.issues_.values()))["title"]
    )


def test_concurrent_bridge_calls_claim_once(app, project, configured):
    app.store.submit("Research project", "a", project["id"])
    api = FakePaperclip()
    with concurrent.futures.ThreadPoolExecutor(5) as pool:
        list(pool.map(lambda _: Engine(app.store, api).tick(), range(5)))
    assert len(api.issues_) == 4 and len(api.agents_) == 4


def test_model_override_and_missing_qualification(configured):
    model, decision = select_model(configured, "code", "test-process")
    assert model["id"] == decision["selected"]
    with pytest.raises(WorkbenchError):
        select_model(configured, "code", "missing")
    configured["models"][0]["verified"] = False
    with pytest.raises(WorkbenchError):
        select_model(configured, "code")


def test_plan_cycles_and_child_limits(app, project, configured):
    r = app.store.submit("Research", "a", project["id"])["request_id"]
    plan = compile_plan(app.dispatch("request.get", {"request_id": r}), configured)
    plan["nodes"][0]["depends_on"] = ["verify"]
    with pytest.raises(WorkbenchError):
        validate_plan(plan, configured)


def test_effort_maps_to_pinned_codex_field(app, project, configured):
    configured["models"][0].update(
        harness="codex",
        model="chosen-model",
        effort="high",
        efforts=["high"],
        auth="subscription",
    )
    from assistant_workbench.util import now

    profile = configured["models"][0]
    receipt = app.home / "probe.json"
    receipt.write_text(
        json.dumps(
            {
                "result": "passed",
                "created": now(),
                "profile": {
                    k: profile[k] for k in ("id", "harness", "model", "effort", "auth")
                },
            }
        )
    )
    profile["qualification"] = str(receipt)
    (app.home / "config.json").write_text(json.dumps(configured))
    app.store.submit("Hello", "a", project["id"])
    api = FakePaperclip()
    Engine(app.store, api).tick()
    cfg = api.agents_[0]["adapterConfig"]
    assert (
        cfg["modelReasoningEffort"] == "high"
        and cfg["dangerouslyBypassApprovalsAndSandbox"] is False
    )


def test_checkpoint_generation_budget_progress_and_effects(
    app, project, configured, tmp_path
):
    r = app.store.submit("Long task", "a", project["id"])["request_id"]
    c = Continuation(app.store, configured["limits"])
    path = tmp_path / "repo" / "result"
    path.write_text("one")
    artifacts = [{"path": str(path), "sha256": digest("one")}]
    c.effect(r, 1, "external-1", "publish")
    with pytest.raises(WorkbenchError):
        c.checkpoint(r, 1, 1, "Summary", artifacts, [])
    c.effect(r, 1, "external-1", "publish", {"destination_id": "receipt-1"})
    c.checkpoint(r, 1, 1, "Summary", artifacts, [])
    with pytest.raises(WorkbenchError):
        c.effect(r, 1, "stale", "publish")
    c.checkpoint(r, 1, 2, "New wording", artifacts, [])
    with pytest.raises(WorkbenchError):
        c.checkpoint(r, 1, 3, "More wording", artifacts, [])
    assert c.recover(r)["next_generation"] == 3


def test_checkpoint_cannot_invent_artifact_progress(app, project, configured):
    r = app.store.submit("Long task", "a", project["id"])["request_id"]
    with pytest.raises(WorkbenchError):
        Continuation(app.store, configured["limits"]).checkpoint(
            r, 1, 1, "Text", [{"name": "invented", "hash": "abc"}], []
        )


def test_rollover_waits_for_stop_receipt_then_requests_fresh_session(
    app, project, configured
):
    r = app.store.submit("Hello", "a", project["id"])["request_id"]
    api = FakePaperclip()
    Engine(app.store, api).tick()
    issue = next(iter(api.issues_.values()))
    issue["executionRunId"] = "old-run"
    Continuation(app.store, configured["limits"]).checkpoint(
        r, 1, 1, "Checkpoint", [], ["Continue"]
    )
    lifecycle = Lifecycle(app.store, api)
    lifecycle.request(r, "worker")
    api.stop_ack = False
    assert lifecycle.tick()["state"] == "quiescing" and not api.wakes
    api.stop_ack = True
    api.cancel_run("old-run")
    assert lifecycle.tick()["state"] == "dispatched"
    assert (
        api.wakes[0]["forceFreshSession"] is True
        and api.wakes[0]["payload"]["requireUnderstandingCheck"] is True
    )
    assert lifecycle.tick() is None


def test_rollover_cancelled_request_never_restarts(app, project, configured):
    r = app.store.submit("Hello", "a", project["id"])["request_id"]
    api = FakePaperclip()
    Engine(app.store, api).tick()
    Continuation(app.store, configured["limits"]).checkpoint(
        r, 1, 1, "Checkpoint", [], []
    )
    lifecycle = Lifecycle(app.store, api)
    lifecycle.request(r, "worker")
    app.store.cancel(r)
    assert lifecycle.tick()["state"] == "blocked" and not api.wakes


def test_cancellation_during_remote_mutation_cancels_new_issue(
    app, project, configured
):
    r = app.store.submit("Fix bug", "a", project["id"])["request_id"]
    api = FakePaperclip()
    original = api.create_issue

    def create_and_cancel(body):
        result = original(body)
        app.store.cancel(r)
        return result

    api.create_issue = create_and_cancel
    assert Engine(app.store, api).tick()["status"] == "superseded"
    assert all(i["status"] == "cancelled" for i in api.issues_.values())


def test_oversized_packet_is_not_silently_truncated(app, project, configured):
    configured["limits"]["context_chars"] = 500
    (app.home / "config.json").write_text(json.dumps(configured))
    app.store.submit("x" * 1000, "large", project["id"])
    api = FakePaperclip()
    result = Engine(app.store, api).tick()
    assert result["status"] == "blocked" and not api.issues_
    assert len(app.store.one("SELECT * FROM prompts")["body"]) == 1000

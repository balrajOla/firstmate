import json
import pytest

from assistant_workbench.runtime_observations import reconcile_observations
from assistant_workbench.policy import compile_plan
from assistant_workbench.util import WorkbenchError, canonical


def observation(app, project, configured):
    identity = app.store.submit("Create a website", "live-obs", project["id"])[
        "request_id"
    ]
    req = app.dispatch("request.get", {"request_id": identity})
    plan = compile_plan(req, configured)
    with app.store.transaction() as db:
        db.execute(
            "UPDATE requests SET plan=?,status='running' WHERE id=?",
            (canonical(plan), identity),
        )
    # This is explicitly a deterministic receipt contract, never a live-model test.
    nodes = {
        n["id"]: {
            "node": n["id"],
            "role": n["role"],
            "body": (
                "Authored output" if n["role"] != "qa" else "Needs artifact validation"
            ),
            "exit_code": 0,
            "stdout_sha256": "fixture",
            "host_id": "host",
        }
        for n in plan["nodes"]
    }
    return identity, {
        identity
        + ":1": {
            "workflow_id": "workflow-fixture",
            "run_id": "run-fixture",
            "revision": 1,
            "status": "completed",
            "result": {"request_id": identity, "revision": 1, "nodes": nodes},
        }
    }


def test_runtime_observation_proposes_once_never_accepts(app, project, configured):
    identity, observed = observation(app, project, configured)
    reconcile_observations(app.store, observed)
    reconcile_observations(app.store, observed)
    rows = app.store.rows("SELECT * FROM outcomes")
    assert len(rows) == 1 and rows[0]["status"] == "needs_review"
    assert rows[0]["body"] == "Authored output" and json.loads(rows[0]["claims"]) == []
    assert json.loads(rows[0]["checks"])["execution"]["reviewer_outputs"] == {
        "verify": "Needs artifact validation"
    }
    assert (
        app.dispatch("request.get", {"request_id": identity})["status"]
        == "needs_review"
    )
    assert not app.store.rows("SELECT * FROM reviews")
    assert json.loads((app.home / "observations.json").read_text())[identity]["verify"][
        "execution_only"
    ]


def test_stale_runtime_observation_does_not_publish(app, project, configured):
    identity, observed = observation(app, project, configured)
    app.store.submit(
        "Also add search",
        "amend",
        project["id"],
        reply_to=identity,
        expected_revision=1,
    )
    reconcile_observations(app.store, observed)
    assert app.store.rows("SELECT * FROM outcomes") == []
    assert json.loads((app.home / "observations.json").read_text()) == {}


@pytest.mark.parametrize("damage", ["identity", "missing", "exit", "role", "empty"])
def test_invalid_result_never_becomes_outcome(app, project, configured, damage):
    identity, observed = observation(app, project, configured)
    result = observed[identity + ":1"]["result"]
    if damage == "identity":
        result["request_id"] = "other"
    elif damage == "missing":
        del result["nodes"]["verify"]
    elif damage == "exit":
        result["nodes"]["verify"]["exit_code"] = 1
    elif damage == "role":
        result["nodes"]["verify"]["role"] = "engineer"
    else:
        result["nodes"]["verify"]["body"] = ""
    with pytest.raises(WorkbenchError):
        reconcile_observations(app.store, observed)
    assert app.store.rows("SELECT * FROM outcomes") == []


def test_changed_runtime_output_rejected(app, project, configured):
    identity, observed = observation(app, project, configured)
    reconcile_observations(app.store, observed)
    observed[identity + ":1"]["result"]["nodes"]["implement"][
        "body"
    ] = "Different output"
    with pytest.raises(WorkbenchError, match="changed"):
        reconcile_observations(app.store, observed)
    assert len(app.store.rows("SELECT * FROM outcomes")) == 1


def test_failed_runtime_moves_request_to_attention_once(app, project, configured):
    identity, observed = observation(app, project, configured)
    detail = observed[identity + ":1"]
    detail["status"] = "failed"
    del detail["result"]
    reconcile_observations(app.store, observed)
    reconcile_observations(app.store, observed)
    assert (
        app.dispatch("request.get", {"request_id": identity})["status"]
        == "needs_revision"
    )
    assert not app.store.rows("SELECT * FROM outcomes")
    assert (
        len(app.store.rows("SELECT * FROM events WHERE kind='execution.failed'")) == 1
    )


def test_partial_runtime_refresh_preserves_other_project_nodes(
    app, project, configured
):
    identity, observed = observation(app, project, configured)
    other = app.store.add_project("Another")
    second = app.store.submit("Create a mobile app", "other-project", other["id"])[
        "request_id"
    ]
    request = app.dispatch("request.get", {"request_id": second})
    plan = compile_plan(request, configured)
    with app.store.transaction() as db:
        db.execute("UPDATE requests SET plan=? WHERE id=?", (canonical(plan), second))
    pending = {
        second
        + ":1": {
            "workflow_id": "other-workflow",
            "run_id": "other-run",
            "revision": 1,
            "status": "running",
            "snapshot": {"nodes": {"implement": {"status": "scheduled"}}},
        }
    }
    reconcile_observations(app.store, pending)
    reconcile_observations(app.store, observed)
    saved = json.loads((app.home / "observations.json").read_text())
    assert set(saved) == {identity, second}
    assert saved[second]["implement"]["status"] == "scheduled"

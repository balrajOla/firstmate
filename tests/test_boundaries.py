import json
import pytest
from assistant_workbench.context import Context
from assistant_workbench.engine import Engine
from assistant_workbench.profiles import readiness
from assistant_workbench.integrations import Integrations
from assistant_workbench.util import WorkbenchError, now
from conftest import FakePaperclip


def test_group_withdrawal_preserves_surviving_demand(app, project, configured):
    ids = [
        app.store.submit(text, str(i), project["id"])["request_id"]
        for i, text in enumerate(["Fix bug one", "Fix bug two"])
    ]
    leader = app.dispatch(
        "workflow.group", {"requests": ids, "reason": "Related changes"}
    )["request_id"]
    api = FakePaperclip()
    engine = Engine(app.store, api)
    engine.tick()
    app.store.cancel(ids[0])
    request = app.dispatch("request.get", {"request_id": leader})
    assert (
        request["revision"] == 2
        and "bug two" in request["body"]
        and "bug one" not in request["body"]
    )
    engine.tick()
    engine.tick()
    old = [i for k, i in api.issues_.items() if "-1:" in k]
    assert old and all(i["status"] == "cancelled" for i in old)


def test_message_receipts_have_distinct_ordered_meanings(app, project):
    msg = app.store.send(project["id"], "author", "reviewer", "Please review", "m")
    with pytest.raises(WorkbenchError):
        app.store.receipt(msg["id"], "reviewer", "acted")
    for stage in ["delivered", "read", "acted"]:
        app.store.receipt(msg["id"], "reviewer", stage)
    assert len(app.store.rows("SELECT * FROM message_receipts")) == 3


def test_current_assertions_surface_conflicts_without_inventing_resolution(
    app, project, tmp_path
):
    c = Context(app.store)
    file = tmp_path / "data.txt"
    file.write_text("Two source assertions disagree.")
    s = c.ingest(project["id"], file)
    c.assertion(project["id"], s["id"], "budget", "14", "2026-11-07")
    second = c.assertion(project["id"], s["id"], "budget", "40", "2026-11-07")
    assert second["status"] == "needs_reconciliation" and len(second["conflicts"]) == 1


def test_purge_keeps_shared_original_until_last_owner_removed(app, project, tmp_path):
    c = Context(app.store)
    file = tmp_path / "source.txt"
    file.write_text("Shared source")
    a = c.ingest(project["id"], file)
    other = app.store.add_project("Birch")
    b = c.ingest(other["id"], file)
    assert not c.purge(project["id"], a["id"])["active_archive_deleted"]
    assert c.source(other["id"], b["id"])["text"] == "Shared source"
    assert c.purge(other["id"], b["id"])["active_archive_deleted"]


def test_original_prompt_history_survives_fresh_application(app, project):
    from assistant_workbench.app import Application

    app.store.submit("Remember the explicit constraint ZEPHYR", "a", project["id"])
    fresh = Application(app.home)
    assert fresh.dispatch(
        "context.history", {"project": project["id"], "query": "ZEPHYR"}
    )[0]["body"].endswith("ZEPHYR")


def test_profile_change_invalidates_qualification(tmp_path):
    model = {
        "id": "x",
        "harness": "codex",
        "model": "chosen",
        "effort": "high",
        "auth": "subscription",
        "verified": True,
    }
    path = tmp_path / "receipt.json"
    path.write_text(
        json.dumps(
            {
                "result": "passed",
                "created": now(),
                "profile": {
                    k: model[k] for k in ("id", "harness", "model", "effort", "auth")
                },
            }
        )
    )
    model["qualification"] = str(path)
    assert readiness(model)[0]
    model["effort"] = "low"
    assert not readiness(model)[0]


def test_telemetry_is_disabled_before_integration_process_start(app):
    env = Integrations(app.store)._env({"env": {"ORT_DISABLE_TELEMETRY": "0"}})
    assert (
        env["ORT_DISABLE_TELEMETRY"] == "1"
        and env["HF_HUB_DISABLE_TELEMETRY"] == "1"
        and env["OTEL_SDK_DISABLED"] == "true"
    )


def test_explicit_missing_superpowers_is_visible_blocker(app, project, configured):
    app.store.submit(
        "Use Superpowers to brainstorm the architecture", "a", project["id"]
    )
    result = Engine(app.store, FakePaperclip()).tick()
    assert result["status"] == "blocked" and "Superpowers" in result["error"]

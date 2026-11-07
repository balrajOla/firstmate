import concurrent.futures
import json
import pytest
from assistant_workbench.util import WorkbenchError


def test_concurrent_unique_and_retry_receipts(app, project):
    with concurrent.futures.ThreadPoolExecutor(12) as pool:
        receipts = list(
            pool.map(
                lambda i: app.store.submit("Fix Cedar bug", f"key-{i%20}"), range(80)
            )
        )
    assert len({r["request_id"] for r in receipts}) == 20
    assert len(app.store.rows("SELECT * FROM prompts")) == 20
    assert len(app.store.rows("SELECT * FROM outbox")) == 20


def test_retry_conflict_and_intentional_repeat(app, project):
    first = app.store.submit("same text", "a", project["id"])
    second = app.store.submit("same text", "b", project["id"])
    assert first["request_id"] != second["request_id"]
    with pytest.raises(WorkbenchError):
        app.store.submit("different", "a", project["id"])


def test_routing_ambiguity_does_not_block_other_work(app, project):
    other = app.store.add_project("Birch")
    unclear = app.store.submit("Compare Cedar and Birch", "q")
    assert (
        app.dispatch("request.get", {"request_id": unclear["request_id"]})["status"]
        == "needs_project"
    )
    clear = app.store.submit("Fix Cedar", "r")
    assert clear["routing"] == "unique named project"
    app.store.assign(unclear["request_id"], other["id"])
    assert len(app.store.rows("SELECT * FROM outbox")) == 2


def test_revision_cas_and_originals(app, project):
    r = app.store.submit("Write report", "a", project["id"])["request_id"]
    app.store.submit("Include limitations", "b", reply_to=r, expected_revision=1)
    with pytest.raises(WorkbenchError):
        app.store.submit("Concurrent stale edit", "c", reply_to=r, expected_revision=1)
    assert len(app.store.rows("SELECT * FROM prompts")) == 2
    assert app.dispatch("request.get", {"request_id": r})["revision"] == 2


def test_reply_cannot_move_project(app, project):
    other = app.store.add_project("Birch")
    r = app.store.submit("Original", "a", project["id"])["request_id"]
    with pytest.raises(WorkbenchError):
        app.store.submit("Move", "b", other["id"], r, 1)


def test_cancel_before_dispatch_and_reopen_preserves_history(app, project):
    r = app.store.submit("Fix Cedar", "a")["request_id"]
    app.store.cancel(r)
    assert app.dispatch("request.get", {"request_id": r})["status"] == "cancelled"
    reopened = app.store.reopen(r)
    assert reopened["request_id"] != r
    assert len(app.store.rows("SELECT * FROM prompts")) == 2


def test_group_preserves_requests_and_creates_obligations(app, project):
    ids = [
        app.store.submit(text, str(i), project["id"])["request_id"]
        for i, text in enumerate(["Review test results", "Report test results"])
    ]
    grouped = app.dispatch(
        "workflow.group",
        {"requests": ids, "reason": "Same evidence and report audience"},
    )
    assert len(app.store.rows("SELECT * FROM prompts")) == 2
    assert len(app.store.rows("SELECT * FROM obligations")) == 2
    assert len(app.store.rows("SELECT * FROM outbox WHERE status='pending'")) == 1
    assert (
        len(
            app.store.rows(
                "SELECT * FROM request_links WHERE parent=?", (grouped["request_id"],)
            )
        )
        == 2
    )


def test_cross_project_group_rejected(app, project):
    other = app.store.add_project("Birch")
    ids = [
        app.store.submit("Same text", "a", project["id"])["request_id"],
        app.store.submit("Same text", "b", other["id"])["request_id"],
    ]
    with pytest.raises(WorkbenchError):
        app.dispatch("workflow.group", {"requests": ids, "reason": "Same words"})


def test_mail_replay_and_recipient_check(app, project):
    args = dict(
        project=project["id"],
        sender="researcher",
        recipient="reviewer",
        body="Evidence is ready",
        client_key="message-1",
    )
    first = app.store.send(**args)
    second = app.store.send(**args)
    assert first["id"] == second["id"]
    with pytest.raises(WorkbenchError):
        app.store.ack(first["id"], "outsider")
    app.store.ack(first["id"], "reviewer")
    assert app.store.one("SELECT * FROM messages")["acked"]


def test_schedule_coalesces_missed_ticks_without_duplicates(app, project):
    app.dispatch(
        "schedule.add",
        {
            "project": project["id"],
            "prompt": "Check Cedar",
            "interval_seconds": 1800,
            "next_run": 1,
        },
    )
    with concurrent.futures.ThreadPoolExecutor(3) as pool:
        list(pool.map(lambda _: app.scheduled(), range(3)))
    assert len(app.store.rows("SELECT * FROM prompts")) == 1


@pytest.mark.parametrize(
    "text,key", [("", "a"), (" ", "b"), ("hello", ""), ("x" * 1_000_001, "a")]
)
def test_invalid_prompt_is_not_saved(app, text, key):
    with pytest.raises(WorkbenchError):
        app.store.submit(text, key)
    assert not app.store.rows("SELECT * FROM prompts")

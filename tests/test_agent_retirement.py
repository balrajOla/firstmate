"""Retiring Workbench-managed Paperclip agents (pinned contract fixture, not live)."""

import json

import pytest
from assistant_workbench.engine import Engine
from assistant_workbench.util import WorkbenchError
from conftest import PinnedPaperclip


def delivered(app, project, prompt="Fix the login bug", key="a"):
    request = app.store.submit(prompt, key, project["id"])["request_id"]
    api = PinnedPaperclip()
    engine = Engine(app.store, api)
    assert engine.tick()["status"] == "delivered"
    return request, api, engine


def finish_all(api):
    for issue in api.issues_.values():
        issue["status"] = "done"


def test_idle_agents_are_retired_and_a_later_dispatch_creates_fresh_ones(app, project, configured):
    request, api, engine = delivered(app, project)
    finish_all(api)
    old = [a["id"] for a in api.agents_]
    result = engine.retire_agents(idle=True)
    assert sorted(r["id"] for r in result["retired"]) == sorted(old)
    assert sorted(result["forgotten"]) == sorted(old) and not result["skipped"]
    assert all(a["status"] == "terminated" for a in api.agents_)
    assert not app.store.rows("SELECT key FROM meta WHERE key LIKE 'agent:%'")
    events = app.store.rows("SELECT entity FROM events WHERE kind='coordinator.agent_retired'")
    assert sorted(e["entity"] for e in events) == sorted(old)
    # The terminated agents are gone from the list; dispatch replaces them
    # instead of stopping on a cached identity that no longer exists.
    app.store.submit("Fix the logout bug", "b", project["id"])
    assert engine.tick()["status"] == "delivered"
    assert len([a for a in api.agents() if a["status"] == "idle"]) == 2


def test_agents_with_open_tasks_or_a_running_invocation_are_kept(app, project, configured):
    request, api, engine = delivered(app, project)
    implementer, reviewer = api.agents_
    reviewer["status"] = "running"
    result = engine.retire_agents(idle=True)
    assert not result["retired"]
    reasons = {s["id"]: s["reason"] for s in result["skipped"]}
    assert reasons[reviewer["id"]] == "agent is running"
    assert reasons[implementer["id"]].startswith("open tasks: ")
    assert all(a["status"] != "terminated" for a in api.agents_)
    assert len(app.store.rows("SELECT key FROM meta WHERE key LIKE 'agent:%'")) == 2


def test_only_named_agents_are_retired_and_unknown_ones_are_reported(app, project, configured):
    request, api, engine = delivered(app, project)
    finish_all(api)
    first, second = api.agents_
    result = engine.retire_agents(agent_ids=[first["id"], "agent-unknown"])
    assert [r["id"] for r in result["retired"]] == [first["id"]]
    assert result["skipped"] == [
        {"id": "agent-unknown", "reason": "not an active Workbench-managed agent"}]
    assert second["status"] == "idle"
    cached = [json.loads(r["value"])["id"] for r in
              app.store.rows("SELECT value FROM meta WHERE key LIKE 'agent:%'")]
    assert cached == [second["id"]]


def test_retirement_needs_an_explicit_scope(app, project, configured):
    request, api, engine = delivered(app, project)
    with pytest.raises(WorkbenchError, match="agent_ids"):
        engine.retire_agents()
    assert all(a["status"] == "idle" for a in api.agents_)


def test_uncertain_creation_stays_for_inspection(app, project, configured):
    request, api, engine = delivered(app, project)
    finish_all(api)
    with app.store.transaction() as db:
        db.execute("INSERT INTO meta(key, value) VALUES (?, ?)",
                   ("agent:awb:uncertain", json.dumps({"state": "creating"})))
    engine.retire_agents(idle=True)
    keys = [r["key"] for r in app.store.rows("SELECT key FROM meta WHERE key LIKE 'agent:%'")]
    assert keys == ["agent:awb:uncertain"]


def test_unconfirmed_termination_is_uncertain(app, project, configured):
    from assistant_workbench.paperclip import CoordinatorError

    request, api, engine = delivered(app, project)
    finish_all(api)
    api.terminate_agent = lambda identity: {"id": identity, "status": "idle"}
    with pytest.raises(CoordinatorError) as caught:
        engine.retire_agents(agent_ids=[api.agents_[0]["id"]])
    assert caught.value.uncertain
    assert len(app.store.rows("SELECT key FROM meta WHERE key LIKE 'agent:%'")) == 2

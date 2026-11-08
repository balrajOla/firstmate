"""No coordinator mutation before local execution admission has succeeded.

FakePaperclip checks the bridge contract; these are not live-agent evaluations.
"""

import copy
import json

import pytest

import assistant_workbench.engine as engine_module
from assistant_workbench.engine import Engine
from assistant_workbench.policy import compile_plan
from assistant_workbench.quality import Outcomes
from assistant_workbench.util import WorkbenchError, canonical, now
from conftest import FakePaperclip


def saved_plan(app, project, configured, text="Fix the website"):
    ident = app.store.submit(text, "prompt", project["id"])["request_id"]
    request = app.dispatch("request.get", {"request_id": ident})
    plan = compile_plan(request, configured)
    app.dispatch("workflow.save_plan", {"request_id": ident, "revision": 1, "plan": plan})
    return ident, plan


def persist_plan(app, ident, plan):
    with app.store.transaction() as db:
        db.execute("UPDATE requests SET plan=? WHERE id=?", (canonical(plan), ident))


def assert_no_remote_mutations(api):
    assert api.projects_ == {}
    assert api.agents_ == []
    assert api.issues_ == {}


def test_missing_qualification_is_rejected_before_creating_remote_project(app, project):
    app.store.submit("Fix the website", "prompt", project["id"])
    api = FakePaperclip()
    result = Engine(app.store, api).tick()
    assert result["status"] == "blocked"
    assert_no_remote_mutations(api)
    assert app.store.project(project["id"])["remote_id"] is None


def test_every_node_packet_is_checked_before_any_remote_mutation(app, project, configured):
    ident, plan = saved_plan(app, project, configured)
    plan["nodes"][-1]["instructions"] = "x" * (configured["limits"]["context_chars"] + 1)
    persist_plan(app, ident, plan)
    api = FakePaperclip()
    result = Engine(app.store, api).tick()
    assert result["status"] == "blocked"
    assert "budget" in result["error"]
    assert_no_remote_mutations(api)


@pytest.mark.parametrize("corruption", ["cycle", "missing_dependency", "wrong_revision", "missing_role", "budget_escalation", "skill_hash"])
def test_saved_plan_is_validated_again_before_remote_mutation(app, project, configured, corruption):
    ident, plan = saved_plan(app, project, configured)
    if corruption == "cycle":
        plan["nodes"][0]["depends_on"] = [plan["nodes"][-1]["id"]]
    elif corruption == "missing_dependency":
        plan["nodes"][0]["depends_on"] = ["missing"]
    elif corruption == "wrong_revision":
        plan["revision"] = 2
    elif corruption == "missing_role":
        del plan["nodes"][0]["role"]
    elif corruption == "budget_escalation":
        plan["limits"]["max_iterations"] += 1000
    else:
        plan["skills"][0]["text"] += " changed after pinning"
    persist_plan(app, ident, plan)
    api = FakePaperclip()
    result = Engine(app.store, api).tick()
    assert result["status"] == "blocked"
    assert_no_remote_mutations(api)


def test_saved_profile_qualification_is_rechecked_when_receipt_expires(app, project, configured):
    profile = configured["models"][0]
    profile.update(harness="codex", model="fixture-model", auth="subscription")
    receipt = app.home / "fixture-qualification.json"
    qualification = {
        "result": "passed", "created": now(),
        "profile": {key: profile[key] for key in ("id", "harness", "model", "effort", "auth")},
    }
    receipt.write_text(json.dumps(qualification))
    profile["qualification"] = str(receipt)
    (app.home / "config.json").write_text(json.dumps(configured))
    saved_plan(app, project, configured)
    qualification["created"] -= 8 * 86400
    receipt.write_text(json.dumps(qualification))
    api = FakePaperclip()
    result = Engine(app.store, api).tick()
    assert result["status"] == "blocked"
    assert_no_remote_mutations(api)


def test_saved_acyclic_nodes_are_dispatched_in_dependency_order(app, project, configured):
    ident, plan = saved_plan(app, project, configured, "Research the website")
    plan["nodes"] = list(reversed(plan["nodes"]))
    app.dispatch("workflow.save_plan", {"request_id": ident, "revision": 1, "plan": plan})
    api = FakePaperclip()
    assert Engine(app.store, api).tick()["status"] == "delivered"
    created = set()
    for issue in api.issues_.values():
        assert set(issue["blockedByIssueIds"]) <= created
        created.add(issue["id"])
    assert len(created) == 4


def test_accepted_request_is_not_dispatched_by_an_outstanding_intake_job(app, project, configured):
    ident = app.store.submit("Say hello", "prompt", project["id"])["request_id"]
    outcomes = Outcomes(app.store)
    outcome = outcomes.propose(ident, "Hello.", [], "writer", 1)
    outcomes.review(outcome["id"], "reviewer", "accept", "Checked the requested greeting")
    api = FakePaperclip()
    assert Engine(app.store, api).tick()["status"] == "superseded"
    assert app.dispatch("request.get", {"request_id": ident})["status"] == "accepted"
    assert_no_remote_mutations(api)


def test_amendment_during_preflight_prevents_remote_mutations(app, project, configured, monkeypatch):
    ident = app.store.submit("Fix the website", "prompt", project["id"])["request_id"]
    original = engine_module.compile_plan

    def compile_and_amend(*args, **kwargs):
        plan = original(*args, **kwargs)
        app.store.submit("Add keyboard support", "amendment", reply_to=ident, expected_revision=1)
        return plan

    monkeypatch.setattr(engine_module, "compile_plan", compile_and_amend)
    api = FakePaperclip()
    assert Engine(app.store, api).tick()["status"] == "superseded"
    assert_no_remote_mutations(api)


def test_existing_bridge_cannot_dispatch_after_temporal_is_selected(app, project, configured, monkeypatch):
    app.store.submit("Say hello", "prompt", project["id"])
    api = FakePaperclip()
    engine = Engine(app.store, api)
    temporal = copy.deepcopy(configured)
    temporal["coordinator"]["kind"] = "temporal"
    monkeypatch.setattr(engine_module, "config", lambda _: temporal)
    with pytest.raises(WorkbenchError, match="paperclip is inactive"):
        engine.tick()
    assert_no_remote_mutations(api)
    assert app.store.one("SELECT * FROM outbox")["status"] == "pending"


def test_direct_delivery_also_checks_the_runtime_fence(app, project, configured, monkeypatch):
    app.store.submit("Say hello", "prompt", project["id"])
    api = FakePaperclip()
    engine = Engine(app.store, api)
    job = engine.claim()
    temporal = copy.deepcopy(configured)
    temporal["coordinator"]["kind"] = "temporal"
    monkeypatch.setattr(engine_module, "config", lambda _: temporal)
    with pytest.raises(WorkbenchError, match="paperclip is inactive"):
        engine.deliver(job)
    assert_no_remote_mutations(api)


def test_stale_cancel_job_does_not_cancel_current_issues(app, project, configured):
    ident = app.store.submit("Say hello", "prompt", project["id"])["request_id"]
    api = FakePaperclip()
    engine = Engine(app.store, api)
    engine.tick()
    result = engine.deliver({"operation": "cancel", "request_id": ident, "revision": 0})
    assert result == "superseded"
    assert all(issue["status"] == "todo" for issue in api.issues_.values())


def test_plan_can_narrow_enforced_runtime_timeout_without_reusing_broader_agent(app, project, configured):
    first = app.store.submit("Say hello", "first", project["id"])["request_id"]
    api = FakePaperclip()
    engine = Engine(app.store, api)
    assert engine.tick()["status"] == "delivered"
    ident, plan = saved_plan(app, project, configured, "Say goodbye")
    plan["limits"]["max_seconds"] = 30
    app.dispatch("workflow.save_plan", {"request_id": ident, "revision": 1, "plan": plan})
    assert engine.tick()["status"] == "delivered"
    issue = next(i for i in api.issues_.values() if ident in i["idempotencyKey"])
    prior = next(i for i in api.issues_.values() if first in i["idempotencyKey"])
    assert issue["assigneeAgentId"] != prior["assigneeAgentId"]
    agent = next(a for a in api.agents_ if a["id"] == issue["assigneeAgentId"])
    assert agent["adapterConfig"]["timeoutSec"] == 30


def test_scope_change_while_reading_agent_pool_prevents_agent_creation(app, project, configured):
    ident = app.store.submit("Say hello", "prompt", project["id"])["request_id"]
    api = FakePaperclip()

    def read_and_cancel():
        app.store.cancel(ident)
        return []

    api.agents = read_and_cancel
    assert Engine(app.store, api).tick()["status"] == "superseded"
    assert api.agents_ == [] and api.issues_ == {}

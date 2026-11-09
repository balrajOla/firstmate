"""Opt-in semantic routing with a scripted CLI; not evidence of model quality."""

import json

import pytest
from assistant_workbench import routing
from assistant_workbench.engine import Engine
from assistant_workbench.util import now
from conftest import FakePaperclip


class ScriptedCLI:
    """Answers like `claude -p --output-format json` with a chosen reply."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, argv, env=None, timeout=None, cwd=None):
        self.calls.append({"argv": argv, "env": env})
        answer = self.answers.pop(0)
        body = answer if isinstance(answer, str) else json.dumps(answer)
        return {"stdout": json.dumps({"type": "result", "is_error": False, "result": body,
                                      "modelUsage": {"claude-sonnet-5-5": {}}})}


@pytest.fixture
def semantic(app, configured, monkeypatch, tmp_path):
    """A qualified Claude routing profile alongside the fixture worker."""
    receipt = tmp_path / "probe.json"
    profile = {"id": "router", "harness": "claude", "model": "claude-sonnet-5-5", "effort": "low",
               "efforts": ["low"], "auth": "subscription", "classes": ["simple"], "verified": True,
               "priority": 50}
    receipt.write_text(json.dumps({"result": "passed", "created": now(), "claude_sign_in": "account", "profile": {
        k: profile[k] for k in ("id", "harness", "model", "effort", "auth")}}))
    profile["qualification"] = str(receipt)
    configured["models"].append(profile)
    configured["routing"] = {"semantic": {"profile": "router", "min_confidence": 0.7}}
    (app.home / "config.json").write_text(json.dumps(configured))
    monkeypatch.setattr(routing.shutil, "which", lambda name: "/usr/local/bin/claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-pass")
    return configured


def answer(project=None, workflow="code", confidence=0.9, explanation="It asks for a code change."):
    return {"project": project, "workflow": workflow, "confidence": confidence, "explanation": explanation}


def test_off_by_default_keeps_the_lexical_policy(app, project, configured):
    request = app.store.submit("Report the architecture review", "a", project["id"])["request_id"]
    assert app.dispatch("routing.pending") == {"routed": [], "notice": "Semantic routing is not configured"}
    req = app.store.one("SELECT * FROM requests WHERE id=?", (request,))
    assert routing.workflow_choice(app.store, configured, req) is None


def test_confident_decision_routes_an_unresolved_request(app, project, semantic):
    app.store.add_project("Harbor", None, ["harbor-site"])
    app.store.submit("Add a contact form to the cedar app", "seed", project["id"])
    pending = app.store.submit("The signup page needs a password strength meter", "b")["request_id"]
    assert app.store.one("SELECT status FROM requests WHERE id=?", (pending,))["status"] == "needs_project"
    cli = ScriptedCLI(answer(project=project["id"], explanation="Cedar owns the signup flow."))
    result = routing.route_pending(app.store, semantic, runner=cli)
    assert result["routed"] == [{"request_id": pending, "assigned": True, "project_id": project["id"], "error": None}]
    row = app.store.one("SELECT status, project_id FROM requests WHERE id=?", (pending,))
    assert row["status"] == "queued" and row["project_id"] == project["id"]
    # The model saw the projects, their aliases, recent requests and the prompt,
    # with tools disabled, one turn and no provider API key.
    argv = cli.calls[0]["argv"]
    prompt = argv[argv.index("-p") + 1]
    for text in ("Harbor", "harbor-site", "Cedar", "cedar-app", "Add a contact form to the cedar app",
                 "password strength meter"):
        assert text in prompt
    assert argv[argv.index("--tools") + 1] == "" and argv[argv.index("--max-turns") + 1] == "1"
    # The router signs in like the profile's workers, without personal settings.
    assert argv[argv.index("--setting-sources") + 1] == "" and "--strict-mcp-config" in argv
    assert "ANTHROPIC_API_KEY" not in cli.calls[0]["env"]
    decided = app.store.rows("SELECT payload FROM events WHERE kind='request.route_decided'")
    payload = json.loads(decided[0]["payload"])
    assert payload["explanation"] == "Cedar owns the signup flow."
    assert payload["effective_models"] == ["claude-sonnet-5-5"] and payload["latency_seconds"] >= 0


def test_low_confidence_leaves_the_choice_to_the_operator_once(app, project, semantic):
    pending = app.store.submit("Tidy things up", "b")["request_id"]
    cli = ScriptedCLI(answer(project=project["id"], confidence=0.4))
    routing.route_pending(app.store, semantic, runner=cli)
    assert app.store.one("SELECT status FROM requests WHERE id=?", (pending,))["status"] == "needs_project"
    # The recorded suggestion is not asked for again on the next pass.
    assert routing.route_pending(app.store, semantic, runner=cli) == {"routed": []}
    assert len(cli.calls) == 1


@pytest.mark.parametrize("reply,error", [
    ("not json", "not a JSON object"),
    (answer(project="p_unknown"), "unknown project"),
    (answer(workflow="poetry"), "unknown workflow"),
    (answer(confidence=1.5), "confidence"),
    (answer(explanation=""), "explanation"),
], ids=["prose", "unknown-project", "unknown-workflow", "confidence", "no-explanation"])
def test_invalid_answers_never_route(app, project, semantic, reply, error):
    pending = app.store.submit("Tidy things up", "b")["request_id"]
    result = routing.route_pending(app.store, semantic, runner=ScriptedCLI(reply))
    assert error in result["routed"][0]["error"] and not result["routed"][0]["assigned"]
    assert app.store.one("SELECT status FROM requests WHERE id=?", (pending,))["status"] == "needs_project"


def test_semantic_workflow_chooses_the_team_once_per_revision(app, project, semantic, monkeypatch):
    request = app.store.submit("Change the VERSION constant in vendor/lib.py from 1.0 to 2.0, "
                               "and report if the file is missing", "a", project["id"])["request_id"]
    cli = ScriptedCLI(answer(project=project["id"], workflow="code"))
    monkeypatch.setattr(routing, "run_process", cli)
    engine = Engine(app.store, FakePaperclip())
    assert engine.tick()["status"] == "delivered"
    plan = json.loads(app.store.one("SELECT plan FROM requests WHERE id=?", (request,))["plan"])
    assert plan["workflow"] == "code" and [n["id"] for n in plan["nodes"]] == ["implement", "verify"]
    assert plan["routing"]["method"] == "semantic policy (official CLI)"
    # The operator's project stands, so the model is asked only for the workflow.
    argv = cli.calls[0]["argv"]
    prompt = argv[argv.index("-p") + 1]
    assert "already assigned to project " + project["id"] in prompt
    assert "Use null when no project clearly fits" not in prompt
    # The lexical policy would have chosen a report from the trailing word.
    assert plan["routing"]["lexical"]["workflow"] == "report"
    with app.store.transaction() as db:
        db.execute("UPDATE outbox SET status='retry' WHERE request_id=?", (request,))
        db.execute("UPDATE requests SET plan=NULL WHERE id=?", (request,))
    engine.tick()
    assert len(cli.calls) == 1


def test_an_assigned_project_is_kept_whatever_the_model_answers(app, project, semantic):
    other = app.store.add_project("Harbor", None, ["harbor-site"])
    request = app.store.submit("Add a contact form", "a", project["id"])["request_id"]
    req = app.store.one("SELECT * FROM requests WHERE id=?", (request,))
    decision = routing.decide(app.store, semantic, req, runner=ScriptedCLI(answer(project=other["id"])))
    assert decision["project_id"] == project["id"] and decision["workflow"] == "code"


def test_failed_or_unconfident_routing_falls_back_to_the_lexical_workflow(app, project, semantic, monkeypatch):
    request = app.store.submit("Report the architecture review", "a", project["id"])["request_id"]
    monkeypatch.setattr(routing, "run_process", ScriptedCLI("I think it is a report."))
    assert Engine(app.store, FakePaperclip()).tick()["status"] == "delivered"
    plan = json.loads(app.store.one("SELECT plan FROM requests WHERE id=?", (request,))["plan"])
    assert plan["workflow"] == "report" and plan["routing"]["method"] == "lexical policy"


def test_operator_can_ask_again_after_an_amendment_or_explicitly(app, project, semantic, monkeypatch):
    pending = app.store.submit("Tidy things up", "b")["request_id"]
    cli = ScriptedCLI(answer(project=project["id"], confidence=0.3),
                      answer(project=project["id"], confidence=0.95))
    monkeypatch.setattr(routing, "run_process", cli)
    first = app.dispatch("request.route", {"request_id": pending})
    assert not first["confident"]
    assert app.dispatch("request.route", {"request_id": pending}) == first
    second = app.dispatch("request.route", {"request_id": pending, "force": True})
    assert second["assigned"] and len(cli.calls) == 2


def test_unqualified_profile_is_never_called(app, project, semantic):
    semantic["models"][-1]["qualification"] = None
    (app.home / "config.json").write_text(json.dumps(semantic))
    app.store.submit("Tidy things up", "b")
    cli = ScriptedCLI()
    result = routing.route_pending(app.store, json.loads((app.home / "config.json").read_text()), runner=cli)
    assert "not qualified" in result["routed"][0]["error"] and not cli.calls


def test_settings_are_validated(semantic):
    with pytest.raises(Exception, match="min_confidence"):
        routing.semantic_settings({"routing": {"semantic": {"profile": "router", "min_confidence": 2}}})
    with pytest.raises(Exception, match="profile"):
        routing.semantic_settings({"routing": {"semantic": {}}})


def test_held_out_evaluation_records_misses_beside_the_lexical_policy(app, semantic):
    from assistant_workbench.evaluation import semantic_routing

    suite = {
        "projects": [{"key": "web", "name": "Bakery site", "aliases": ["bakery"],
                      "history": ["Fix the carousel"]}],
        "cases": [
            {"id": "a", "prompt": "Report the architecture review", "workflow": "report", "project": None},
            {"id": "b", "prompt": "The bakery checkout fails", "workflow": "code", "project": "web"},
            {"id": "c", "prompt": "Tidy up", "workflow": "simple", "project": None},
        ],
    }

    class SuiteCLI(ScriptedCLI):
        def __call__(self, argv, env=None, timeout=None, cwd=None):
            prompt = argv[argv.index("-p") + 1]
            web = prompt.split("- id: ")[1].split(" |")[0]
            request = prompt.split("<<<")[1]
            self.answers = [answer(project=web if "checkout" in request else None,
                                   workflow="report" if "architecture" in request else "code",
                                   confidence=0.5 if "Tidy" in request else 0.9)]
            return super().__call__(argv, env, timeout, cwd)

    projects_before = app.store.rows("SELECT id FROM projects")
    result = semantic_routing(semantic, suite, runner=SuiteCLI())
    assert app.store.rows("SELECT id FROM projects") == projects_before
    assert result["total"] == 3 and result["errors"] == 0
    cases = {c["id"]: c for c in result["cases"]}
    assert cases["a"]["miss"] == [] and cases["b"]["project"] == "web" and cases["b"]["miss"] == []
    assert cases["c"]["miss"] == ["workflow", "unconfident"]
    assert result["semantic_workflow_accuracy"] == round(2 / 3, 3)
    assert cases["b"]["lexical_project"] == "web" and cases["a"]["lexical_workflow"] == "report"
    assert "these held-out cases only" in result["kind"]


def test_plan_preview_shows_the_team_delivery_would_choose(app, project, semantic, monkeypatch):
    request = app.store.submit("Change the VERSION constant and report if missing", "a",
                               project["id"])["request_id"]
    cli = ScriptedCLI(answer(project=project["id"], workflow="code"))
    monkeypatch.setattr(routing, "run_process", cli)
    preview = app.dispatch("request.plan", {"request_id": request})
    assert preview["workflow"] == "code" and preview["routing"]["method"].startswith("semantic")
    assert app.dispatch("request.plan", {"request_id": request, "kind": "report"})["workflow"] == "report"
    assert Engine(app.store, FakePaperclip()).tick()["status"] == "delivered"
    assert len(cli.calls) == 1

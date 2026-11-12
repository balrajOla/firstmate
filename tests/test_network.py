"""Connectivity guard and automatic resumption with a scripted network and
coordinator. A real outage and reboot are covered by the live notes."""

import json

import pytest
from assistant_workbench import engine as engine_module
from assistant_workbench.config import config
from assistant_workbench.engine import Engine, resumable
from assistant_workbench.network import Guard, network_settings
from assistant_workbench.util import WorkbenchError
from conftest import PinnedPaperclip
from test_paperclip_contract import official_cli_profile, remote_issues


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Paperclip(PinnedPaperclip):
    """Adds the resume and retry calls; it has no pause, so pausing would fail."""

    def __init__(self):
        super().__init__()
        self.paused, self.resumed, self.retries = [], [], []  # paused stays empty

    def resume_agent(self, identity):
        self.resumed.append(identity)
        next(a for a in self.agents_ if a["id"] == identity)["status"] = "idle"
        return {}

    def retry_issue(self, identity, key, observed_run):
        self.retries.append((identity, key, observed_run))
        return {"accepted": True, "requestId": "retry-" + str(len(self.retries))}


def delivered(app, project, configured):
    official_cli_profile(app, configured)
    request = app.store.submit("Fix the login bug", "a", project["id"])["request_id"]
    api = Paperclip()
    engine = Engine(app.store, api)
    assert engine.tick()["status"] == "delivered"
    issues = remote_issues(app, request)
    return request, api, engine, issues["1:implement"], issues["1:verify"]


@pytest.fixture
def online(app, configured):
    configured["network"] = {"probe_hosts": ["provider.example:443"]}
    (app.home / "config.json").write_text(json.dumps(configured))
    return configured


def test_offline_needs_two_failed_probes_and_one_success_ends_it(app, online):
    answers, clock = [False, False, False, True], Clock()
    guard = Guard(app.store, config(app.home), check=lambda hosts: answers.pop(0), clock=clock)
    assert guard.update()["status"] == "online"  # one failure may be a blip
    assert guard.update()["status"] == "online"  # not probed again within the interval
    clock.t += 10
    assert guard.update()["status"] == "offline"
    clock.t += 10
    assert guard.update()["status"] == "offline"
    clock.t += 10
    assert guard.update()["status"] == "online"
    kinds = [r["kind"] for r in app.store.rows("SELECT kind FROM events WHERE kind LIKE 'network.%' ORDER BY seq")]
    assert kinds == ["network.offline", "network.online"]
    from assistant_workbench.network import outages

    [(start, end)] = outages(app.store)
    assert start < end


def test_probe_hosts_follow_the_configured_harnesses(app, configured):
    configured["models"].append({"harness": "claude"})
    configured["models"].append({"harness": "codex"})
    assert network_settings(configured)["hosts"] == ["api.anthropic.com:443", "api.openai.com:443", "chatgpt.com:443"]
    # A gateway machine names its own; the fixture worker alone needs none.
    assert network_settings({**configured, "network": {"probe_hosts": ["gateway.example:8443"]}})["hosts"] == [
        "gateway.example:8443"]
    assert network_settings({"models": []})["hosts"] == []


@pytest.mark.parametrize("spec, error", [
    ({"enabled": "yes"}, "enabled"), ({"probe_hosts": ["no-port"]}, "probe_hosts"),
    ({"interval_seconds": 1}, "interval_seconds"), ({"other": 1}, "network accepts"),
], ids=["enabled", "hosts", "interval", "unknown"])
def test_network_settings_are_validated(app, configured, spec, error):
    configured["network"] = spec
    (app.home / "config.json").write_text(json.dumps(configured))
    with pytest.raises(WorkbenchError, match=error):
        config(app.home)


def test_a_disabled_guard_or_no_hosts_never_reports_offline(app, configured):
    configured["network"] = {"enabled": False, "probe_hosts": ["provider.example:443"]}
    guard = Guard(app.store, configured, check=lambda hosts: pytest.fail("probed"))
    assert guard.update()["status"] == "online"


def test_an_outage_leaves_workers_alone_and_retries_runs_that_failed_during_it(
        app, project, configured, online, monkeypatch):
    request, api, engine, implement, verify = delivered(app, project, configured)
    monkeypatch.setattr(type(app), "_coordinator", lambda self: engine)
    answers, clock = [False, False], Clock()
    monkeypatch.setattr("assistant_workbench.network.reachable", lambda hosts, timeout=3: answers.pop(0))
    monkeypatch.setattr("assistant_workbench.network.time.time", clock)
    app.dispatch("network.check")
    clock.t += 10
    assert app.dispatch("network.check")["status"] == "offline"
    # No agent is paused: Paperclip would block their waiting tasks as stranded.
    assert api.paused == [] and {a["status"] for a in api.agents_} == {"idle"}
    # The worker's run fails with a message that names no network error.
    stopped(api, implement, "adapter_failed", "Process exited with code 1", finished=clock.t + 5)
    engine.observe()
    assert api.retries == []  # never while offline
    clock.t += 30
    answers.append(True)
    assert app.dispatch("network.check")["status"] == "online"
    engine.observe()
    assert [issue for issue, _, _ in api.retries] == [implement]
    # The same message outside an outage waits for the operator.
    stopped(api, verify, "adapter_failed", "Process exited with code 1", finished=clock.t + 3600)
    engine.observe()
    assert [issue for issue, _, _ in api.retries] == [implement]


def test_agents_paused_by_an_earlier_version_are_released(app, project, configured, online, monkeypatch):
    request, api, engine, implement, verify = delivered(app, project, configured)
    monkeypatch.setattr(type(app), "_coordinator", lambda self: engine)
    agent = api.agents_[0]
    agent["status"] = "paused"
    with app.store.transaction() as db:
        db.execute("INSERT OR REPLACE INTO meta VALUES('network', ?)",
                   (json.dumps({"status": "online", "since": 1.0, "failures": 0, "paused": True}),))
        db.execute("INSERT OR REPLACE INTO meta VALUES('network-paused', ?)",
                   (json.dumps({"agents": [agent["id"]], "issues": {}}),))
    monkeypatch.setattr("assistant_workbench.network.reachable", lambda hosts, timeout=3: True)
    state = app.dispatch("network.check")
    assert state["paused"] is False and api.resumed == [agent["id"]]
    assert not app.store.rows("SELECT 1 FROM meta WHERE key='network-paused'")


def stopped(api, issue, code, reason, agent_error=None, label="0", finished=None):
    """Paperclip failed the task's run and blocked it; no worker reported."""
    from datetime import datetime, timezone

    agent = api.issue(issue)["assigneeAgentId"]
    next(a for a in api.agents_ if a["id"] == agent)["errorReason"] = agent_error
    run_id = issue + "-failed-" + label
    api.runs[run_id] = {"runId": run_id, "issueId": issue, "agentId": agent, "status": "failed",
                        "createdAt": "2026-11-09T10:00:0" + label, "errorCode": code, "livenessReason": reason,
                        "finishedAt": datetime.fromtimestamp(finished, timezone.utc).isoformat() if finished else None}
    api.issue(issue)["status"] = "blocked"
    return run_id


def test_a_task_lost_to_a_restart_is_retried_once_per_run_with_growing_delays(app, project, configured, monkeypatch):
    request, api, engine, implement, verify = delivered(app, project, configured)
    clock = Clock()
    monkeypatch.setattr(engine_module.time, "time", clock)
    first = stopped(api, implement, "process_lost", "Process lost during server restart")
    engine.observe()
    engine.observe()
    assert [observed for _, _, observed in api.retries] == [first]
    # The retry also failed; the next attempt waits two minutes.
    second = stopped(api, implement, "process_lost", "Process lost during server restart", label="1")
    engine.observe()
    assert len(api.retries) == 1
    clock.t += 121
    engine.observe()
    assert [observed for _, _, observed in api.retries] == [first, second]
    third = stopped(api, implement, "process_lost", "lost", label="2")
    clock.t += 601
    engine.observe()
    fourth = stopped(api, implement, "process_lost", "lost", label="3")
    clock.t += 3600
    engine.observe()
    engine.observe()
    assert [observed for _, _, observed in api.retries] == [first, second, third]
    exhausted = app.store.rows("SELECT payload FROM events WHERE kind='execution.resume_exhausted'")
    assert len(exhausted) == 1 and json.loads(exhausted[0]["payload"])["attempts"] == 3
    # Retries replay safely: each run has its own idempotency key.
    assert len({key for _, key, _ in api.retries}) == 3
    # The task still asks for the operator.
    assert app.store.snapshot()["attention"][0]["status"] == "execution_failed"
    assert fourth


@pytest.mark.parametrize("code, reason, agent_error, expected", [
    ("process_lost", "Process lost", None, True),
    ("server_shutdown_interrupted", "Run interrupted (server_shutdown_interrupted)", None, True),
    ("orphaned_running_run", "process and sandbox gone while heartbeat_runs.status stayed live", None, True),
    ("claude_transient_upstream", "overloaded", None, True),
    ("adapter_failed", "API Error: Connection error (getaddrinfo ENOTFOUND api.anthropic.com)", None, True),
    ("adapter_failed", "fetch failed: ECONNRESET", None, True),
    ("adapter_failed", "Process exited with code 1", None, False),
    ("configuration_incomplete", "Run ended with failed", "no Codex credentials available", False),
    ("process_lost", "Process lost", "Claude CLI is installed, but login is required", False),
    ("adapter_failed", "Invalid API key · Please run /login", None, False),
], ids=["restart", "shutdown", "orphaned", "transient", "dns", "reset", "plain-failure", "no-credentials", "signed-out", "bad-key"])
def test_only_recoverable_failures_are_retried(code, reason, agent_error, expected):
    assert resumable({"run": "r", "code": code, "reason": reason, "agent_error": agent_error}) is expected


def test_nothing_is_retried_while_offline_or_for_a_sign_in_problem(app, project, configured, monkeypatch):
    request, api, engine, implement, verify = delivered(app, project, configured)
    with app.store.transaction() as db:
        db.execute("INSERT OR REPLACE INTO meta VALUES('network', ?)", (json.dumps({"status": "offline"}),))
    stopped(api, implement, "adapter_failed", "getaddrinfo ENOTFOUND api.anthropic.com")
    engine.observe()
    assert api.retries == []
    with app.store.transaction() as db:
        db.execute("INSERT OR REPLACE INTO meta VALUES('network', ?)", (json.dumps({"status": "online"}),))
    engine.observe()
    assert len(api.retries) == 1
    stopped(api, verify, "process_lost", "lost", agent_error="Not logged in. Please run /login")
    engine.observe()
    assert [issue for issue, _, _ in api.retries] == [implement]


def test_a_quota_failure_waits_much_longer(app, project, configured, monkeypatch):
    request, api, engine, implement, verify = delivered(app, project, configured)
    clock = Clock()
    monkeypatch.setattr(engine_module.time, "time", clock)
    stopped(api, implement, "provider_quota", "usage limit reached")
    engine.observe()
    assert api.retries == []  # the first quota retry waits 15 minutes
    clock.t += 901
    engine.observe()
    assert len(api.retries) == 1


def test_the_bridge_saves_prompts_but_dispatches_nothing_while_offline(app, project, monkeypatch):
    import threading
    from types import SimpleNamespace
    import assistant_workbench.server as server_module
    from test_server_workers import await_state, running_bridge

    calls = []
    original = server_module.Application.dispatch

    def dispatch(self, action, args=None):
        calls.append(action)
        if action == "network.check":
            return {"status": "offline", "since": 1.0}
        if action == "coordinator.observe":
            return {}
        return original(self, action, args or {})

    class ShortWaitEvent:
        def __init__(self):
            self.event = threading.Event()

        def is_set(self):
            return self.event.is_set()

        def set(self):
            self.event.set()

        def wait(self, timeout=None):
            return self.event.wait(min(timeout or .05, .05))

    monkeypatch.setattr(server_module.Application, "dispatch", dispatch)
    monkeypatch.setattr(server_module, "threading", SimpleNamespace(
        Thread=threading.Thread, BoundedSemaphore=threading.BoundedSemaphore, Event=ShortWaitEvent))
    with running_bridge(app, background=True):
        health = await_state(app.home / "bridge-health.json", "offline")
        assert "resumes automatically" in health["reason"]
        app.store.submit("Saved while offline", "off", project["id"])
        await_state(app.home / "network-health.json", "offline")
    assert "network.check" in calls
    assert "coordinator.tick" not in calls and "routing.pending" not in calls
    assert app.store.one("SELECT status FROM requests WHERE body='Saved while offline'")["status"] == "queued"


class Commented(Paperclip):
    """Paperclip posts a worker's final message as a task comment."""

    def __init__(self):
        super().__init__()
        self.comments = {}

    def issue_comments(self, identity):
        return list(self.comments.get(identity, []))


def ended(api, issue, status, code, created="2026-11-12T18:24:42Z"):
    """The worker closed its task, then its run ended without succeeding."""
    agent = api.issue(issue)["assigneeAgentId"]
    run_id = issue + "-" + status
    api.runs[run_id] = {"runId": run_id, "issueId": issue, "agentId": agent, "status": status,
                        "errorCode": code, "createdAt": created}
    api.issue(issue)["status"] = "done"
    return agent


def test_a_task_done_before_a_restart_cut_its_report_is_not_left_waiting(app, project, configured):
    # Observed live: a restart interrupted the engineer's run four seconds
    # after it closed its task; the reviewer's run was marked failed after it
    # closed its task, and Paperclip posted its final message as a comment.
    official_cli_profile(app, configured)
    request = app.store.submit("Add farewell(name)", "r", project["id"])["request_id"]
    api = Commented()
    engine = Engine(app.store, api)
    engine.tick()
    issues = remote_issues(app, request)
    implement, verify = issues["1:implement"], issues["1:verify"]
    ended(api, implement, "interrupted", "server_shutdown_interrupted")
    reviewer = ended(api, verify, "failed", "adapter_failed", created="2026-11-12T18:25:19Z")
    api.comments[verify] = [
        {"authorAgentId": reviewer, "createdAt": "2026-11-12T18:25:19Z", "body": "## Workspace Ready\n\n- Branch: x"},
        {"authorAgentId": reviewer, "createdAt": "2026-11-12T18:25:40Z",
         "body": "farewell(name) and its test look right; the test passed."},
        {"authorAgentId": "someone-else", "createdAt": "2026-11-12T18:26:00Z", "body": "Not the worker."},
    ]
    observed = engine.observe()[request]
    assert observed["implement"]["report"] == "captured" and observed["verify"]["report"] == "captured"
    reports = {n: json.loads(app.store.one("SELECT value FROM meta WHERE key=?", ("report:" + request + ":1:" + n,))["value"])
               for n in ("implement", "verify")}
    assert reports["implement"]["recovered"] == "none"
    assert "run ended interrupted (server_shutdown_interrupted) before it reported" in reports["implement"]["text"]
    assert "awb/" + request + "-r1-implement" in reports["implement"]["text"]
    assert reports["verify"]["recovered"] == "comment"
    assert reports["verify"]["text"].startswith("farewell(name) and its test look right")
    assert "Workspace Ready" not in reports["verify"]["text"] and "Not the worker" not in reports["verify"]["text"]
    # The request reaches review instead of staying "running" with nothing to do.
    assert app.dispatch("request.get", {"request_id": request})["status"] == "needs_review"


def test_a_done_task_whose_worker_is_still_writing_its_report_waits(app, project, configured):
    official_cli_profile(app, configured)
    request = app.store.submit("Add farewell(name)", "w", project["id"])["request_id"]
    api = Commented()
    engine = Engine(app.store, api)
    engine.tick()
    implement = remote_issues(app, request)["1:implement"]
    ended(api, implement, "running", None)
    assert engine.observe()[request]["implement"]["report"] == "pending"


def orphaned(api, issue, finished, label="0", status="interrupted", code="orphaned_running_run"):
    """A restart orphaned the task's run; Paperclip ended the run and left the
    task in progress with nothing queued."""
    from datetime import datetime, timezone

    agent = api.issue(issue)["assigneeAgentId"]
    run_id = issue + "-orphaned-" + label
    api.runs[run_id] = {"runId": run_id, "issueId": issue, "agentId": agent, "status": status,
                        "createdAt": "2026-11-12T18:34:2" + label, "errorCode": code,
                        "error": "run terminalized by recovery backstop: process and sandbox gone",
                        "finishedAt": datetime.fromtimestamp(finished, timezone.utc).isoformat()}
    api.issue(issue).update({"status": "in_progress", "executionRunId": None})
    return run_id


def test_a_task_left_in_progress_by_an_orphaned_run_is_retried_after_a_grace_period(
        app, project, configured, monkeypatch):
    # Observed live: Paperclip restarted three seconds into a run; it marked
    # the run interrupted (orphaned_running_run) and queued nothing more, so
    # the task stayed in progress and the request stayed running.
    request, api, engine, implement, verify = delivered(app, project, configured)
    clock = Clock()
    monkeypatch.setattr(engine_module.time, "time", clock)
    first = orphaned(api, implement, finished=clock.t)
    clock.t += 30
    engine.observe()
    assert api.retries == []  # Paperclip's own recovery goes first
    clock.t += 61
    observed = engine.observe()[request]
    assert [observed_run for _, _, observed_run in api.retries] == [first]
    assert observed["implement"]["report"] == "pending"
    event = app.store.rows("SELECT payload FROM events WHERE kind='execution.node_resumed'")
    assert json.loads(event[0]["payload"])["code"] == "orphaned_running_run"
    # The retried run is active: nothing more is retried and nothing is listed.
    api.runs[implement + "-retry"] = {"runId": implement + "-retry", "issueId": implement, "status": "running",
                                      "createdAt": "2026-11-12T18:40:00"}
    engine.observe()
    assert len(api.retries) == 1
    assert not app.store.rows("SELECT 1 FROM meta WHERE key LIKE 'failure:%'")
    assert app.store.snapshot()["attention"] == []


def test_a_stalled_task_that_cannot_be_retried_is_listed_for_the_operator(app, project, configured, monkeypatch):
    request, api, engine, implement, verify = delivered(app, project, configured)
    clock = Clock()
    monkeypatch.setattr(engine_module.time, "time", clock)
    orphaned(api, implement, finished=clock.t - 600, status="failed", code="adapter_failed")
    engine.observe()
    assert api.retries == []
    attention = app.store.snapshot()["attention"]
    assert attention[0]["status"] == "execution_failed" and "adapter_failed" in attention[0]["error"]
    # A task merely waiting for its first run is not stalled.
    assert all(i["report"] == "pending" for i in engine.observe()[request].values())
    assert [i["nodes"] for i in app.store.snapshot()["attention"]] == [["implement"]]

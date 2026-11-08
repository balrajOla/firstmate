"""Real loopback HTTP boundaries; coordinator doubles are explicitly fixtures."""

from contextlib import contextmanager
import http.client
import json
import threading
import time
from types import SimpleNamespace

import pytest

from assistant_workbench.runtime_backends import Fleet
from assistant_workbench.server import make_server
from assistant_workbench.util import WorkbenchError, canonical


@contextmanager
def running_bridge(app, background=False):
    server = make_server(app.home, background=background)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, (app.home / "service.token").read_text().strip()
    finally:
        server.workbench_stop.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def exchange(bridge, path, body=None, token=None, method="POST", headers=None):
    port, _ = bridge
    if body is not None and not isinstance(body, bytes):
        body = canonical(body).encode()
    actual = {"Connection": "close", "Content-Type": "application/json"}
    if token is not None:
        actual["Authorization"] = "Bearer " + token
    actual.update(headers or {})
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request(method, path, body=body, headers=actual)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def operator(bridge, action, **args):
    status, body = exchange(bridge, "/request", {"action": action, "args": args}, bridge[1])
    assert status == 200, body
    return body["result"]


def worker(bridge, host, token, action, **args):
    return exchange(bridge, "/worker", {"host_id": host, "action": action, "args": args}, token)


def registered(app, project, identity="host-a"):
    fleet = Fleet(app.store)
    receipt = fleet.register(identity, identity, "queue-" + identity, 2, ["process"], {project["id"]: "/work/" + identity})
    return fleet, receipt["token"]


def test_http_worker_credentials_cannot_access_operator_routes(app, project):
    fleet, token = registered(app, project)
    request = app.store.submit("Private original prompt", "private", project["id"])["request_id"]
    with running_bridge(app) as bridge:
        for method, path, body in [
            ("GET", "/snapshot", None),
            ("POST", "/request", {"action": "snapshot"}),
            ("POST", "/request", {"action": "request.cancel", "args": {"request_id": request}}),
            ("POST", "/request", {"action": "project.add", "args": {"name": "Unauthorized"}}),
        ]:
            status, result = exchange(bridge, path, body, token, method)
            assert status == 401 and "result" not in result
        for action in ("outcome.propose", "outcome.review", "project.grant", "coordinator.tick", "snapshot"):
            status, result = worker(bridge, "host-a", token, action, request_id=request)
            assert status == 400 and "Unsupported worker operation" in result["error"]
        status, result = worker(bridge, "host-a", bridge[1], "status")
        assert status == 400 and "Invalid host credential" in result["error"]
    assert app.dispatch("request.get", {"request_id": request})["status"] == "queued"
    assert len(app.dispatch("project.list")) == 1
    assert app.store.rows("SELECT * FROM outcomes") == []
    assert fleet.status()["hosts"][0]["heartbeat"] is None


def test_http_worker_scope_hides_other_hosts_projects_and_lease_tokens(app, project):
    fleet, token = registered(app, project)
    other = app.store.add_project("Other project")
    _, other_token = registered(app, other, "host-b")
    own_request = app.store.submit("HOST-A-PRIVATE-PROMPT", "a", project["id"])["request_id"]
    other_request = app.store.submit("HOST-B-PRIVATE-PROMPT", "b", other["id"])["request_id"]
    with running_bridge(app) as bridge:
        assert worker(bridge, "host-a", token, "heartbeat")[0] == 200
        assert worker(bridge, "host-b", other_token, "heartbeat")[0] == 200
        status, state = worker(bridge, "host-a", token, "request_state", request_id=own_request)
        assert status == 200 and state["result"]["id"] == own_request
        assert not {"body", "plan", "title"} & state["result"].keys()
        status, own_host = worker(bridge, "host-a", token, "status")
        assert status == 200 and set(own_host["result"]["workspaces"]) == {project["id"]}
        serialized = json.dumps(own_host)
        assert token not in serialized and other_token not in serialized and "token_hash" not in serialized
        for host, credential in [("host-b", token), ("host-a", other_token)]:
            status, result = worker(bridge, host, credential, "status")
            assert status == 400 and "Invalid host credential" in result["error"]
        for action, args in [
            ("request_state", {"request_id": other_request}),
            ("admit", {"request_id": other_request, "revision": 1, "task_key": "wrong-project", "capability": "process"}),
        ]:
            status, result = worker(bridge, "host-a", token, action, **args)
            assert status == 400 and "result" not in result
        status, receipt = worker(bridge, "host-a", token, "admit", request_id=own_request, revision=1, task_key="own", capability="process")
        assert status == 200
        lease = receipt["result"]
        for action, args in [
            ("renew", {"run_id": "stolen-run"}),
            ("release", {"state": "completed", "stop_receipt": {"run_id": "stolen-run", "stopped": True}}),
        ]:
            status, result = worker(bridge, "host-b", other_token, action, lease_id=lease["lease_id"], lease_token=lease["lease_token"], **args)
            assert status == 400 and "outside this worker's scope" in result["error"]
    assert fleet.status()["leases"][0]["state"] == "leased"


@pytest.mark.parametrize("origin", ["null", "http://127.0.0.1", "https://untrusted.example"])
def test_http_browser_origins_cannot_use_host_or_operator_tokens(app, project, origin):
    fleet, token = registered(app, project)
    with running_bridge(app) as bridge:
        for path, credential, body in [
            ("/worker", token, {"host_id": "host-a", "action": "heartbeat"}),
            ("/request", bridge[1], {"action": "project.add", "args": {"name": "Browser write"}}),
        ]:
            status, result = exchange(bridge, path, body, credential, headers={"Origin": origin})
            assert status == 401 and "result" not in result
        status, _ = exchange(bridge, "/snapshot", token=bridge[1], method="GET", headers={"Origin": origin})
        assert status == 401
    assert len(app.dispatch("project.list")) == 1
    assert fleet.status()["hosts"][0]["heartbeat"] is None


@pytest.mark.parametrize("length", ["0", "-1", str(2 * 1024 * 1024 + 1)])
def test_http_worker_body_bounds_reject_before_mutation(app, project, length):
    fleet, token = registered(app, project)
    with running_bridge(app) as bridge:
        status, result = exchange(bridge, "/worker", b"", token, headers={"Content-Length": length})
        assert status == 413 and "result" not in result
        assert worker(bridge, "host-a", token, "status")[0] == 200
    assert fleet.status()["hosts"][0]["heartbeat"] is None


@pytest.mark.parametrize("args", [[], False, 0, "", "not-an-object"])
def test_http_worker_rejects_nonobject_arguments(app, project, args):
    fleet, token = registered(app, project)
    with running_bridge(app) as bridge:
        status, result = exchange(bridge, "/worker", {"host_id": "host-a", "action": "heartbeat", "args": args}, token)
        assert status == 400 and "result" not in result
    assert fleet.status()["hosts"][0]["heartbeat"] is None


@pytest.mark.parametrize("body", [b"{broken", b"[]", b'{"host_id":"host-a","action":"heartbeat","unexpected":1}'])
def test_http_worker_bad_envelopes_have_no_effect(app, project, body):
    fleet, token = registered(app, project)
    with running_bridge(app) as bridge:
        status, result = exchange(bridge, "/worker", body, token)
        assert status == 400 and "result" not in result
    assert fleet.status()["hosts"][0]["heartbeat"] is None


def await_state(path, expected, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            value = json.loads(path.read_text())
            if value.get("status") == expected:
                return value
        time.sleep(.01)
    raise AssertionError(f"{path.name} did not reach {expected}")


@pytest.mark.parametrize("kind", ["paperclip", "temporal"])
def test_background_uses_selected_coordinator_and_recovers_observation_health(app, monkeypatch, kind):
    import assistant_workbench.app as application_module
    import assistant_workbench.server as server_module
    import assistant_workbench.temporal_runtime as temporal_module

    settings = json.loads((app.home / "config.json").read_text())
    settings["coordinator"] = {"kind": kind, "target": "127.0.0.1:7233"}
    (app.home / "config.json").write_text(canonical(settings))
    failure = threading.Event()
    failure.set()
    calls = []

    class FixtureRuntime:
        def __init__(self, store, settings=None):
            calls.append((self.name, "constructed"))

        def tick(self):
            calls.append((self.name, "tick"))
            return {"fixture_only": True}

        def observe(self):
            calls.append((self.name, "observe"))
            if failure.is_set():
                raise WorkbenchError("Deliberate coordinator fixture disconnect")
            return {}

    class PaperclipFixture(FixtureRuntime):
        name = "paperclip"

    class TemporalFixture(FixtureRuntime):
        name = "temporal"

    class ShortWaitEvent:
        def __init__(self):
            self.event = threading.Event()

        def is_set(self):
            return self.event.is_set()

        def set(self):
            self.event.set()

        def wait(self, timeout=None):
            return self.event.wait(min(timeout or .05, .05))

    monkeypatch.setattr(application_module, "Engine", PaperclipFixture)
    monkeypatch.setattr(temporal_module, "TemporalRuntime", TemporalFixture)
    monkeypatch.setattr(server_module, "threading", SimpleNamespace(Thread=threading.Thread, BoundedSemaphore=threading.BoundedSemaphore, Event=ShortWaitEvent))
    with running_bridge(app, background=True):
        failed = await_state(app.home / "observation-health.json", "unavailable")
        assert "fixture disconnect" in failed["reason"]
        failure.clear()
        recovered = await_state(app.home / "observation-health.json", "observed")
        assert recovered["at"] > failed["at"] and recovered["records"] == 0
        assert "reason" not in recovered
        await_state(app.home / "bridge-health.json", "running")
    assert {name for name, _ in calls} == {kind}
    assert (kind, "tick") in calls and (kind, "observe") in calls
    assert app.store.one("SELECT value FROM meta WHERE key='runtime:selected'")["value"] == kind


def test_http_completed_worker_receipts_need_observation_and_independent_review(app, project, configured, monkeypatch):
    """HTTP control and reconciliation contract; receipts below are labelled fixtures."""
    import assistant_workbench.temporal_runtime as temporal_module

    settings = json.loads((app.home / "config.json").read_text())
    settings["coordinator"] = {"kind": "temporal", "target": "127.0.0.1:7233"}
    (app.home / "config.json").write_text(canonical(settings))
    fleet, token = registered(app, project)
    observation = {}

    class TemporalFixture:
        def __init__(self, store, settings):
            pass

        def observe(self):
            return observation

    monkeypatch.setattr(temporal_module, "TemporalRuntime", TemporalFixture)
    with running_bridge(app) as bridge:
        request = operator(bridge, "prompt.submit", text="Create a website", client_key="http-full-path", project=project["id"])["request_id"]
        plan = operator(bridge, "request.plan", request_id=request)
        operator(bridge, "workflow.save_plan", request_id=request, revision=1, plan=plan)
        obligations = operator(bridge, "workflow.obligations", request_id=request, revision=1, items=[{"description": "Fixture artifact", "acceptance": "Review the explicit fixture limitation"}])["ids"]
        assert worker(bridge, "host-a", token, "heartbeat")[0] == 200
        results = {}
        for node in plan["nodes"]:
            status, receipt = worker(bridge, "host-a", token, "admit", request_id=request, revision=1, task_key=node["id"], capability="process")
            assert status == 200
            lease = receipt["result"]
            run_id = "http-fixture-" + node["id"]
            assert worker(bridge, "host-a", token, "renew", lease_id=lease["lease_id"], lease_token=lease["lease_token"], run_id=run_id)[0] == 200
            assert worker(bridge, "host-a", token, "release", lease_id=lease["lease_id"], lease_token=lease["lease_token"], state="completed", stop_receipt={"run_id": run_id, "stopped": True, "exit_code": 0})[0] == 200
            results[node["id"]] = {"node": node["id"], "role": node["role"], "host_id": "host-a", "exit_code": 0, "body": "HTTP contract fixture; no model or website execution.", "fixture_only": True}
        assert app.store.rows("SELECT * FROM outcomes") == []
        assert fleet.status()["hosts"][0]["occupied"] == 0
        observation[request + ":1"] = {"workflow_id": "http-fixture", "run_id": "http-fixture-run", "revision": 1, "status": "completed", "result": {"request_id": request, "revision": 1, "nodes": results}}
        operator(bridge, "coordinator.observe")
        operator(bridge, "coordinator.observe")
        outcomes = operator(bridge, "outcome.list", project=project["id"])
        assert len(outcomes) == 1 and outcomes[0]["status"] == "needs_review"
        assert operator(bridge, "request.get", request_id=request)["status"] == "needs_review"
        assert app.store.rows("SELECT * FROM reviews") == []
        for obligation in obligations:
            operator(bridge, "workflow.fulfill", obligation_id=obligation, outcome_id=outcomes[0]["id"])
        review = operator(bridge, "outcome.review", outcome_id=outcomes[0]["id"], reviewer="independent-fixture-reviewer", verdict="accept", notes="Reviewed the fixture limitation; this does not qualify a real model or artifact")
        assert review["request_status"] == "accepted"

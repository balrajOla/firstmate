"""Dispatcher integration tests; runtime routing spies are not live-service evidence."""

import json
from pathlib import Path
import sys
import types

import pytest

from assistant_workbench.app import Application
from assistant_workbench.config import config
from assistant_workbench.runtime_backends import Fleet, select_runtime
from assistant_workbench.util import WorkbenchError
from test_context_backends import backend_server, configure, source


def set_coordinator(app, **spec):
    path = app.home / "config.json"
    settings = json.loads(path.read_text())
    settings["coordinator"] = {"kind": "temporal", **spec}
    path.write_text(json.dumps(settings))
    return settings


@pytest.mark.parametrize("backend", ["hindsight", "openviking"])
def test_context_backend_actions_reach_http_and_preserve_receipts(
    app, project, tmp_path, backend_server, backend
):
    configure(app, project, backend_server, backend)
    original = source(app, project, tmp_path)
    args = {"backend": backend, "project": project["id"], "source_id": original["id"]}
    assert app.dispatch("backend.ingest", args)["state"] == "retained"
    assert app.dispatch("backend.read", args)["original_sha256"] == original["hash"]
    result = app.dispatch(
        "backend.recall",
        {"backend": backend, "project": project["id"], "query": "refund exclusions"},
    )
    assert result["results"][0]["derived"]
    assert app.dispatch("backend.reconcile", args)["state"] == "index_pending"
    assert (
        app.dispatch("backend.status", {"project": project["id"]})[0]["source_id"]
        == original["id"]
    )
    assert app.dispatch("backend.delete", args)["state"] == "deleted"
    assert len(backend_server["requests"]) == 5


def test_execution_actions_run_real_tests_and_reopen_results(app, project):
    repo = Path(project["repository"])
    (repo / "test_totals.py").write_text(
        "from decimal import Decimal\n"
        "def test_net_total():\n    assert Decimal('17.50') - Decimal('2.25') == Decimal('15.25')\n"
        "def test_refund_boundary():\n    assert max(0, 0 - 2) == 0\n"
    )
    (repo / "index.html").write_text(
        "<html><head><title>Totals</title></head><body>Summary</body></html>"
    )
    created = app.dispatch("execution.create_workspace", {"project": project["id"]})
    workspace_id = created["id"]
    assert app.dispatch("execution.inspect", {"workspace_id": workspace_id})[
        "languages"
    ] == ["python"]
    plan = app.dispatch(
        "execution.plan", {"workspace_id": workspace_id, "language": "python"}
    )
    run = app.dispatch(
        "execution.run", {"plan_id": plan["id"], "expected_sha256": plan["sha256"]}
    )
    assert run["status"] == "passed" and run["tests_observed"] == 2
    reopened = Application(app.home)
    assert (
        reopened.dispatch("execution.get_run", {"run_id": run["run_id"]})["result"][
            "tests_observed"
        ]
        == 2
    )
    assert (
        reopened.dispatch("execution.list_runs", {"workspace_id": workspace_id})[0][
            "id"
        ]
        == run["run_id"]
    )
    assert (
        reopened.dispatch("execution.list_workspaces", {"project": project["id"]})[0][
            "id"
        ]
        == workspace_id
    )
    assert (
        reopened.dispatch("execution.cancel", {"run_id": run["run_id"]})["status"]
        == "passed"
    )
    artifact = reopened.dispatch(
        "execution.artifacts",
        {"workspace_id": workspace_id, "paths": ["index.html"], "kind": "web"},
    )
    checked = reopened.dispatch(
        "execution.verify_artifacts", {"manifest_id": artifact["id"]}
    )
    assert checked["integrity"] == "passed"
    assert (
        checked["status"] == "needs_review"
        and checked["semantic_acceptance"] == "not granted"
    )


@pytest.mark.parametrize(
    "action",
    [
        "backend._bound",
        "backend.ingest.extra",
        "backend.request",
        "execution._execute",
        "execution.run.extra",
        "execution.__class__",
        "fleet._host",
        "fleet.worker_request",
        "fleet.admit",
        "fleet.register.extra",
    ],
)
def test_new_dispatch_namespaces_use_exact_method_allowlists(app, action):
    with pytest.raises(WorkbenchError, match="Unknown"):
        app.dispatch(action, {})


@pytest.mark.parametrize(
    "action,args",
    [
        (None, {}),
        ([], {}),
        ("", {}),
        ("backend.status", []),
        ("backend.status", "token"),
    ],
)
def test_dispatch_rejects_malformed_envelopes(app, action, args):
    with pytest.raises(WorkbenchError):
        app.dispatch(action, args)


def test_fleet_operator_surface_does_not_expose_registration_token(app, project):
    registration = app.dispatch(
        "fleet.register",
        {
            "host_id": "host-a",
            "name": "Host A",
            "task_queue": "worker-a",
            "capacity": 2,
            "capabilities": ["codex"],
            "workspaces": {project["id"]: project["repository"]},
        },
    )
    token = registration["token"]
    status = app.dispatch("fleet.status")
    events = app.store.rows("SELECT payload FROM events WHERE kind='fleet.registered'")
    public = json.dumps(
        {"status": status, "events": events, "snapshot": app.dispatch("snapshot")}
    )
    assert token not in public and "token_hash" not in public
    assert app.dispatch("fleet.expire") == {"uncertain": []}
    assert app.dispatch("fleet.disable", {"host_id": "host-a"})["enabled"] is False
    with pytest.raises(WorkbenchError, match="Unknown fleet"):
        app.dispatch("fleet.heartbeat", {"host_id": "host-a", "token": token})


def test_coordinator_dispatch_preserves_paperclip_default(app, monkeypatch):
    import assistant_workbench.app as app_module

    class PaperclipSpy:
        def __init__(self, store):
            self.api = self

        def tick(self):
            return {"selected": "paperclip", "operation": "tick"}

        def observe(self):
            return {"selected": "paperclip", "operation": "observe"}

        def health(self):
            return {"selected": "paperclip", "healthy": True}

    monkeypatch.setattr(app_module, "Engine", PaperclipSpy)
    assert app.dispatch("coordinator.tick")["selected"] == "paperclip"
    assert app.dispatch("coordinator.observe")["selected"] == "paperclip"
    health = app.dispatch("coordinator.health")
    assert health["selected"] == "paperclip"
    assert any(
        c["component"] == "paperclip" and c["reachable"] for c in app.doctor()["checks"]
    )


def test_temporal_dispatch_routes_health_tick_and_observation_reconciliation(
    app, monkeypatch
):
    import assistant_workbench.temporal_runtime as temporal

    set_coordinator(app, target="127.0.0.1:7233", task_queue="test-control")
    calls = []

    class TemporalSpy:
        def __init__(self, store, settings):
            assert settings["coordinator"]["kind"] == "temporal"

        def tick(self):
            calls.append("tick")
            return {"selected": "temporal"}

        def observe(self, request_id=None):
            calls.append(("observe", request_id))
            return {"workflow": {"status": "running"}}

        def health(self):
            calls.append("health")
            return {"kind": "temporal", "reachable": True}

    def reconcile(store, observed):
        calls.append("reconcile")
        assert store is app.store
        return {**observed, "reconciled": True}

    monkeypatch.setattr(temporal, "TemporalRuntime", TemporalSpy)
    monkeypatch.setitem(
        sys.modules,
        "assistant_workbench.runtime_observations",
        types.SimpleNamespace(reconcile_observations=reconcile),
    )
    assert app.dispatch("coordinator.tick")["selected"] == "temporal"
    assert app.dispatch("coordinator.observe", {"request_id": "request-1"})[
        "reconciled"
    ]
    assert app.dispatch("coordinator.health")["kind"] == "temporal"
    assert calls == ["tick", ("observe", "request-1"), "reconcile", "health"]
    # Paperclip agent retirement never runs against the Temporal backend.
    with pytest.raises(WorkbenchError, match="Paperclip coordinator"):
        app.dispatch("coordinator.retire_agents", {"idle": True})
    assert any(
        c["component"] == "temporal" and c["reachable"] for c in app.doctor()["checks"]
    )
    assert not any(c["component"] == "paperclip" for c in app.doctor()["checks"])


def test_temporal_configuration_is_valid_without_sdk_and_health_reports_missing_sdk(
    app, monkeypatch
):
    import assistant_workbench.temporal_runtime as temporal

    set_coordinator(app, target="127.0.0.1:7233")
    monkeypatch.setattr(temporal, "SDK_AVAILABLE", False)
    assert config(app.home)["coordinator"]["kind"] == "temporal"
    Application(app.home)
    with pytest.raises(WorkbenchError, match="SDK is missing"):
        app.dispatch("coordinator.health")
    check = next(c for c in app.doctor()["checks"] if c["component"] == "temporal")
    assert not check["reachable"] and "SDK is missing" in check["reason"]


def test_temporal_lifecycle_never_falls_back_to_paperclip(app, monkeypatch):
    import assistant_workbench.temporal_runtime as temporal
    import assistant_workbench.lifecycle as lifecycle

    set_coordinator(app, target="127.0.0.1:7233")

    class TemporalSpy:
        def __init__(self, store, settings):
            pass

        def rollover(self, request_id, node):
            return {
                "request_id": request_id,
                "node": node,
                "state": "pending",
                "selected": "temporal",
            }

    monkeypatch.setattr(temporal, "TemporalRuntime", TemporalSpy)
    monkeypatch.setattr(
        lifecycle,
        "Lifecycle",
        lambda *a, **k: pytest.fail("Paperclip lifecycle must not be constructed"),
    )
    result = app.dispatch(
        "lifecycle.request", {"request_id": "request-1", "node": "worker"}
    )
    assert result["selected"] == "temporal" and result["node"] == "worker"


def test_paperclip_lifecycle_keeps_existing_request_contract(app, monkeypatch):
    import assistant_workbench.lifecycle as lifecycle

    class LifecycleSpy:
        def __init__(self, store):
            assert store is app.store

        def request(self, request_id, node):
            return {"request_id": request_id, "node": node, "state": "pending"}

    monkeypatch.setattr(lifecycle, "Lifecycle", LifecycleSpy)
    assert app.dispatch(
        "lifecycle.request", {"request_id": "request-1", "node": "worker"}
    ) == {
        "request_id": "request-1",
        "node": "worker",
        "state": "pending",
    }


def test_retry_works_for_temporal_without_constructing_paperclip(
    app, project, monkeypatch
):
    import assistant_workbench.paperclip as paperclip

    set_coordinator(app, target="127.0.0.1:7233")
    request = app.dispatch(
        "prompt.submit",
        {
            "text": "Build a project report",
            "client_key": "retry",
            "project": project["id"],
        },
    )
    with app.store.transaction() as db:
        db.execute(
            "UPDATE outbox SET status='blocked' WHERE request_id=?",
            (request["request_id"],),
        )
    monkeypatch.setattr(
        paperclip,
        "Paperclip",
        lambda *a, **k: pytest.fail("Paperclip must not be constructed"),
    )
    app.dispatch("request.retry", {"request_id": request["request_id"]})
    assert (
        app.store.one(
            "SELECT status FROM outbox WHERE request_id=?", (request["request_id"],)
        )["status"]
        == "retry"
    )
    with app.store.transaction() as db:
        db.execute(
            "UPDATE outbox SET status='uncertain' WHERE request_id=?",
            (request["request_id"],),
        )
    app.dispatch("request.retry", {"request_id": request["request_id"]})
    assert (
        app.store.one(
            "SELECT status FROM outbox WHERE request_id=?", (request["request_id"],)
        )["status"]
        == "uncertain"
    )


@pytest.mark.parametrize(
    "action,args",
    [
        ("coordinator.tick", {}),
        ("lifecycle.request", {"request_id": "ignored", "node": "worker"}),
    ],
)
def test_dispatch_runtime_switch_is_fenced_by_active_lease(app, project, action, args):
    select_runtime(app.store, config(app.home))
    fleet = Fleet(app.store)
    registration = fleet.register(
        "host-a",
        "Host A",
        "queue-a",
        1,
        ["process"],
        {project["id"]: project["repository"]},
    )
    fleet.heartbeat("host-a", registration["token"])
    request = app.dispatch(
        "prompt.submit",
        {"text": "Work", "client_key": "lease", "project": project["id"]},
    )
    fleet.admit(
        "host-a", registration["token"], request["request_id"], 1, "worker", "process"
    )
    set_coordinator(app, target="127.0.0.1:7233")
    with pytest.raises(WorkbenchError, match="stop active work"):
        app.dispatch(action, args)


@pytest.mark.parametrize(
    "settings",
    [
        {"target": "https://example.com:7233"},
        {"target": "host:bad"},
        {"target": "host"},
        {"target": "user:secret@host:7233"},
        {"target": "localhost:7233/path"},
        {"target": "localhost:7233?x=1"},
        {"target": "remote.example:7233"},
        {"target": "[broken"},
        {"tls": "true"},
        {"namespace": "../other"},
        {"task_queue": ""},
        {"host_id": "a/b"},
        {"api_key_env": "$(read-key)"},
        {"client_cert": "/tmp/cert"},
        {"tls": True, "client_cert": "cert", "client_key": "key"},
        {"client_cert": "/tmp/cert", "client_key": "/tmp/key"},
    ],
)
def test_invalid_temporal_configuration_rejected_without_network(app, settings):
    set_coordinator(app, **settings)
    with pytest.raises(WorkbenchError):
        config(app.home)


def test_temporal_auth_configuration_does_not_read_credentials_or_require_sdk(
    app, monkeypatch
):
    monkeypatch.delenv("AWB_TEST_TEMPORAL_KEY", raising=False)
    set_coordinator(
        app,
        target="cluster.example:7233",
        tls=True,
        client_cert="/nonexistent/operator-cert.pem",
        client_key="/nonexistent/operator-key.pem",
        api_key_env="AWB_TEST_TEMPORAL_KEY",
        namespace="team.a",
        task_queue="control-a",
        host_id="host-a",
    )
    assert config(app.home)["coordinator"]["api_key_env"] == "AWB_TEST_TEMPORAL_KEY"


@pytest.mark.parametrize(
    "coordinator", [None, [], {"kind": ["paperclip", "temporal"]}, {"kind": "both"}]
)
def test_configuration_requires_exactly_one_coordinator(app, coordinator):
    path = app.home / "config.json"
    settings = json.loads(path.read_text())
    settings["coordinator"] = coordinator
    path.write_text(json.dumps(settings))
    with pytest.raises(WorkbenchError, match="exactly one coordinator"):
        config(app.home)

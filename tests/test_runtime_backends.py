"""Runtime safety contracts; real Temporal tests still use labelled fixture activities."""

import asyncio
import copy
import json
import os
from pathlib import Path

import pytest

from assistant_workbench.runtime_backends import Fleet, select_runtime
from assistant_workbench.temporal_runtime import (
    TemporalRuntime,
    plan_layers,
    serve_worker,
)
from assistant_workbench.util import WorkbenchError


def registered(app, project, *, capacity=2, reserve=0, host_id="host-a"):
    fleet = Fleet(app.store)
    receipt = fleet.register(
        host_id,
        host_id,
        "queue-" + host_id,
        capacity,
        ["process"],
        {project["id"]: project["repository"]},
        reserve,
    )
    return fleet, receipt["token"]


def submitted(app, project, key="request"):
    return app.store.submit("Research the project", key, project["id"])["request_id"]


def test_fleet_registration_scope_and_no_secrets(app, project):
    fleet, token = registered(app, project)
    request_id = submitted(app, project)
    with pytest.raises(WorkbenchError, match="heartbeat"):
        fleet.admit("host-a", token, request_id, 1, "task", "process")
    with pytest.raises(WorkbenchError, match="credential"):
        fleet.heartbeat("host-a", "wrong")
    fleet.heartbeat("host-a", token)
    with pytest.raises(WorkbenchError, match="capability"):
        fleet.admit("host-a", token, request_id, 1, "task", "codex")
    public = json.dumps(fleet.status())
    assert token not in public and "token_hash" not in public
    with pytest.raises(WorkbenchError, match="already exists"):
        registered(app, project)


def test_fleet_capacity_reservation_and_idempotency(app, project):
    fleet, token = registered(app, project, capacity=2, reserve=1)
    fleet.heartbeat("host-a", token)
    request_id = submitted(app, project)
    first = fleet.admit("host-a", token, request_id, 1, "task-a", "process")
    assert fleet.admit("host-a", token, request_id, 1, "task-a", "process") == first
    with pytest.raises(WorkbenchError, match="capacity"):
        fleet.admit("host-a", token, request_id, 1, "task-b", "process")
    fleet.admit("host-a", token, request_id, 1, "task-b", "process", interactive=True)
    assert fleet.status()["hosts"][0]["occupied"] == 2
    with pytest.raises(WorkbenchError, match="capacity"):
        fleet.admit(
            "host-a", token, request_id, 1, "task-c", "process", interactive=True
        )


def test_fleet_expired_lease_keeps_capacity_until_stop(app, project):
    fleet, token = registered(app, project, capacity=1)
    fleet.heartbeat("host-a", token)
    request_id = submitted(app, project)
    lease = fleet.admit("host-a", token, request_id, 1, "task", "process")
    fleet.renew(lease["lease_id"], lease["lease_token"], "process-1")
    with app.store.transaction() as db:
        db.execute("UPDATE runtime_leases SET expires=0")
    assert fleet.status()["hosts"][0]["occupied"] == 1
    assert fleet.status()["leases"][0]["state"] == "uncertain"
    with pytest.raises(WorkbenchError, match="capacity"):
        fleet.admit("host-a", token, request_id, 1, "another-task", "process")
    with pytest.raises(WorkbenchError, match="identify"):
        fleet.release(
            lease["lease_id"],
            lease["lease_token"],
            "cancelled",
            {"run_id": "wrong", "stopped": True},
        )
    receipt = {"run_id": "process-1", "stopped": True}
    fleet.release(lease["lease_id"], lease["lease_token"], "cancelled", receipt)
    assert fleet.release(lease["lease_id"], lease["lease_token"], "cancelled", receipt)[
        "duplicate"
    ]
    next_lease = fleet.admit(
        "host-a", token, request_id, 1, "task", "process", generation=2
    )
    assert next_lease["lease_id"] != lease["lease_id"]


def test_fleet_revision_and_cancel_fence_renewal(app, project):
    fleet, token = registered(app, project)
    fleet.heartbeat("host-a", token)
    request_id = submitted(app, project)
    lease = fleet.admit("host-a", token, request_id, 1, "task", "process")
    app.store.submit(
        "Expand the topic", "amend", reply_to=request_id, expected_revision=1
    )
    with pytest.raises(WorkbenchError, match="fenced"):
        fleet.renew(lease["lease_id"], lease["lease_token"], "process-1")
    fleet.cancel(lease["lease_id"])
    assert fleet.status()["hosts"][0]["occupied"] == 1
    fleet.release(
        lease["lease_id"],
        lease["lease_token"],
        "cancelled",
        {"run_id": None, "stopped": True, "never_started": True},
    )
    assert fleet.status()["hosts"][0]["occupied"] == 0


def test_runtime_switch_rejects_active_or_uncertain_process(app, project):
    select_runtime(app.store, {"coordinator": {"kind": "paperclip"}})
    fleet, token = registered(app, project)
    fleet.heartbeat("host-a", token)
    fleet.admit("host-a", token, submitted(app, project), 1, "task", "process")
    with pytest.raises(WorkbenchError, match="stop active"):
        select_runtime(app.store, {"coordinator": {"kind": "temporal"}})
    with pytest.raises(WorkbenchError, match="inactive"):
        select_runtime(app.store, {"coordinator": {"kind": "paperclip"}}, "temporal")


def test_temporal_graph_bounds_dependencies_and_cycle():
    nodes = [{"id": "b", "depends_on": ["a"]}, {"id": "a", "depends_on": []}]
    assert [[n["id"] for n in layer] for layer in plan_layers(nodes, 2)] == [
        ["a"],
        ["b"],
    ]
    with pytest.raises(WorkbenchError, match="budget"):
        plan_layers(nodes, 1)
    with pytest.raises(WorkbenchError, match="missing"):
        plan_layers([{"id": "b", "depends_on": ["missing"]}], 1)
    with pytest.raises(WorkbenchError, match="cycle"):
        plan_layers([{"id": "b", "depends_on": ["b"]}], 1)


@pytest.fixture
async def temporal_environment(tmp_path):
    pytest.importorskip("temporalio")
    binary = os.environ.get("AWB_TEMPORAL_SERVER_BINARY")
    if not binary or not Path(binary).is_file():
        pytest.skip(
            "Set AWB_TEMPORAL_SERVER_BINARY to the official Temporal CLI for real-service tests"
        )
    from temporalio.testing import WorkflowEnvironment

    async with await WorkflowEnvironment.start_local(
        dev_server_existing_path=binary,
        dev_server_database_filename=str(tmp_path / "temporal.sqlite"),
        dev_server_log_level="error",
    ) as env:
        yield env


def temporal_settings(configured, environment):
    settings = copy.deepcopy(configured)
    settings["coordinator"] = {
        "kind": "temporal",
        "target": environment.client.service_client.config.target_host,
        "task_queue": "test-control",
        "host_id": "host-a",
    }
    return settings


async def stop_worker(task):
    task.cancel()
    await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 15)


@pytest.mark.asyncio
async def test_temporal_real_service_dag_idempotency_recovery(
    app, project, configured, temporal_environment
):
    """Actual Temporal server/SDK/queues; task outputs are deterministic fixtures, not LLMs."""
    settings = temporal_settings(configured, temporal_environment)
    fleet, token = registered(app, project, capacity=4)
    fleet.heartbeat("host-a", token)
    request_id = submitted(app, project)
    started, finished, overlap = [], [], set()

    class FixtureExecutor:
        async def execute(self, packet):
            node = packet["node"]
            assert set(node["depends_on"]).issubset(finished)
            assert set(packet["prior_results"]) == set(node["depends_on"])
            started.append(node["id"])
            overlap.update(n for n in started if n not in finished)
            await asyncio.sleep(0.15)
            finished.append(node["id"])
            return {
                "node": node["id"],
                "body": "CONTRACT FIXTURE",
                "fixture_only": True,
            }

    worker = asyncio.create_task(
        serve_worker(
            app.store,
            settings,
            "host-a",
            token,
            client=temporal_environment.client,
            executor=FixtureExecutor(),
        )
    )
    runtime = TemporalRuntime(app.store, settings, temporal_environment.client)
    try:
        first = await runtime.a_submit(request_id, kind="research")
        duplicate = await runtime.a_submit(request_id, kind="research")
        assert first["workflow_id"] == duplicate["workflow_id"]
        result = await asyncio.wait_for(
            temporal_environment.client.get_workflow_handle(
                first["workflow_id"]
            ).result(),
            25,
        )
        assert result["status"] == "needs_review"
        assert sorted(started) == ["research-a", "research-b", "synthesize", "verify"]
        assert started[:2] == ["research-a", "research-b"] or started[:2] == [
            "research-b",
            "research-a",
        ]
        assert {"research-a", "research-b"}.issubset(overlap)
        # A fresh controller reads durable execution state and results by workflow identity.
        recovered = TemporalRuntime(app.store, settings, temporal_environment.client)
        observed = await recovered.a_observe(request_id)
        assert observed[request_id + ":1"]["status"] == "completed"
        assert (await recovered.a_submit(request_id))["duplicate"]
        assert len(started) == 4
    finally:
        await stop_worker(worker)


@pytest.mark.asyncio
async def test_temporal_real_service_cancellation_stops_activity(
    app, project, configured, temporal_environment
):
    """Real service delivers cancellation; a fixture activity acknowledges its stop."""
    from temporalio import activity
    from temporalio.client import WorkflowFailureError

    settings = temporal_settings(configured, temporal_environment)
    fleet, token = registered(app, project)
    fleet.heartbeat("host-a", token)
    request_id = submitted(app, project)
    running, stopped = asyncio.Event(), asyncio.Event()

    class WaitingFixture:
        async def execute(self, packet):
            running.set()
            try:
                while True:
                    activity.heartbeat("contract fixture alive")
                    await asyncio.sleep(0.05)
            finally:
                stopped.set()

    worker = asyncio.create_task(
        serve_worker(
            app.store,
            settings,
            "host-a",
            token,
            client=temporal_environment.client,
            executor=WaitingFixture(),
        )
    )
    runtime = TemporalRuntime(app.store, settings, temporal_environment.client)
    try:
        first = await runtime.a_submit(request_id, kind="simple")
        await asyncio.wait_for(running.wait(), 20)
        cancellation = await runtime.a_cancel(request_id)
        assert cancellation["stopped"] is False
        with pytest.raises(WorkflowFailureError):
            await asyncio.wait_for(
                temporal_environment.client.get_workflow_handle(
                    first["workflow_id"]
                ).result(),
                25,
            )
        assert stopped.is_set()
        assert (await runtime.a_observe(request_id))[request_id + ":1"][
            "status"
        ] == "cancelled"
    finally:
        await stop_worker(worker)


def test_worker_control_host_project_and_lease_scope(app, project, tmp_path):
    fleet, token = registered(app, project)
    second_repo = tmp_path / "second"
    second_repo.mkdir()
    second = app.store.add_project("Other", str(second_repo), [])
    second_token = fleet.register(
        "host-b", "Other", "queue-b", 2, ["process"], {second["id"]: str(second_repo)}
    )["token"]
    request_id = submitted(app, project)
    fleet.worker_request("host-a", token, "heartbeat")
    lease = fleet.worker_request(
        "host-a",
        token,
        "admit",
        {
            "request_id": request_id,
            "revision": 1,
            "task_key": "a",
            "capability": "process",
        },
    )
    assert (
        fleet.worker_request(
            "host-a", token, "request_state", {"request_id": request_id}
        )["revision"]
        == 1
    )
    with pytest.raises(WorkbenchError, match="project scope"):
        fleet.worker_request(
            "host-b", second_token, "request_state", {"request_id": request_id}
        )
    with pytest.raises(WorkbenchError, match="outside"):
        fleet.worker_request(
            "host-b",
            second_token,
            "renew",
            {
                "lease_id": lease["lease_id"],
                "lease_token": lease["lease_token"],
                "run_id": "run",
            },
        )
    with pytest.raises(WorkbenchError, match="reserve"):
        fleet.worker_request("host-a", token, "admit", {"interactive": True})
    with pytest.raises(WorkbenchError, match="Unsupported"):
        fleet.worker_request("host-a", token, "register", {})
    host = fleet.worker_request("host-a", token, "status")
    assert host["id"] == "host-a" and second["id"] not in host["workspaces"]


def test_worker_control_transport_rejects_unsafe_origins():
    from assistant_workbench.runtime_backends import WorkerControl

    for url in [
        "http://remote.example",
        "https://user:password@example.org",
        "https://example.org/path",
        "file:///etc/passwd",
    ]:
        with pytest.raises(WorkbenchError):
            WorkerControl("host", "token", endpoint=url)
    WorkerControl("host", "token", endpoint="http://127.0.0.1:1234")
    WorkerControl("host", "token", endpoint="https://workers.example")


@pytest.mark.asyncio
async def test_temporal_real_service_checkpoint_rollover(
    app, project, configured, temporal_environment
):
    """Actual workflow signal and fresh activity after a fixture process-stop receipt."""
    from temporalio import activity
    from assistant_workbench.continuation import Continuation
    from assistant_workbench.util import digest

    settings = temporal_settings(configured, temporal_environment)
    fleet, token = registered(app, project)
    fleet.heartbeat("host-a", token)
    request_id = submitted(app, project)
    running, stopped = asyncio.Event(), asyncio.Event()
    generations = []
    checkpoint_file = Path(project["repository"]) / "analysis.txt"
    checkpoint_file.write_text("Verified partial findings")

    class RolloverFixture:
        async def execute(self, packet):
            generation = packet["generation"]
            lease = fleet.admit(
                "host-a",
                token,
                request_id,
                1,
                packet["node"]["id"],
                "process",
                generation=generation,
            )
            run = "fixture-generation-" + str(generation)
            fleet.renew(lease["lease_id"], lease["lease_token"], run)
            generations.append(generation)
            if generation == 1:
                running.set()
                try:
                    while True:
                        activity.heartbeat("fixture waiting for a checkpoint handoff")
                        await asyncio.sleep(0.05)
                finally:
                    fleet.release(
                        lease["lease_id"],
                        lease["lease_token"],
                        "cancelled",
                        {"run_id": run, "stopped": True},
                    )
                    stopped.set()
            assert stopped.is_set()
            assert packet["checkpoint"]["summary"] == "Continue with verified findings"
            fleet.release(
                lease["lease_id"],
                lease["lease_token"],
                "completed",
                {"run_id": run, "stopped": True},
            )
            return {
                "fixture_only": True,
                "generation": generation,
                "body": "CONTRACT FIXTURE",
            }

    worker = asyncio.create_task(
        serve_worker(
            app.store,
            settings,
            "host-a",
            token,
            client=temporal_environment.client,
            executor=RolloverFixture(),
        )
    )
    runtime = TemporalRuntime(app.store, settings, temporal_environment.client)
    try:
        first = await runtime.a_submit(request_id, kind="simple")
        await asyncio.wait_for(running.wait(), 20)
        Continuation(app.store, settings["limits"]).checkpoint(
            request_id,
            1,
            1,
            "Continue with verified findings",
            [
                {
                    "path": str(checkpoint_file),
                    "sha256": digest(checkpoint_file.read_bytes()),
                }
            ],
            ["Finish research"],
        )
        checkpoint_file.write_text("Changed after checkpoint")
        with pytest.raises(WorkbenchError, match="artifact changed"):
            await runtime.a_rollover(request_id, "worker")
        checkpoint_file.write_text("Verified partial findings")
        assert (await runtime.a_rollover(request_id, "worker"))["state"] == "quiescing"
        result = await asyncio.wait_for(
            temporal_environment.client.get_workflow_handle(
                first["workflow_id"]
            ).result(),
            30,
        )
        assert result["nodes"]["worker"]["generation"] == 2
        assert generations == [1, 2]
        assert fleet.status()["hosts"][0]["occupied"] == 0
    finally:
        await stop_worker(worker)


@pytest.mark.asyncio
async def test_worker_control_real_http_host_auth_and_scope(app, project):
    """Real loopback HTTP route, authenticated worker protocol, no operator token."""
    import threading
    from assistant_workbench.runtime_backends import WorkerControl
    from assistant_workbench.server import make_server

    fleet, token = registered(app, project)
    request_id = submitted(app, project)
    server = make_server(app.home)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = "http://127.0.0.1:" + str(server.server_address[1])
    worker = WorkerControl("host-a", token, endpoint=endpoint)
    try:
        await worker.call("heartbeat")
        assert (await worker.call("status"))["id"] == "host-a"
        lease = await worker.call(
            "admit",
            request_id=request_id,
            revision=1,
            task_key="remote",
            capability="process",
        )
        await worker.call(
            "renew",
            lease_id=lease["lease_id"],
            lease_token=lease["lease_token"],
            run_id="remote-process",
        )
        await worker.call(
            "release",
            lease_id=lease["lease_id"],
            lease_token=lease["lease_token"],
            state="completed",
            stop_receipt={"run_id": "remote-process", "stopped": True},
        )
        assert fleet.status()["hosts"][0]["occupied"] == 0
        with pytest.raises(WorkbenchError, match="rejected"):
            await WorkerControl("host-a", "wrong", endpoint=endpoint).call("status")
        with pytest.raises(WorkbenchError, match="rejected"):
            await worker.call("register", host_id="escalated")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.asyncio
async def test_temporal_separate_worker_store_over_http(
    app, project, configured, temporal_environment, tmp_path
):
    """Actual Temporal+HTTP with isolated worker storage on one machine; fixture task."""
    import threading
    from assistant_workbench.app import Application
    from assistant_workbench.runtime_backends import WorkerControl
    from assistant_workbench.server import make_server

    settings = temporal_settings(configured, temporal_environment)
    fleet, token = registered(app, project)
    request_id = submitted(app, project)
    remote_store = Application(tmp_path / "isolated-worker-state").store
    server = make_server(app.home)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = "http://127.0.0.1:" + str(server.server_address[1])
    control = WorkerControl("host-a", token, endpoint=endpoint)
    ready = asyncio.Event()

    class RemoteFixture:
        async def execute(self, packet):
            assert (await control.call("request_state", request_id=request_id))[
                "payload_hash"
            ] == packet["payload_hash"]
            lease = await control.call(
                "admit",
                request_id=request_id,
                revision=1,
                task_key=packet["node"]["id"],
                capability="process",
            )
            await control.call(
                "renew",
                lease_id=lease["lease_id"],
                lease_token=lease["lease_token"],
                run_id="remote-fixture",
            )
            await control.call(
                "release",
                lease_id=lease["lease_id"],
                lease_token=lease["lease_token"],
                state="completed",
                stop_receipt={"run_id": "remote-fixture", "stopped": True},
            )
            return {"fixture_only": True, "body": "REMOTE CONTROL FIXTURE"}

    await control.call("heartbeat")
    worker = asyncio.create_task(
        serve_worker(
            remote_store,
            settings,
            "host-a",
            token,
            client=temporal_environment.client,
            executor=RemoteFixture(),
            control_endpoint=endpoint,
        )
    )
    runtime = TemporalRuntime(app.store, settings, temporal_environment.client)
    try:
        execution = await runtime.a_submit(request_id, kind="simple")
        result = await asyncio.wait_for(
            temporal_environment.client.get_workflow_handle(
                execution["workflow_id"]
            ).result(),
            20,
        )
        assert result["nodes"]["worker"]["fixture_only"]
        assert not remote_store.rows("SELECT * FROM requests")
        assert not remote_store.rows("SELECT * FROM runtime_hosts")
        assert fleet.status()["leases"][0]["state"] == "completed"
    finally:
        await stop_worker(worker)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_temporal_missing_sdk_is_actionable(monkeypatch):
    from assistant_workbench import temporal_runtime

    monkeypatch.setattr(temporal_runtime, "SDK_AVAILABLE", False)
    with pytest.raises(WorkbenchError, match="install temporalio==1.35.0"):
        temporal_runtime.require_sdk()


@pytest.mark.asyncio
async def test_temporal_missing_worker_poller_blocks_dispatch(
    app, project, configured, temporal_environment
):
    settings = temporal_settings(configured, temporal_environment)
    fleet, token = registered(app, project)
    fleet.heartbeat("host-a", token)
    request_id = submitted(app, project)
    runtime = TemporalRuntime(app.store, settings, temporal_environment.client)
    with pytest.raises(WorkbenchError, match="No recent Temporal activity poller"):
        await runtime.a_submit(request_id, kind="simple")
    assert not app.store.rows("SELECT * FROM temporal_executions")


@pytest.mark.asyncio
async def test_cli_process_contract_stdin_receipt_and_cancel(
    app, project, configured, tmp_path, monkeypatch
):
    """Deliberate local subprocess fixture: proves process control, never an LLM canary."""
    pytest.importorskip("temporalio")
    from assistant_workbench import profiles, temporal_runtime
    from assistant_workbench.temporal_runtime import OfficialCLIExecutor
    from assistant_workbench.util import canonical, digest, now

    executable = tmp_path / "fixture-cli"
    executable.write_text(
        "#!/usr/bin/env python3\nimport json,sys,time\nprompt=sys.stdin.read()\nif 'WAIT_FOR_CANCEL' in prompt:\n    time.sleep(90)\nprint(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'EXPLICIT PROCESS CONTRACT FIXTURE'}}))\nprint(json.dumps({'type':'turn.completed','usage':{'input_tokens':100,'cached_input_tokens':60,'output_tokens':40,'reasoning_output_tokens':15}}))\n"
    )
    executable.chmod(0o700)
    monkeypatch.setattr(temporal_runtime.shutil, "which", lambda name: str(executable))
    monkeypatch.setattr(profiles, "readiness", lambda model: (True, "TEST MOCK ONLY"))
    monkeypatch.setattr(temporal_runtime.activity, "heartbeat", lambda *args: None)
    settings = copy.deepcopy(configured)
    model = {
        "id": "contract",
        "harness": "codex",
        "model": "fixture",
        "effort": "default",
        "auth": "local",
        "fixture_only": True,
    }
    settings["models"] = [model]
    fleet = Fleet(app.store)
    token = fleet.register(
        "host-a",
        "Fixture worker",
        "queue-a",
        2,
        ["codex"],
        {project["id"]: project["repository"]},
    )["token"]
    fleet.heartbeat("host-a", token)
    executor = OfficialCLIExecutor(app.store, settings, "host-a", token)

    def packet_for(body, key):
        request_id = app.store.submit(body, key, project["id"])["request_id"]
        packet = {
            "request_id": request_id,
            "revision": 1,
            "project_id": project["id"],
            "body": body,
            "generation": 1,
            "node": {"id": "worker", "role": "general"},
            "plan": {"model": model, "limits": settings["limits"], "skills": []},
            "deadline": now() + 60,
        }
        packet["payload_hash"] = digest(canonical(packet))
        with app.store.transaction() as db:
            db.execute(
                "INSERT INTO temporal_executions VALUES(?,?,?,NULL,?,'running',NULL,?,?)",
                (
                    request_id,
                    1,
                    "fixture-" + request_id,
                    packet["payload_hash"],
                    now(),
                    now(),
                ),
            )
        return packet

    complete = packet_for("Read the closed input stream", "process-complete")
    result = await executor.execute(complete)
    assert result["body"] == "EXPLICIT PROCESS CONTRACT FIXTURE"
    assert result["usage"]["tokens"]["input_tokens"] == 100
    assert result["usage"]["tokens"]["cache_write_input_tokens"] is None
    assert fleet.usage()["totals"]["input_tokens"] == 100
    assert (await executor.execute(complete)) == result
    assert len(fleet.status()["leases"]) == 1
    waiting = packet_for("WAIT_FOR_CANCEL", "process-cancel")
    task = asyncio.create_task(executor.execute(waiting))
    pid = None
    try:
        for _ in range(100):
            receipts = list((app.home / "worker-runs").glob("*/receipt.json"))
            for path in receipts:
                receipt = json.loads(path.read_text())
                if (
                    receipt.get("request_id") == waiting["request_id"]
                    and receipt.get("state") == "running"
                ):
                    pid = receipt["pid"]
            if pid:
                break
            await asyncio.sleep(0.02)
        assert pid
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 10)
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        assert fleet.status()["hosts"][0]["occupied"] == 0
        assert fleet.usage()["totals"]["input_tokens"] == 100
        assert fleet.usage()["coverage"]["unknown"] == 1
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def test_cli_usage_codex_preserves_cache_and_reasoning_without_double_count():
    """Protocol fixture from the documented CLI event shape, not a model call."""
    from assistant_workbench.temporal_runtime import parse_cli_usage

    event = {
        "type": "turn.completed",
        "usage": {
            "input_tokens": 100,
            "cached_input_tokens": 60,
            "cache_write_input_tokens": 10,
            "output_tokens": 40,
            "reasoning_output_tokens": 15,
        },
    }
    parsed = parse_cli_usage(json.dumps(event) + "\n" + json.dumps(event), "codex")
    assert parsed["tokens"] == {
        "input_tokens": 100,
        "cached_input_tokens": 60,
        "cache_write_input_tokens": 10,
        "output_tokens": 40,
        "reasoning_output_tokens": 15,
    }
    assert parsed["terminal_events"] == 2
    assert parsed["input_output_complete"]
    assert parsed["cost_usd"] is None and parsed["subscription_remaining"] is None
    event["usage"]["input_tokens"] = 150
    latest = parse_cli_usage(
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 100, "output_tokens": 20},
            }
        )
        + "\n"
        + json.dumps(event),
        "codex",
    )
    assert latest["tokens"]["input_tokens"] == 150


def test_cli_usage_claude_normalizes_separate_cache_buckets_and_unknown_reasoning():
    from assistant_workbench.temporal_runtime import parse_cli_usage

    event = {
        "type": "result",
        "is_error": False,
        "usage": {
            "input_tokens": 5,
            "cache_read_input_tokens": 10,
            "cache_creation_input_tokens": 1,
            "output_tokens": 15,
        },
        "total_cost_usd": 123.4,
    }
    parsed = parse_cli_usage(json.dumps(event), "claude")
    assert parsed["tokens"]["input_tokens"] == 16
    assert parsed["reported_input_tokens"] == 5
    assert parsed["tokens"]["cached_input_tokens"] == 10
    assert parsed["tokens"]["reasoning_output_tokens"] is None
    assert parsed["cost_usd"] is None
    del event["usage"]["cache_creation_input_tokens"]
    incomplete = parse_cli_usage(json.dumps(event), "claude")
    assert incomplete["tokens"]["input_tokens"] is None
    assert incomplete["tokens"]["output_tokens"] == 15
    assert incomplete["input_output_complete"] is False


@pytest.mark.parametrize("bad", [-1, True, "100", 1.5, 2**70, {}, []])
def test_cli_usage_malformed_counter_is_unknown_not_zero(bad):
    from assistant_workbench.temporal_runtime import parse_cli_usage

    event = {
        "type": "turn.completed",
        "usage": {"input_tokens": bad, "output_tokens": 0},
    }
    parsed = parse_cli_usage(json.dumps(event), "codex")
    assert parsed["status"] == "malformed"
    assert parsed["tokens"]["input_tokens"] is None
    assert parsed["tokens"]["output_tokens"] == 0
    assert not parsed["input_output_complete"]


def test_cli_usage_missing_and_malformed_terminal_event_remains_unknown():
    from assistant_workbench.temporal_runtime import parse_cli_usage

    for text in [
        "",
        "not-json",
        "{}",
        '{"type":"turn.completed"}',
        '{"type":"result","usage":{"input_tokens":100}}',
    ]:
        result = parse_cli_usage(text, "codex")
        assert all(value is None for value in result["tokens"].values())
    text = (
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 10, "output_tokens": 2},
            }
        )
        + '\n{"type":"turn.completed","usage":[]}'
    )
    result = parse_cli_usage(text, "codex")
    assert result["status"] == "malformed"
    assert all(value is None for value in result["tokens"].values())
    inconsistent = parse_cli_usage(
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 2,
                    "cached_input_tokens": 3,
                    "output_tokens": 1,
                    "reasoning_output_tokens": 2,
                },
            }
        ),
        "codex",
    )
    assert inconsistent["tokens"]["cached_input_tokens"] is None
    assert inconsistent["tokens"]["reasoning_output_tokens"] is None


def test_usage_report_deduplicates_sources_observations_and_counts_revisions(
    app, project
):
    from assistant_workbench.temporal_runtime import parse_cli_usage
    from assistant_workbench.util import canonical, now

    fleet, token = registered(app, project)
    fleet.heartbeat("host-a", token)
    request_id = submitted(app, project)
    codex = parse_cli_usage(
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 60,
                    "cache_write_input_tokens": 10,
                    "output_tokens": 40,
                    "reasoning_output_tokens": 15,
                },
            }
        ),
        "codex",
    )
    lease = fleet.admit("host-a", token, request_id, 1, "worker", "process")
    fleet.renew(lease["lease_id"], lease["lease_token"], "fixture-run")
    fleet.release(
        lease["lease_id"],
        lease["lease_token"],
        "completed",
        {"stopped": True, "run_id": "fixture-run", "usage": codex},
    )
    first = {"nodes": {"worker": {"generation": 1, "usage": codex}}}
    with app.store.transaction() as db:
        db.execute(
            "INSERT INTO temporal_executions VALUES(?,?,?,NULL,?,'completed',?,?,?)",
            (request_id, 1, "usage-v1", "hash", canonical(first), now(), now()),
        )
        for _ in range(20):
            db.execute(
                "UPDATE temporal_executions SET result=? WHERE request_id=? AND revision=1",
                (canonical(first), request_id),
            )
    assert fleet.usage()["totals"]["input_tokens"] == 100
    assert fleet.usage()["coverage"]["recorded_nodes"] == 1
    app.store.submit(
        "Expand this research", "usage-amend", reply_to=request_id, expected_revision=1
    )
    claude = parse_cli_usage(
        json.dumps(
            {
                "type": "result",
                "usage": {
                    "input_tokens": 5,
                    "cache_read_input_tokens": 10,
                    "cache_creation_input_tokens": 1,
                    "output_tokens": 15,
                },
            }
        ),
        "claude",
    )
    second = {
        "nodes": {
            "worker": {"generation": 1, "usage": claude},
            "unknown": {},
            "fixture": {"usage": codex, "fixture_only": True},
        }
    }
    with app.store.transaction() as db:
        db.execute(
            "INSERT INTO temporal_executions VALUES(?,?,?,NULL,?,'completed',?,?,?)",
            (request_id, 2, "usage-v2", "hash2", canonical(second), now(), now()),
        )
    report = fleet.usage()
    assert report["totals"]["input_tokens"] == 116
    assert report["totals"]["output_tokens"] == 55
    assert report["totals"]["cached_input_tokens"] == 70
    assert report["metrics"]["reasoning_output_tokens"]["unknown_records"] == 2
    assert report["coverage"]["recorded_nodes"] == 3
    assert report["coverage"]["input_output_complete"] == 2
    assert report["coverage"]["unknown"] == 1
    assert report["coverage"]["excluded_fixture_nodes"] == 1
    assert {
        row["revision"]: row["current_revision"] for row in report["revisions"]
    } == {1: False, 2: True}
    assert (
        fleet.usage(request_id=request_id, revision=2)["totals"]["input_tokens"] == 16
    )
    assert report["cost_usd"] is None and report["subscription_remaining"] is None
    assert report["capacity"]["configured_slots"] == 2


def test_usage_report_conflicts_unknown_generations_and_read_only(app, project):
    from assistant_workbench.temporal_runtime import parse_cli_usage
    from assistant_workbench.util import canonical, now

    fleet, token = registered(app, project)
    fleet.heartbeat("host-a", token)
    request_id = submitted(app, project)
    usage = parse_cli_usage(
        '{"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":20}}',
        "codex",
    )
    lease = fleet.admit("host-a", token, request_id, 1, "worker", "process")
    fleet.renew(lease["lease_id"], lease["lease_token"], "run")
    fleet.release(
        lease["lease_id"],
        lease["lease_token"],
        "completed",
        {"run_id": "run", "stopped": True, "usage": usage},
    )
    conflicting = copy.deepcopy(usage)
    conflicting["tokens"]["input_tokens"] = 101
    with app.store.transaction() as db:
        result = {
            "nodes": {
                "worker": {"generation": 1, "usage": conflicting},
                "rollover": {"generation": 3},
            }
        }
        db.execute(
            "INSERT INTO temporal_executions VALUES(?,?,?,NULL,?,'completed',?,?,?)",
            (request_id, 1, "usage-conflict", "hash", canonical(result), now(), now()),
        )
    report = fleet.usage()
    assert report["totals"]["input_tokens"] is None
    assert report["totals"]["output_tokens"] == 20
    assert report["coverage"]["conflicting"] == 1
    assert report["coverage"]["unrepresented_prior_generations"] == 2
    pending = fleet.admit("host-a", token, request_id, 1, "pending", "process")
    with app.store.transaction() as db:
        db.execute(
            "UPDATE runtime_leases SET expires=0 WHERE id=?", (pending["lease_id"],)
        )
    assert fleet.usage()["capacity"]["occupied_slots"] == 1
    assert (
        app.store.one(
            "SELECT state FROM runtime_leases WHERE id=?", (pending["lease_id"],)
        )["state"]
        == "leased"
    )


def test_usage_report_without_observations_never_invents_zero_usage(app):
    report = Fleet(app.store).usage()
    assert all(value is None for value in report["totals"].values())
    assert report["coverage"]["recorded_nodes"] == 0
    assert report["cost_usd"] is None


@pytest.mark.asyncio
async def test_claude_worker_runs_isolated_with_edit_permission_and_profile_grants(
    app, project, configured, tmp_path, monkeypatch
):
    """Fixture CLI records its arguments; proves the invocation, never an LLM result."""
    pytest.importorskip("temporalio")
    from assistant_workbench import profiles, temporal_runtime
    from assistant_workbench.temporal_runtime import OfficialCLIExecutor
    from assistant_workbench.util import canonical, digest, now

    argv_log = tmp_path / "argv.json"
    executable = tmp_path / "claude"
    executable.write_text(
        "#!/usr/bin/env python3\nimport json,os,sys\nsys.stdin.read()\n"
        "open(os.environ['ARGV_LOG'],'w').write(json.dumps(sys.argv[1:]))\n"
        "print(json.dumps({'type':'result','subtype':'success','is_error':False,'result':'CLAUDE ARGV FIXTURE',"
        "'usage':{'input_tokens':5,'output_tokens':2}}))\n"
    )
    executable.chmod(0o700)
    monkeypatch.setenv("ARGV_LOG", str(argv_log))
    monkeypatch.setattr(temporal_runtime.shutil, "which", lambda name: str(executable))
    monkeypatch.setattr(profiles, "readiness", lambda model: (True, "TEST MOCK ONLY"))
    monkeypatch.setattr(temporal_runtime.activity, "heartbeat", lambda *args: None)
    model = {"id": "claude-fixture", "harness": "claude", "model": "fixture", "effort": "low",
             "auth": "subscription",
             "adapter_config": {"extraArgs": ["--allowedTools", "Bash(python -m pytest:*)"]}}
    settings = copy.deepcopy(configured)
    settings["models"] = [model]
    fleet = Fleet(app.store)
    token = fleet.register("host-a", "Fixture worker", "queue-a", 2, ["claude"],
                           {project["id"]: project["repository"]})["token"]
    fleet.heartbeat("host-a", token)
    executor = OfficialCLIExecutor(app.store, settings, "host-a", token)

    def packet_for(key):
        request_id = app.store.submit("Fix the bug", key, project["id"])["request_id"]
        packet = {"request_id": request_id, "revision": 1, "project_id": project["id"],
                  "body": "Fix the bug", "generation": 1, "node": {"id": "worker", "role": "engineer"},
                  "plan": {"model": model, "limits": settings["limits"], "skills": []},
                  "deadline": now() + 60}
        packet["payload_hash"] = digest(canonical(packet))
        with app.store.transaction() as db:
            db.execute("INSERT INTO temporal_executions VALUES(?,?,?,NULL,?,'running',NULL,?,?)",
                       (request_id, 1, "fixture-" + request_id, packet["payload_hash"], now(), now()))
        return packet

    result = await executor.execute(packet_for("argv"))
    assert result["body"] == "CLAUDE ARGV FIXTURE"
    argv = json.loads(argv_log.read_text())
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits" and "--bare" in argv
    assert "Bash(python -m pytest:*)" in argv and "Bash(git status:*)" in argv
    assert not any("commit" in a or "push" in a for a in argv)
    # A profile that would bypass permissions never starts a process.
    argv_log.unlink()
    model["adapter_config"] = {"extraArgs": ["--permission-mode", "bypassPermissions"]}
    with pytest.raises(WorkbenchError, match="bypass"):
        await executor.execute(packet_for("bypass"))
    assert not argv_log.exists()

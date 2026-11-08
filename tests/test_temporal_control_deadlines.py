"""Controller fault injection, not evidence of live models or a network partition."""

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from assistant_workbench import temporal_runtime
from assistant_workbench.runtime_backends import Fleet
from assistant_workbench.temporal_runtime import TemporalRuntime
from assistant_workbench.util import WorkbenchError, now


@pytest.fixture
def control(app, project, configured, monkeypatch):
    pytest.importorskip("temporalio")
    monkeypatch.setattr(temporal_runtime, "CONTROL_RPC_SECONDS", 0.05)
    settings = copy.deepcopy(configured)
    settings["coordinator"] = {
        "kind": "temporal", "target": "127.0.0.1:7233", "host_id": "fault-host",
    }
    fleet = Fleet(app.store)
    host = fleet.register(
        "fault-host", "Fault injection", "fault-queue", 2, ["process"],
        {project["id"]: project["repository"]}, 0,
    )
    fleet.heartbeat("fault-host", host["token"])
    request_id = app.store.submit("Inspect this project", "fault-test", project["id"])["request_id"]
    handle = SimpleNamespace(describe=AsyncMock(), cancel=AsyncMock(), result=AsyncMock())
    client = SimpleNamespace(
        start_workflow=AsyncMock(return_value=handle),
        get_workflow_handle=Mock(return_value=handle),
    )
    runtime = TemporalRuntime(app.store, settings, client)
    # This test targets controller responses after admission. The existing
    # real-service suite covers actual queue/poller readiness independently.
    runtime._require_poller = AsyncMock()
    return runtime, client, handle, request_id


def seed_execution(app, request_id, state="running"):
    with app.store.transaction() as db:
        db.execute(
            "INSERT INTO temporal_executions VALUES(?,?,?,NULL,?,?,NULL,?,?)",
            (request_id, 1, "known-workflow", "test-hash", state, now(), now()),
        )


async def unavailable(**kwargs):
    assert kwargs["rpc_timeout"].total_seconds() == 0.05
    await asyncio.Event().wait()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["timeout", "error", "memo_timeout"])
async def test_dispatch_lost_verification_is_bounded_and_uncertain(app, control, fault):
    runtime, client, handle, request_id = control
    if fault == "timeout":
        handle.describe.side_effect = unavailable
    elif fault == "error":
        handle.describe.side_effect = RuntimeError("PRIVATE_CONNECTION_DETAILS")
    else:
        async def stalled_memo():
            await asyncio.Event().wait()
        handle.describe.return_value = SimpleNamespace(memo=stalled_memo)
    with pytest.raises(WorkbenchError, match="reconcile") as failure:
        await asyncio.wait_for(runtime.a_submit(request_id, kind="simple"), 1)
    assert "PRIVATE_CONNECTION_DETAILS" not in str(failure.value)
    row = app.store.one("SELECT * FROM temporal_executions WHERE request_id=?", (request_id,))
    assert row["state"] == "uncertain"
    assert client.start_workflow.await_count == 1
    assert handle.describe.await_count == 1
    assert not handle.cancel.called


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["timeout", "error"])
async def test_lost_cancellation_reply_never_reports_stopped(app, control, fault):
    runtime, client, handle, request_id = control
    seed_execution(app, request_id)
    handle.cancel.side_effect = unavailable if fault == "timeout" else RuntimeError("PRIVATE_TOKEN")
    with pytest.raises(WorkbenchError, match="cancellation") as failure:
        await asyncio.wait_for(runtime.a_cancel(request_id), 1)
    assert "PRIVATE_TOKEN" not in str(failure.value)
    row = app.store.one("SELECT * FROM temporal_executions WHERE request_id=?", (request_id,))
    assert row["state"] == "uncertain"
    assert handle.cancel.await_count == 1
    assert not client.start_workflow.called


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["describe", "result"])
async def test_unavailable_observation_retains_last_known_state(app, control, operation):
    runtime, client, handle, request_id = control
    seed_execution(app, request_id)
    handle.describe.return_value = SimpleNamespace(status=SimpleNamespace(name="COMPLETED"), run_id="run-1")
    getattr(handle, operation).side_effect = unavailable
    with pytest.raises(WorkbenchError, match="reconcile"):
        await asyncio.wait_for(runtime.a_observe(request_id), 1)
    row = app.store.one("SELECT * FROM temporal_executions WHERE request_id=?", (request_id,))
    assert row["state"] == "running" and row["result"] is None
    assert not client.start_workflow.called and not handle.cancel.called


@pytest.mark.asyncio
async def test_cancelled_controller_preserves_uncertainty(app, control):
    runtime, client, handle, request_id = control
    seed_execution(app, request_id)
    entered = asyncio.Event()

    async def pending(**kwargs):
        entered.set()
        await asyncio.Event().wait()

    handle.cancel.side_effect = pending
    task = asyncio.create_task(runtime.a_cancel(request_id))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    row = app.store.one("SELECT * FROM temporal_executions WHERE request_id=?", (request_id,))
    assert row["state"] == "uncertain"
    assert handle.cancel.await_count == 1 and not client.start_workflow.called

"""Optional Temporal coordinator and real official-CLI worker.

Install temporalio==1.35.0 only when this backend is selected. Temporal owns the
DAG and worker queue; Paperclip is not run alongside it. Workflow completion
produces an unreviewed result, never a fabricated acceptance or qualification.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import urllib.parse

from .runtime_backends import Fleet, WorkerControl, USAGE_FIELDS, select_runtime
from .util import WorkbenchError, atomic, canonical, digest, now

# Bound controller requests independently of workflow execution budgets. A lost
# reply must not leave the HTTP/TUI control loop waiting indefinitely.
CONTROL_RPC_SECONDS = 10

try:
    from temporalio import activity, workflow
    from temporalio.client import Client
    from temporalio.common import (
        RetryPolicy,
        WorkflowIDConflictPolicy,
        WorkflowIDReusePolicy,
    )
    from temporalio.exceptions import (
        WorkflowAlreadyStartedError,
        ActivityError,
        CancelledError,
    )
    from temporalio.service import TLSConfig
    from temporalio.worker import Worker

    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False


def require_sdk():
    if not SDK_AVAILABLE:
        raise WorkbenchError(
            "Temporal is selected but its SDK is missing; install temporalio==1.35.0 in this environment"
        )


def parse_cli_usage(text, harness):
    """Read one-shot CLI terminal counters; never sum cumulative stream snapshots.

    Codex input includes cached input. Claude input excludes its cache buckets,
    so normalized total input is unknown unless all three buckets are reported.
    No billing or subscription allowance is inferred from these counters.
    """
    source = {"codex": "codex.turn.completed", "claude": "claude.result"}.get(harness)
    result = {
        "schema": 1,
        "source": source,
        "status": "unknown",
        "input_output_complete": False,
        "tokens": {field: None for field in USAGE_FIELDS},
        "reported_input_tokens": None,
        "terminal_events": 0,
        "missing_fields": list(USAGE_FIELDS),
        "malformed_fields": [],
        "scope": "CLI terminal usage only; nested agents or interrupted runs may be incomplete.",
        "cost_usd": None,
        "subscription_remaining": None,
    }
    if not source or not isinstance(text, str):
        return result
    last = None
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(event, dict) and event.get("type") == source.split(".", 1)[1]:
            result["terminal_events"] += 1
            last = event
    if last is None or last.get("usage") is None:
        return result
    usage = last["usage"]
    if not isinstance(usage, dict):
        result.update(status="malformed", malformed_fields=["usage"])
        return result
    malformed = []

    def number(key):
        value = usage.get(key)
        if value is None:
            return None
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and 0 <= value <= 2**63 - 1
        ):
            return value
        malformed.append(key)
        return None

    raw_input, output = number("input_tokens"), number("output_tokens")
    cached = number(
        "cached_input_tokens" if harness == "codex" else "cache_read_input_tokens"
    )
    written = number(
        "cache_write_input_tokens"
        if harness == "codex"
        else "cache_creation_input_tokens"
    )
    reasoning = number("reasoning_output_tokens")
    total_input = raw_input
    if harness == "claude":
        total_input = (
            sum((raw_input, cached, written))
            if all(v is not None for v in (raw_input, cached, written))
            else None
        )
    if total_input is not None and total_input > 2**63 - 1:
        malformed.append("total_input_tokens")
        total_input = None
    if (
        harness == "codex"
        and raw_input is not None
        and cached is not None
        and cached > raw_input
    ):
        malformed.append("cached_input_tokens")
        cached = None
    if reasoning is not None and output is not None and reasoning > output:
        malformed.append("reasoning_output_tokens")
        reasoning = None
    tokens = dict(zip(USAGE_FIELDS, (total_input, output, cached, written, reasoning)))
    missing = [field for field, value in tokens.items() if value is None]
    complete = total_input is not None and output is not None
    result.update(
        tokens=tokens,
        reported_input_tokens=raw_input,
        input_output_complete=complete,
        missing_fields=missing,
        malformed_fields=malformed,
        status=(
            "malformed"
            if malformed
            else (
                "reported"
                if complete
                else (
                    "partial"
                    if any(v is not None for v in tokens.values())
                    else "unknown"
                )
            )
        ),
        input_accounting=(
            "Input includes cache; cache counters are a breakdown."
            if harness == "codex"
            else "Input sums uncached, cache-read and cache-creation counters; cache counters are a breakdown."
        ),
        reported_error=last.get("is_error") is True,
    )
    return result


def plan_layers(nodes, max_agents):
    """Deterministic topological batches; malformed graphs fail before dispatch."""
    if not isinstance(nodes, list) or not nodes or len(nodes) > max_agents:
        raise WorkbenchError("Temporal plan is empty or exceeds its agent budget")
    graph = {n["id"]: n for n in nodes}
    if len(graph) != len(nodes):
        raise WorkbenchError("Duplicate Temporal node identity")
    if any(not set(n.get("depends_on", [])).issubset(graph) for n in nodes):
        raise WorkbenchError("Temporal plan has a missing dependency")
    done, layers = set(), []
    while len(done) < len(nodes):
        batch = [
            n
            for n in nodes
            if n["id"] not in done and set(n.get("depends_on", [])).issubset(done)
        ]
        if not batch:
            raise WorkbenchError("Temporal plan has a dependency cycle")
        layers.append(batch)
        done.update(n["id"] for n in batch)
    return layers


if SDK_AVAILABLE:

    @workflow.defn(name="awb.plan.v1")
    class WorkbenchPlanWorkflow:
        def __init__(self):
            self.state = {"phase": "starting", "nodes": {}}
            self.handles = {}
            self.rollovers = {}

        @workflow.query(name="snapshot")
        def snapshot(self):
            return self.state

        @workflow.signal(name="rollover")
        def rollover(self, recovery: dict):
            node = recovery["node"]
            current = self.state["nodes"].get(node, {})
            if recovery["generation"] <= current.get("generation", 0):
                return
            if node not in self.handles or current.get("status") not in {
                "scheduled",
                "quiescing",
            }:
                return
            self.rollovers[node] = recovery
            current["status"] = "quiescing"
            self.handles[node].cancel()

        async def run_node(self, packet, node, prior_results):
            generation, checkpoint = 1, None
            while True:
                self.state["nodes"][node["id"]] = {
                    "status": "scheduled",
                    "role": node["role"],
                    "host_id": packet["host_id"],
                    "generation": generation,
                }
                handle = workflow.start_activity(
                    "awb.run_node.v1",
                    {
                        **packet,
                        "node": node,
                        "prior_results": prior_results,
                        "generation": generation,
                        "checkpoint": checkpoint,
                    },
                    task_queue=packet["worker_queue"],
                    activity_id=node["id"] + "-g" + str(generation),
                    start_to_close_timeout=timedelta(
                        seconds=packet["plan"]["limits"]["max_seconds"]
                    ),
                    schedule_to_close_timeout=timedelta(
                        seconds=packet["plan"]["limits"]["max_seconds"]
                    ),
                    heartbeat_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=1),
                    cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                )
                self.handles[node["id"]] = handle
                try:
                    result = await handle
                    self.state["nodes"][node["id"]]["status"] = "done"
                    return result
                except (ActivityError, CancelledError):
                    recovery = self.rollovers.pop(node["id"], None)
                    if (
                        not recovery
                        or recovery["generation"] != generation + 1
                        or recovery["generation"]
                        > packet["plan"]["limits"]["max_iterations"]
                    ):
                        raise
                    generation, checkpoint = (
                        recovery["generation"],
                        recovery["checkpoint"],
                    )
                finally:
                    self.handles.pop(node["id"], None)

        @workflow.run
        async def run(self, packet: dict) -> dict:
            self.state.update(
                request_id=packet["request_id"],
                revision=packet["revision"],
                payload_hash=packet["payload_hash"],
                phase="running",
            )
            results = {}
            try:
                for batch in plan_layers(
                    packet["plan"]["nodes"], packet["plan"]["limits"]["max_agents"]
                ):
                    tasks = [
                        asyncio.create_task(
                            self.run_node(
                                packet,
                                node,
                                {k: results[k] for k in node["depends_on"]},
                            )
                        )
                        for node in batch
                    ]
                    try:
                        completed = await asyncio.gather(*tasks)
                    except BaseException:
                        for handle in self.handles.values():
                            handle.cancel()
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                        raise
                    results.update(
                        {node["id"]: result for node, result in zip(batch, completed)}
                    )
                self.state["phase"] = "needs_review"
                return {
                    "request_id": packet["request_id"],
                    "revision": packet["revision"],
                    "status": "needs_review",
                    "nodes": results,
                    "notice": "Real worker results still require source checks and independent outcome review.",
                }
            except BaseException:
                self.state["phase"] = "stopped_or_failed"
                raise


class TemporalRuntime:
    def __init__(self, store, settings=None, client=None):
        from .config import config

        self.store = store
        self.settings = settings or config(store.home)
        select_runtime(store, self.settings, "temporal")
        self.spec = self.settings["coordinator"]
        self.client = client
        target = self.spec.get("target", "127.0.0.1:7233")
        parsed = urllib.parse.urlsplit("//" + target)
        if (
            parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
            or not parsed.hostname
        ):
            raise WorkbenchError(
                "Temporal target must be host:port without credentials or a URL path"
            )
        if parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        } and not self.spec.get("tls"):
            raise WorkbenchError("A remote Temporal service requires TLS")
        self.target = target

    async def connect(self):
        if self.client is not None:
            return self.client
        require_sdk()
        tls = bool(self.spec.get("tls", False))
        if self.spec.get("client_cert") or self.spec.get("client_key"):
            if not self.spec.get("client_cert") or not self.spec.get("client_key"):
                raise WorkbenchError(
                    "Temporal mTLS requires both certificate and private-key paths"
                )
            tls = TLSConfig(
                client_cert=Path(self.spec["client_cert"]).read_bytes(),
                client_private_key=Path(self.spec["client_key"]).read_bytes(),
            )
        key_env = self.spec.get("api_key_env")
        api_key = os.environ.get(key_env) if key_env else None
        if key_env and not api_key:
            raise WorkbenchError("Temporal API-key environment variable is not set")
        try:
            self.client = await asyncio.wait_for(
                Client.connect(
                    self.target,
                    namespace=self.spec.get("namespace", "default"),
                    tls=tls,
                    api_key=api_key,
                ),
                timeout=10,
            )
        except Exception as exc:
            raise WorkbenchError(
                "Temporal service connection failed; start the configured server or check TLS/authentication"
            ) from exc
        return self.client

    async def a_health(self):
        client = await self.connect()
        try:
            await asyncio.wait_for(client.service_client.check_health(), timeout=10)
        except Exception as exc:
            raise WorkbenchError("Temporal health check did not succeed") from exc
        return {
            "kind": "temporal",
            "target": self.target,
            "reachable": True,
            "worker_readiness": "Requires a registered host heartbeat and an actual task result",
        }

    async def _control_rpc(self, operation, method, **kwargs):
        try:
            return await asyncio.wait_for(
                method(rpc_timeout=timedelta(seconds=CONTROL_RPC_SECONDS), **kwargs),
                timeout=CONTROL_RPC_SECONDS,
            )
        except Exception as exc:
            # Provider exceptions can contain connection details. Keep the
            # operator message actionable without publishing their raw text.
            raise WorkbenchError(
                "Temporal " + operation + " did not complete; reconcile the existing workflow before retrying"
            ) from exc

    async def _cancel_execution(self, client, request_id, revision, workflow_id, run_id):
        handle = client.get_workflow_handle(workflow_id, run_id=run_id)
        try:
            await self._control_rpc("cancellation", handle.cancel)
        except (Exception, asyncio.CancelledError):
            # The server may have accepted the request before its reply was
            # lost. Neither a failed RPC nor caller cancellation proves stop.
            self._state(request_id, revision, "uncertain", run_id=run_id)
            raise
        self._state(request_id, revision, "cancel_requested", run_id=run_id)

    async def _require_poller(self, client, queue):
        from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest
        from temporalio.api.taskqueue.v1 import TaskQueue
        from temporalio.api.enums.v1 import TaskQueueType

        request = DescribeTaskQueueRequest(
            namespace=self.spec.get("namespace", "default"),
            task_queue=TaskQueue(name=queue),
            task_queue_type=TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY,
        )
        for attempt in range(5):
            response = await client.workflow_service.describe_task_queue(
                request, timeout=timedelta(seconds=5)
            )
            if any(
                0
                <= now()
                - p.last_access_time.ToDatetime()
                .replace(tzinfo=timezone.utc)
                .timestamp()
                < 60
                for p in response.pollers
            ):
                return
            if attempt != 4:
                await asyncio.sleep(0.25)
        raise WorkbenchError(
            "No recent Temporal activity poller on the configured host queue; start temporal-worker first"
        )

    async def a_submit(self, request_id, kind=None, model_override=None, host_id=None):
        from .policy import compile_plan, validate_plan

        require_sdk()
        req = self.store.one("SELECT * FROM requests WHERE id=?", (request_id,))
        if req["status"] in {"cancelled", "accepted", "grouped", "needs_project"}:
            raise WorkbenchError("Only an active assigned request can be dispatched")
        if req.get("plan"):
            plan = json.loads(req["plan"])
        else:
            from .routing import workflow_choice

            choice = None if kind else workflow_choice(self.store, self.settings, req)
            plan = compile_plan(
                req, self.settings, choice[0] if choice else kind, model_override,
                routing=choice[1] if choice else None,
            )
        validate_plan(plan, self.settings)
        plan_layers(plan["nodes"], self.settings["limits"]["max_agents"])
        host_id = host_id or self.spec.get("host_id", "local")
        hosts = Fleet(self.store).status()["hosts"]
        host = next((x for x in hosts if x["id"] == host_id), None)
        if not host or not host["alive"] or not host["enabled"]:
            raise WorkbenchError(
                "Configured Temporal worker host has no recent heartbeat"
            )
        if (
            req["project_id"] not in host["workspaces"]
            or plan["model"]["harness"] not in host["capabilities"]
        ):
            raise WorkbenchError(
                "Worker host lacks this project mapping or harness capability"
            )
        # Each worker uses its own local qualification; no credential files enter Temporal history.
        public_model = {
            key: plan["model"][key]
            for key in ("id", "harness", "model", "effort", "auth")
        }
        public_plan = {
            **plan,
            "model": public_model,
            "skills": [
                {k: v for k, v in s.items() if k != "path"} for s in plan["skills"]
            ],
        }
        packet = {
            "request_id": req["id"],
            "revision": req["revision"],
            "project_id": req["project_id"],
            "body": req["body"],
            "plan": public_plan,
            "host_id": host_id,
            "worker_queue": host["task_queue"],
            "deadline": req["created"] + self.settings["limits"]["max_seconds"],
        }
        if len(canonical(packet)) > self.settings["limits"]["context_chars"]:
            raise WorkbenchError(
                "Temporal execution packet exceeds the configured context budget"
            )
        payload_hash = digest(canonical(packet))
        packet["payload_hash"] = payload_hash
        workflow_id = "awb-" + req["id"] + "-v" + str(req["revision"])
        client = await self.connect()
        if packet["deadline"] <= now():
            raise WorkbenchError("Cumulative request execution budget exhausted")
        await self._require_poller(client, host["task_queue"])
        predecessors = self.store.rows(
            "SELECT * FROM temporal_executions WHERE request_id=? AND revision<? AND state IN ('starting','running','uncertain','cancel_requested')",
            (request_id, req["revision"]),
        )
        if predecessors:
            for previous in predecessors:
                await self._cancel_execution(
                    client, request_id, previous["revision"],
                    previous["workflow_id"], previous["run_id"],
                )
            raise WorkbenchError(
                "Previous revision cancellation requested; observe its stop before dispatching the replacement"
            )
        if self.store.rows(
            "SELECT 1 FROM runtime_leases WHERE request_id=? AND revision<? AND state IN ('leased','running','cancel_requested','uncertain')",
            (request_id, req["revision"]),
        ):
            raise WorkbenchError(
                "Previous revision still has an unconfirmed process lease"
            )
        with self.store.transaction() as db:
            current = db.execute(
                "SELECT revision,status FROM requests WHERE id=?", (request_id,)
            ).fetchone()
            if current["revision"] != req["revision"] or current["status"] in {
                "cancelled",
                "accepted",
                "grouped",
            }:
                raise WorkbenchError("Request changed before Temporal dispatch")
            previous = db.execute(
                "SELECT * FROM temporal_executions WHERE request_id=? AND revision=?",
                (request_id, req["revision"]),
            ).fetchone()
            if previous and previous["payload_hash"] != payload_hash:
                raise WorkbenchError(
                    "A different plan already owns this request revision"
                )
            if previous and previous["state"] in {
                "completed",
                "failed",
                "cancelled",
                "terminated",
                "timed_out",
            }:
                return {
                    "workflow_id": previous["workflow_id"],
                    "run_id": previous["run_id"],
                    "status": previous["state"],
                    "duplicate": True,
                }
            db.execute(
                "INSERT OR IGNORE INTO temporal_executions VALUES(?,?,?,NULL,?,'starting',NULL,?,?)",
                (request_id, req["revision"], workflow_id, payload_hash, now(), now()),
            )
            db.execute(
                "UPDATE requests SET plan=? WHERE id=? AND revision=?",
                (canonical(plan), request_id, req["revision"]),
            )
        try:
            handle = await client.start_workflow(
                "awb.plan.v1",
                packet,
                id=workflow_id,
                task_queue=self.spec.get("task_queue", "awb-control"),
                execution_timeout=timedelta(seconds=max(1, packet["deadline"] - now())),
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                retry_policy=RetryPolicy(maximum_attempts=1),
                memo={"awb_payload_hash": payload_hash},
                rpc_timeout=timedelta(seconds=15),
            )
        except WorkflowAlreadyStartedError:
            handle = client.get_workflow_handle(workflow_id)
        except asyncio.CancelledError:
            self._state(request_id, req["revision"], "uncertain")
            raise
        except Exception as exc:
            self._state(request_id, req["revision"], "uncertain")
            raise WorkbenchError(
                "Temporal start has no confirmed response; reconcile by the same workflow ID before retrying"
            ) from exc
        try:
            description = await self._control_rpc("dispatch verification", handle.describe)
            memo = await asyncio.wait_for(description.memo(), timeout=CONTROL_RPC_SECONDS)
        except asyncio.CancelledError:
            self._state(request_id, req["revision"], "uncertain")
            raise
        except Exception as exc:
            self._state(request_id, req["revision"], "uncertain")
            raise WorkbenchError(
                "Temporal dispatch verification did not complete; reconcile the existing workflow before retrying"
            ) from exc
        if memo.get("awb_payload_hash") != payload_hash:
            self._state(request_id, req["revision"], "uncertain")
            raise WorkbenchError(
                "Existing Temporal execution has a different request hash"
            )
        run_id = description.run_id
        current = self.store.one(
            "SELECT revision,status FROM requests WHERE id=?", (request_id,)
        )
        if current["revision"] != req["revision"] or current["status"] in {
            "cancelled",
            "accepted",
            "grouped",
        }:
            await self._cancel_execution(
                client, request_id, req["revision"], workflow_id, run_id,
            )
            raise WorkbenchError(
                "Request changed during dispatch; cancellation sent, stop confirmation pending"
            )
        with self.store.transaction() as db:
            db.execute(
                "UPDATE temporal_executions SET state='running',run_id=?,updated=? WHERE request_id=? AND revision=?",
                (run_id, now(), request_id, req["revision"]),
            )
            db.execute(
                "UPDATE requests SET status='running',remote_id=?,updated=? WHERE id=? AND revision=?",
                (workflow_id, now(), request_id, req["revision"]),
            )
            self.store.event(
                db,
                "temporal.submitted",
                request_id,
                {
                    "workflow_id": workflow_id,
                    "run_id": run_id,
                    "revision": req["revision"],
                },
            )
        return {"workflow_id": workflow_id, "run_id": run_id, "status": "running"}

    def _state(self, request_id, revision, state, result=None, run_id=None):
        with self.store.transaction() as db:
            db.execute(
                "UPDATE temporal_executions SET state=?,result=COALESCE(?,result),run_id=COALESCE(?,run_id),updated=? WHERE request_id=? AND revision=?",
                (
                    state,
                    canonical(result) if result is not None else None,
                    run_id,
                    now(),
                    request_id,
                    revision,
                ),
            )

    async def a_cancel(self, request_id):
        client = await self.connect()
        rows = self.store.rows(
            "SELECT * FROM temporal_executions WHERE request_id=? AND state IN ('starting','running','uncertain','cancel_requested')",
            (request_id,),
        )
        cancelled = []
        for row in rows:
            await self._cancel_execution(
                client, request_id, row["revision"], row["workflow_id"], row["run_id"],
            )
            cancelled.append(row["workflow_id"])
        return {
            "request_id": request_id,
            "cancel_requested": cancelled,
            "stopped": False,
            "notice": "Cancellation is a request; observe terminal workflow and process-stop receipts before replacement work.",
        }

    async def a_observe(self, request_id=None):
        client = await self.connect()
        sql = "SELECT * FROM temporal_executions"
        rows = self.store.rows(
            sql + (" WHERE request_id=?" if request_id else ""),
            (request_id,) if request_id else (),
        )
        result = {}
        for row in rows:
            handle = client.get_workflow_handle(
                row["workflow_id"], run_id=row["run_id"]
            )
            description = await self._control_rpc("status observation", handle.describe)
            state = description.status.name.lower().replace("canceled", "cancelled")
            detail = {
                "workflow_id": row["workflow_id"],
                "run_id": description.run_id,
                "status": state,
                "revision": row["revision"],
            }
            if state == "completed":
                detail["result"] = await self._control_rpc("result retrieval", handle.result)
                self._state(
                    row["request_id"],
                    row["revision"],
                    state,
                    detail["result"],
                    description.run_id,
                )
            else:
                self._state(
                    row["request_id"], row["revision"], state, run_id=description.run_id
                )
                if state == "running":
                    try:
                        detail["snapshot"] = await handle.query(
                            "snapshot", rpc_timeout=timedelta(seconds=5)
                        )
                    except Exception:
                        detail["snapshot"] = {"phase": "awaiting_worker", "nodes": {}}
            result[row["request_id"] + ":" + str(row["revision"])] = detail
        return result

    async def a_rollover(self, request_id, node):
        """Request a fresh CLI activity after a validated durable checkpoint.

        Temporal cancellation acknowledgement and the central process lease gate
        the successor. This explicit handoff does not infer a model's context use.
        """
        from .continuation import Continuation

        recovered = Continuation(self.store, self.settings["limits"]).recover(
            request_id
        )
        req = recovered["request"]
        if (
            now() - req["created"] >= self.settings["limits"]["max_seconds"]
            or recovered["next_generation"] > self.settings["limits"]["max_iterations"]
        ):
            raise WorkbenchError("Continuation budget exhausted")
        if self.store.rows(
            "SELECT id FROM effects WHERE request_id=? AND state='uncertain'",
            (request_id,),
        ):
            raise WorkbenchError(
                "Reconcile uncertain effects before requesting a fresh session"
            )
        from .context import Context

        for artifact in recovered["checkpoint"].get("artifacts", []):
            if artifact.get("source_id"):
                source = Context(self.store).source(
                    req["project_id"], artifact["source_id"]
                )
                matches = source["hash"] == artifact.get("sha256")
            else:
                path = Path(artifact.get("path", "")).resolve(strict=True)
                repository = self.store.project(req["project_id"])["repository"]
                matches = bool(
                    repository
                    and path.is_relative_to(Path(repository).resolve())
                    and path.is_file()
                    and digest(path.read_bytes()) == artifact.get("sha256")
                )
            if not matches:
                raise WorkbenchError(
                    "Checkpoint artifact changed since it was recorded"
                )
        plan = json.loads(req["plan"] or "{}")
        if node not in {n["id"] for n in plan.get("nodes", [])}:
            raise WorkbenchError("Node does not belong to the active execution plan")
        execution = self.store.one(
            "SELECT * FROM temporal_executions WHERE request_id=? AND revision=?",
            (request_id, req["revision"]),
        )
        if execution["state"] != "running":
            raise WorkbenchError(
                "Only a running Temporal execution can hand off context"
            )
        client = await self.connect()
        handle = client.get_workflow_handle(
            execution["workflow_id"], run_id=execution["run_id"]
        )
        snapshot = await handle.query("snapshot", rpc_timeout=timedelta(seconds=5))
        current = snapshot.get("nodes", {}).get(node, {})
        if current.get("status") not in {"scheduled", "quiescing"}:
            raise WorkbenchError("This node has no active activity to hand off")
        generation = recovered["next_generation"]
        if generation != current.get("generation", 0) + 1:
            raise WorkbenchError(
                "Checkpoint generation does not match this active node"
            )
        # The signal ID/generation is deterministic. A lost response is retried with
        # the same generation; workflow history ignores already-applied signals.
        await handle.signal(
            "rollover",
            {
                "node": node,
                "generation": generation,
                "checkpoint": recovered["checkpoint"],
            },
            rpc_timeout=timedelta(seconds=10),
        )
        with self.store.transaction() as db:
            self.store.event(
                db,
                "temporal.rollover_requested",
                request_id,
                {"node": node, "generation": generation, "revision": req["revision"]},
            )
        return {
            "request_id": request_id,
            "node": node,
            "generation": generation,
            "state": "quiescing",
            "notice": "Fresh activity waits for cancellation; central leases require the prior process to stop.",
        }

    async def a_tick(self):
        # A single local outbox owner; Temporal alone schedules the actual DAG.
        with open(self.store.home / "bridge.lock", "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"status": "busy"}
            try:
                from .engine import Engine

                job = Engine.claim(self)
                if not job:
                    return None
                req = self.store.one(
                    "SELECT * FROM requests WHERE id=?", (job["request_id"],)
                )
                try:
                    if job["operation"] == "cancel":
                        result = await self.a_cancel(req["id"])
                    elif req["revision"] != job["revision"] or req["status"] in {
                        "cancelled",
                        "accepted",
                        "grouped",
                    }:
                        result = {"status": "superseded"}
                    else:
                        result = await self.a_submit(req["id"])
                    state, error = (
                        "superseded"
                        if result.get("status") == "superseded"
                        else "delivered"
                    ), None
                except WorkbenchError as exc:
                    state, error = "blocked", str(exc)
                    if self.store.rows(
                        "SELECT 1 FROM temporal_executions WHERE request_id=? AND revision=? AND state='uncertain'",
                        (req["id"], job["revision"]),
                    ):
                        state = "uncertain"
                    result = None
                with self.store.transaction() as db:
                    db.execute(
                        "UPDATE outbox SET status=?,error=?,lease=NULL WHERE id=?",
                        (state, error, job["id"]),
                    )
                    self.store.event(
                        db,
                        "delivery." + state,
                        req["id"],
                        {"coordinator": "temporal", "error": error},
                    )
                return {
                    "id": job["id"],
                    "status": state,
                    "error": error,
                    "result": result,
                }
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _sync(self, awaitable):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            try:
                return asyncio.run(awaitable)
            finally:
                self.client = None  # SDK clients must not be reused across closed sync event loops.
        awaitable.close()
        raise WorkbenchError(
            "Use the async Temporal method inside an active event loop"
        )

    def submit(self, **args):
        return self._sync(self.a_submit(**args))

    def cancel(self, **args):
        return self._sync(self.a_cancel(**args))

    def observe(self, **args):
        return self._sync(self.a_observe(**args))

    def health(self):
        return self._sync(self.a_health())

    def rollover(self, request_id, node):
        return self._sync(self.a_rollover(request_id, node))

    def tick(self):
        return self._sync(self.a_tick())


class OfficialCLIExecutor:
    """Bounded official CLI activity with durable process receipts.

    Central admission and revision checks use a host-scoped control client.
    Remote workers keep only their own process receipts and model qualification.
    """

    def __init__(self, store, settings, host_id, host_token, control=None):
        self.store, self.settings = store, settings
        self.host_id, self.host_token = host_id, host_token
        self.control = control or WorkerControl(host_id, host_token, store=store)

    async def execute(self, packet):
        from .profiles import readiness

        node = packet["node"]
        expected = packet["plan"]["model"]
        model = next(
            (m for m in self.settings["models"] if m["id"] == expected["id"]), None
        )
        if not model or any(
            model.get(k) != expected.get(k)
            for k in ("id", "harness", "model", "effort", "auth")
        ):
            raise WorkbenchError(
                "Worker model profile differs from the dispatched profile"
            )
        ready, reason = readiness(model)
        if not ready or model["harness"] not in {"claude", "codex"}:
            raise WorkbenchError(
                "Real worker requires a qualified official CLI: " + reason
            )
        executable = shutil.which(model["harness"])
        if not executable:
            raise WorkbenchError("Official CLI is absent on this worker")
        request_id, revision = packet["request_id"], packet["revision"]
        generation = packet.get("generation", 1)
        run_key = digest(
            canonical(
                [request_id, revision, node["id"], generation, packet["payload_hash"]]
            )
        )
        directory = self.store.home / "worker-runs" / run_key
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        receipt_file = directory / "receipt.json"
        current = await self.control.call("request_state", request_id=request_id)
        if (
            current["revision"] != revision
            or current["status"] in {"cancelled", "accepted", "grouped"}
            or current["payload_hash"] != packet["payload_hash"]
        ):
            raise WorkbenchError(
                "Worker payload does not match the current controller revision"
            )
        if receipt_file.exists():
            prior = json.loads(receipt_file.read_text())
            if prior.get("state") == "completed":
                return prior["result"]
            raise WorkbenchError(
                "This worker task has an earlier uncertain/failed process receipt; reconcile before replay"
            )
        host = await self.control.call("status")
        mapped = host["workspaces"].get(packet["project_id"])
        if not mapped:
            raise WorkbenchError("This worker has no project mapping")
        workspace = Path(mapped).resolve(strict=True)
        if not workspace.is_dir():
            raise WorkbenchError("Mapped worker workspace is not a directory")
        # Independent projects run together; one writer per project avoids shared-tree corruption.
        lock_path = self.store.home / ("workspace-" + digest(str(workspace)) + ".lock")
        lock = open(lock_path, "a")
        try:
            lease = await self.control.call(
                "admit",
                request_id=request_id,
                revision=revision,
                task_key=node["id"],
                capability=model["harness"],
                generation=generation,
            )
        except BaseException:
            lock.close()
            raise
        run_id = run_key
        proc = None
        output = directory / "stdout.jsonl"
        errors = directory / "stderr.log"
        deadline = min(
            packet["deadline"],
            now() + self.settings["limits"]["max_seconds"],
            now() + packet["plan"]["limits"]["max_seconds"],
        )
        receipt = {
            "request_id": request_id,
            "revision": revision,
            "node": node["id"],
            "host_id": self.host_id,
            "state": "starting",
            "lease_id": lease["lease_id"],
        }
        atomic(receipt_file, canonical(receipt))
        try:
            await self.control.call(
                "renew",
                lease_id=lease["lease_id"],
                lease_token=lease["lease_token"],
                run_id=run_id,
            )
            waiting_renewed = now()
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    current = await self.control.call(
                        "request_state", request_id=request_id
                    )
                    if current["revision"] != revision or current["status"] in {
                        "cancelled",
                        "accepted",
                        "grouped",
                    }:
                        raise WorkbenchError(
                            "Current requirements fence this waiting worker"
                        )
                    if now() >= deadline:
                        raise WorkbenchError(
                            "Timed out waiting for this project's writer slot"
                        )
                    if now() - waiting_renewed >= 15:
                        await self.control.call("heartbeat")
                        await self.control.call(
                            "renew",
                            lease_id=lease["lease_id"],
                            lease_token=lease["lease_token"],
                            run_id=run_id,
                        )
                        waiting_renewed = now()
                    activity.heartbeat(
                        {"phase": "waiting_workspace", "node": node["id"]}
                    )
                    await asyncio.sleep(1)
            await self.control.call(
                "renew",
                lease_id=lease["lease_id"],
                lease_token=lease["lease_token"],
                run_id=run_id,
            )
            prompt = (
                f"You are the {node['role']} for project {packet['project_id']}. Request {request_id}, revision {revision}.\n"
                "Complete your role in the current workspace. Preserve existing work and current requirements. Run relevant tests. "
                "Do not publish, send messages externally, or claim unrun tests passed. Keep your final answer concise, with changed files, tests, and blockers.\n\n"
                + packet["body"]
                + "\n\nPrior role results:\n"
                + canonical(packet.get("prior_results", {}))
                + "\n\nCurrent validated checkpoint (reload requirements before continuing):\n"
                + canonical(packet.get("checkpoint"))
                + "\n\nProcedures:\n"
                + "\n\n".join(s["text"] for s in packet["plan"].get("skills", []))
            )
            if len(prompt) > packet["plan"]["limits"]["context_chars"]:
                raise WorkbenchError("Worker prompt exceeds the current context budget")
            if model["harness"] == "codex":
                argv = [
                    executable,
                    "exec",
                    "--json",
                    "--model",
                    model["model"],
                    "--sandbox",
                    "workspace-write",
                    "--skip-git-repo-check",
                ]
                if model["effort"] != "default":
                    argv += [
                        "-c",
                        "model_reasoning_effort=" + json.dumps(model["effort"]),
                    ]
                argv.append("-")
            else:
                argv = [
                    executable,
                    "-p",
                    "--output-format",
                    "json",
                    "--model",
                    model["model"],
                    "--max-turns",
                    "30",
                ]
                if model["effort"] != "default":
                    argv += ["--effort", model["effort"]]
            env = os.environ.copy()
            if model["auth"] == "subscription":
                for key in (
                    "OPENAI_API_KEY",
                    "ANTHROPIC_API_KEY",
                    "AZURE_OPENAI_API_KEY",
                ):
                    env.pop(key, None)
            with output.open("wb") as out, errors.open("wb") as err:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    cwd=workspace,
                    env=env,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=out,
                    stderr=err,
                    start_new_session=True,
                )
                receipt.update(state="running", pid=proc.pid, run_id=run_id)
                atomic(receipt_file, canonical(receipt))
                proc.stdin.write(prompt.encode())
                await proc.stdin.drain()
                proc.stdin.close()
                last_renew = 0
                while proc.returncode is None:
                    activity.heartbeat(
                        {"phase": "running", "node": node["id"], "pid": proc.pid}
                    )
                    current = await self.control.call(
                        "request_state", request_id=request_id
                    )
                    if current["revision"] != revision or current["status"] in {
                        "cancelled",
                        "accepted",
                        "grouped",
                    }:
                        raise WorkbenchError(
                            "Current requirements fence this worker generation"
                        )
                    if now() >= deadline:
                        raise WorkbenchError("Worker execution budget exhausted")
                    if output.stat().st_size + errors.stat().st_size > 8 * 1024 * 1024:
                        raise WorkbenchError("Worker output exceeded its 8 MiB limit")
                    if now() - last_renew >= 15:
                        await self.control.call("heartbeat")
                        await self.control.call(
                            "renew",
                            lease_id=lease["lease_id"],
                            lease_token=lease["lease_token"],
                            run_id=run_id,
                        )
                        last_renew = now()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=1)
                    except asyncio.TimeoutError:
                        pass
            if output.stat().st_size + errors.stat().st_size > 8 * 1024 * 1024:
                raise WorkbenchError("Worker output exceeded its 8 MiB limit")
            if proc.returncode != 0:
                raise WorkbenchError(
                    "Official CLI exited unsuccessfully; inspect the retained worker stderr"
                )
            stdout_text = output.read_text(errors="replace")
            usage = parse_cli_usage(stdout_text, model["harness"])
            messages = []
            for line in stdout_text.splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if (
                    event.get("type") == "item.completed"
                    and (event.get("item") or {}).get("type") == "agent_message"
                ):
                    messages.append(event["item"]["text"])
                if (
                    event.get("type") == "result"
                    and not event.get("is_error")
                    and event.get("result")
                ):
                    messages.append(event["result"])
            if not messages:
                raise WorkbenchError(
                    "CLI returned no successful assistant result event"
                )
            result = {
                "node": node["id"],
                "role": node["role"],
                "host_id": self.host_id,
                "generation": generation,
                "usage": usage,
                "body": messages[-1],
                "stdout_path": str(output),
                "stdout_sha256": digest(output.read_bytes()),
                "exit_code": proc.returncode,
                "status": "needs_review",
            }
            receipt.update(state="completed", result=result, stopped=True, usage=usage)
            await self.control.call(
                "release",
                lease_id=lease["lease_id"],
                lease_token=lease["lease_token"],
                state="completed",
                stop_receipt={
                    "run_id": run_id,
                    "stopped": True,
                    "exit_code": proc.returncode,
                    "usage": usage,
                },
            )
            atomic(receipt_file, canonical(receipt))
            return result
        except BaseException:
            if proc and proc.returncode is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                    await asyncio.wait_for(proc.wait(), timeout=3)
                except asyncio.TimeoutError:
                    os.killpg(proc.pid, signal.SIGKILL)
                    await proc.wait()
                except ProcessLookupError:
                    await proc.wait()
            usage = parse_cli_usage(
                (
                    output.read_text(errors="replace")
                    if output.is_file() and output.stat().st_size <= 8 * 1024 * 1024
                    else ""
                ),
                model["harness"],
            )
            receipt.update(
                usage=usage,
                state="failed_or_cancelled",
                stopped=proc is None or proc.returncode is not None,
            )
            atomic(receipt_file, canonical(receipt))
            try:
                await self.control.call(
                    "release",
                    lease_id=lease["lease_id"],
                    lease_token=lease["lease_token"],
                    state="failed",
                    stop_receipt={
                        "run_id": run_id,
                        "stopped": receipt["stopped"],
                        "never_started": proc is None,
                        "usage": usage,
                    },
                )
            except WorkbenchError:
                pass  # Keep the lease occupied/uncertain if a stop receipt cannot be established.
            raise
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()


async def serve_worker(
    store,
    settings,
    host_id,
    host_token,
    *,
    client=None,
    executor=None,
    control_endpoint=None,
):
    """Run a real Temporal worker until cancelled. Tests may inject labelled activities."""
    require_sdk()
    runtime = TemporalRuntime(store, settings, client)
    client = await runtime.connect()
    control = (
        WorkerControl(host_id, host_token, endpoint=control_endpoint)
        if control_endpoint
        else WorkerControl(host_id, host_token, store=store)
    )
    await control.call("heartbeat")
    host = await control.call("status")
    implementation = executor or OfficialCLIExecutor(
        store, settings, host_id, host_token, control
    )

    @activity.defn(name="awb.run_node.v1")
    async def run_node(packet: dict) -> dict:
        return await implementation.execute(packet)

    async def heartbeats():
        while True:
            await control.call("heartbeat")
            await asyncio.sleep(15)

    pulse = asyncio.create_task(heartbeats())
    try:
        control_queue = settings["coordinator"].get("task_queue", "awb-control")
        async with (
            Worker(
                client,
                task_queue=control_queue,
                workflows=[WorkbenchPlanWorkflow],
                max_concurrent_workflow_tasks=host["capacity"],
                workflow_failure_exception_types=[WorkbenchError],
            ),
            Worker(
                client,
                task_queue=host["task_queue"],
                activities=[run_node],
                max_concurrent_activities=host["capacity"]
                - host["interactive_reserve"],
                graceful_shutdown_timeout=timedelta(seconds=10),
            ),
        ):
            await pulse
    finally:
        pulse.cancel()
        await asyncio.gather(pulse, return_exceptions=True)

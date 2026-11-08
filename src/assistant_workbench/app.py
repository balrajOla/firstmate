from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from .config import initialize, config
from .context import Context
from .continuation import Continuation
from .engine import Engine
from .integrations import Integrations, run_process
from .policy import compile_plan
from .quality import Outcomes, lint
from .store import Store
from .workflow import Workflow
from .util import WorkbenchError, atomic, canonical, now, uid, digest


class Application:
    def __init__(self, home):
        initialize(home)
        self.store = Store(home)
        self.home = home
        Workflow(self.store)

    def dispatch(self, action, args=None):
        if not isinstance(action, str) or not action:
            raise WorkbenchError("Action must be a nonempty string")
        if args is not None and not isinstance(args, dict):
            raise WorkbenchError("Action arguments must be an object")
        args = dict(args or {})
        s = self.store
        if action.startswith("backend."):
            from .context_backends import ContextBackends

            method = action.removeprefix("backend.")
            if method not in {
                "ingest",
                "recall",
                "read",
                "delete",
                "reconcile",
                "status",
            }:
                raise WorkbenchError("Unknown context backend action")
            return getattr(ContextBackends(s), method)(**args)
        if action.startswith("execution."):
            from .execution_packs import ExecutionPacks

            method = action.removeprefix("execution.")
            if method not in {
                "create_workspace",
                "inspect",
                "list_workspaces",
                "list_runs",
                "get_run",
                "plan",
                "run",
                "cancel",
                "artifacts",
                "verify_artifacts",
            }:
                raise WorkbenchError("Unknown execution action")
            return getattr(ExecutionPacks(s), method)(**args)
        if action.startswith("fleet."):
            from .runtime_backends import Fleet

            method = action.removeprefix("fleet.")
            if method not in {
                "register",
                "status",
                "disable",
                "expire",
                "cancel",
                "usage",
            }:
                raise WorkbenchError("Unknown fleet operator action")
            # Registration returns a new token only to its caller. Status and
            # persisted events never include credentials. Workers use /worker.
            return getattr(Fleet(s), method)(**args)
        if action.startswith("workflow."):
            method = action.split(".")[1]
            if method not in {
                "obligations",
                "fulfill",
                "suggestions",
                "group",
                "project_policy",
                "save_plan",
            }:
                raise WorkbenchError("Unknown workflow action")
            return getattr(Workflow(s), method)(**args)
        if action == "project.add":
            return s.add_project(**args)
        if action == "project.list":
            return s.rows("SELECT * FROM projects ORDER BY name")
        if action == "project.grant":
            return s.grant(**args)
        if action == "prompt.submit":
            return s.submit(**args)
        if action == "request.assign":
            return s.assign(**args)
        if action == "request.cancel":
            return s.cancel(**args)
        if action == "request.reopen":
            return s.reopen(**args)
        if action == "request.get":
            return s.one("SELECT * FROM requests WHERE id=?", (args["request_id"],))
        if action == "request.plan":
            return compile_plan(
                s.one("SELECT * FROM requests WHERE id=?", (args.pop("request_id"),)),
                config(self.home),
                **args,
            )
        if action == "request.retry":
            from .runtime_backends import select_runtime

            select_runtime(s, config(self.home))
            # Retry only resets blocked outbox rows; no coordinator or SDK is
            # constructed, and uncertain work remains fenced.
            return Engine.retry(self, **args)
        if action == "coordinator.tick":
            return self._coordinator().tick(**args)
        if action == "coordinator.observe":
            runtime = self._coordinator()
            observed = runtime.observe(**args)
            if config(self.home)["coordinator"]["kind"] == "temporal":
                from .runtime_observations import reconcile_observations

                return reconcile_observations(s, observed)
            return observed
        if action == "coordinator.retire_agents":
            runtime = self._coordinator()
            if not isinstance(runtime, Engine):
                raise WorkbenchError("Retiring managed agents applies to the Paperclip coordinator")
            return runtime.retire_agents(**args)
        if action == "coordinator.health":
            runtime = self._coordinator()
            return (
                runtime.api.health()
                if isinstance(runtime, Engine)
                else runtime.health()
            )
        if action == "lifecycle.request":
            from .lifecycle import Lifecycle
            from .runtime_backends import select_runtime

            selected = select_runtime(s, config(self.home))
            if selected == "temporal":
                return self._coordinator().rollover(**args)
            return Lifecycle(s).request(**args)
        if action == "snapshot":
            return s.snapshot()
        if action.startswith("context."):
            method = action.split(".")[1]
            if method not in {
                "ingest",
                "inventory",
                "mark",
                "coverage",
                "source",
                "search",
                "packet",
                "revoke",
                "history",
                "assertion",
                "purge",
            }:
                raise WorkbenchError("Unknown context action")
            return getattr(Context(s), method)(**args)
        if action == "outcome.propose":
            return Outcomes(s).propose(**args)
        if action == "outcome.review":
            return Outcomes(s).review(**args)
        if action == "outcome.list":
            return (
                s.rows(
                    "SELECT o.* FROM outcomes o JOIN requests r ON r.id=o.request_id WHERE r.project_id=? ORDER BY o.pinned DESC,o.created DESC",
                    (s.project(args["project"])["id"],),
                )
                if args.get("project")
                else s.rows("SELECT * FROM outcomes ORDER BY pinned DESC,created DESC")
            )
        if action == "outcome.mark":
            field = args["field"]
            if field not in {"pinned", "is_read"}:
                raise WorkbenchError("Unknown outcome marker")
            with s.transaction() as db:
                db.execute(
                    "UPDATE outcomes SET " + field + "=? WHERE id=?",
                    (int(bool(args["value"])), args["outcome_id"]),
                )
            return {"updated": args["outcome_id"]}
        if action == "outcome.search":
            return s.rows(
                "SELECT * FROM outcomes WHERE instr(lower(body),lower(?))>0 ORDER BY created DESC LIMIT 50",
                (args["query"],),
            )
        if action == "mail.send":
            return s.send(**args)
        if action == "mail.ack":
            return s.ack(**args)
        if action == "mail.receipt":
            return s.receipt(**args)
        if action == "mail.list":
            return s.rows(
                "SELECT * FROM messages WHERE project_id=? AND recipient=? ORDER BY created",
                (s.project(args["project"])["id"], args["recipient"]),
            )
        if action.startswith("continuation."):
            method = action.split(".")[1]
            if method not in {"checkpoint", "recover", "effect"}:
                raise WorkbenchError("Unknown continuation action")
            return getattr(Continuation(s, config(self.home)["limits"]), method)(**args)
        if action.startswith("integration."):
            method = action.split(".")[1]
            if method not in {
                "basic_memory",
                "qmd",
                "history",
                "docling",
                "notebook",
                "researcher",
                "writing",
            }:
                raise WorkbenchError("Unknown integration action")
            value = getattr(Integrations(s), method)(**args)
            with s.transaction() as db:
                s.event(
                    db,
                    "integration.executed",
                    method,
                    {
                        "result_sha256": digest(canonical(value)),
                        "notice": "Execution receipt; not semantic acceptance",
                    },
                )
            return value
        if action == "capabilities.list":
            from .capabilities import registry

            return registry(self.home)
        if action == "capabilities.activate":
            from .capabilities import activate

            return activate(self.home, **args)
        if action == "profile.add":
            from .profiles import add

            return add(self.home, **args)
        if action == "profile.probe":
            from .profiles import probe

            return probe(self.home, **args)
        if action in {
            "evaluation.routing",
            "evaluation.judges",
            "evaluation.propose_policy",
            "evaluation.apply_policy",
        }:
            from . import evaluation

            if action == "evaluation.routing":
                return evaluation.routing_cases(**args)
            if action == "evaluation.judges":
                return evaluation.judge_agreement(**args)
            return getattr(evaluation, action.split(".")[1])(s, **args)
        if action == "writing.lint":
            return lint(**args)
        if action == "schedule.add":
            interval = int(args["interval_seconds"])
            if interval < 60:
                raise WorkbenchError("Scheduled intervals must be at least 60 seconds")
            ident = uid("sch_")
            with s.transaction() as db:
                db.execute(
                    "INSERT INTO schedules VALUES(?,?,?,?,?,1)",
                    (
                        ident,
                        s.project(args["project"])["id"],
                        args["prompt"],
                        float(args.get("next_run", now() + interval)),
                        interval,
                    ),
                )
            return {"id": ident}
        if action == "schedule.list":
            return s.rows("SELECT * FROM schedules")
        if action == "schedule.pause":
            with s.transaction() as db:
                db.execute("UPDATE schedules SET enabled=0 WHERE id=?", (args["id"],))
            return {"paused": args["id"]}
        if action == "doctor":
            return self.doctor()
        if action == "qualification.record":
            from .qualification import record

            return record(s, **args)
        if action == "qualification.cutover":
            from .qualification import cutover

            return cutover(self, **args)
        if action == "evidence.list":
            return s.rows("SELECT * FROM evidence ORDER BY created DESC")
        raise WorkbenchError("Unknown action: " + action)

    def _coordinator(self):
        from .runtime_backends import select_runtime

        settings = config(self.home)
        kind = select_runtime(self.store, settings)
        if kind == "temporal":
            from .temporal_runtime import TemporalRuntime

            return TemporalRuntime(self.store, settings)
        return Engine(self.store)

    def scheduled(self):
        # UTC epochs avoid DST ambiguity. Catch-up coalesces missed runs; no catch-up flood.
        for schedule in self.store.rows(
            "SELECT * FROM schedules WHERE enabled=1 AND next_run<=?", (now(),)
        ):
            key = "schedule:" + schedule["id"] + ":" + str(schedule["next_run"])
            self.store.submit(schedule["prompt"], key, schedule["project_id"])
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE schedules SET next_run=? WHERE id=? AND next_run=?",
                    (
                        now() + schedule["interval_seconds"],
                        schedule["id"],
                        schedule["next_run"],
                    ),
                )

    def doctor(self):
        settings = config(self.home)
        checks = []
        for executable in ["git", "node", "pnpm", "claude", "codex", "wezterm"]:
            path = shutil.which(executable)
            checks.append(
                {
                    "component": executable,
                    "installed": bool(path),
                    "path": path,
                    "authenticated": (
                        "not probed" if executable in {"claude", "codex"} else None
                    ),
                }
            )
        try:
            health = self.dispatch("coordinator.health")
            checks.append(
                {
                    "component": settings["coordinator"]["kind"],
                    "reachable": True,
                    "health": health,
                }
            )
        except WorkbenchError as exc:
            checks.append(
                {
                    "component": settings["coordinator"]["kind"],
                    "reachable": False,
                    "reason": str(exc),
                }
            )
        for name, item in settings.get("integrations", {}).items():
            cmd = item.get("command", [])
            checks.append(
                {
                    "component": name,
                    "enabled": item.get("enabled", False),
                    "installed": bool(cmd and shutil.which(cmd[0])),
                    "configured": bool(cmd or item.get("url")),
                    "execution": "not tested by doctor",
                }
            )
        from .profiles import readiness

        return {
            "version": "0.1.0",
            "home": str(self.home),
            "checks": checks,
            "models": [
                {
                    "id": m["id"],
                    "ready": readiness(m)[0],
                    "qualification": readiness(m)[1],
                    "auth": m["auth"],
                }
                for m in settings["models"]
            ],
            "release": "experimental; live model, GUI, quality and soak evidence required",
        }

"""Bounded fresh-context handoff through Paperclip, never a second worker runner."""

from __future__ import annotations
import json
from .config import config
from .continuation import Continuation
from .paperclip import Paperclip
from .policy import skill_packets
from .util import WorkbenchError, canonical, now


class Lifecycle:
    def __init__(self, store, api=None):
        self.store = store
        self.settings = config(store.home)
        self.api = api or Paperclip(self.settings["coordinator"])
        db = store.connect()
        try:
            db.execute(
                "CREATE TABLE IF NOT EXISTS rollovers(request_id TEXT NOT NULL,node TEXT NOT NULL,generation INTEGER NOT NULL,revision INTEGER NOT NULL,state TEXT NOT NULL,old_run TEXT,new_run TEXT,error TEXT,PRIMARY KEY(request_id,node,generation))"
            )
        finally:
            db.close()

    def request(self, request_id, node):
        recovered = Continuation(self.store, self.settings["limits"]).recover(
            request_id
        )
        plan = json.loads(recovered["request"]["plan"] or "{}")
        if node not in {n["id"] for n in plan.get("nodes", [])}:
            raise WorkbenchError("Node does not belong to the active execution plan")
        generation = recovered["next_generation"]
        if generation > self.settings["limits"]["max_iterations"]:
            raise WorkbenchError("Fresh-session budget exhausted")
        with self.store.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO rollovers(request_id,node,generation,revision,state) VALUES(?,?,?,?,'pending')",
                (request_id, node, generation, recovered["request"]["revision"]),
            )
        return {
            "request_id": request_id,
            "generation": generation,
            "state": "pending",
            "notice": "Successor starts only after a coordinator stop acknowledgement and current checkpoint checks.",
        }

    def tick(self):
        rows = self.store.rows(
            "SELECT * FROM rollovers WHERE state IN ('pending','quiescing','ready') ORDER BY rowid LIMIT 1"
        )
        if not rows:
            return None
        row = rows[0]
        state = row["state"]
        error = None
        new = None
        old = row["old_run"]
        try:
            recovered = Continuation(self.store, self.settings["limits"]).recover(
                row["request_id"]
            )
            req = recovered["request"]
            if (
                req["revision"] != row["revision"]
                or recovered["next_generation"] != row["generation"]
            ):
                raise WorkbenchError(
                    "Rollover superseded by current requirements or checkpoint"
                )
            if now() - req["created"] > self.settings["limits"]["max_seconds"]:
                raise WorkbenchError("Cumulative task time exhausted")
            mapping = json.loads(
                self.store.one(
                    "SELECT value FROM meta WHERE key=?", ("issues:" + req["id"],)
                )["value"]
            )
            issue = self.api.issue(mapping[str(req["revision"]) + ":" + row["node"]])
            if issue.get("status") in {"done", "cancelled"}:
                raise WorkbenchError("Issue is closed; do not restart it")
            if state == "pending":
                old = issue.get("executionRunId")
                if not old:
                    raise WorkbenchError(
                        "No previous run identity; quiescence cannot be established automatically"
                    )
                # Persist before cancellation so a lost response is reconciled by run identity.
                with self.store.transaction() as db:
                    db.execute(
                        "UPDATE rollovers SET state='quiescing',old_run=? WHERE request_id=? AND node=? AND generation=?",
                        (old, row["request_id"], row["node"], row["generation"]),
                    )
                self.api.cancel_run(old)
                state = "quiescing"
            if state == "quiescing":
                run = self.api.run(old)
                result = run.get("resultJson") or {}
                ack = result.get("executionCancellation") or {}
                native = result.get("nativeCancellation") or {}
                stopped = ack.get("state") == "acknowledged" or (
                    native.get("schema") == "paperclip.native-cancellation.v1"
                    and native.get("runId") == old
                    and native.get("dispatchState") == "acknowledged"
                    and native.get("dispatched") is True
                )
                if not stopped:
                    return {
                        "state": "quiescing",
                        "request_id": row["request_id"],
                        "reason": "Awaiting process stop evidence; terminal run status alone is insufficient",
                    }
                state = "ready"
            if state == "ready":
                current = self.api.issue(issue["id"])
                if current.get("executionRunId") not in {None, old}:
                    raise WorkbenchError(
                        "Another worker owns this issue; reconcile before handoff"
                    )
                plan = json.loads(req["plan"])
                skills = skill_packets(
                    self.settings, [s["name"] for s in plan["skills"]]
                )
                # Recheck immediately before the only successor dispatch operation.
                Continuation(self.store, self.settings["limits"]).recover(req["id"])
                response = self.api.wakeup(
                    issue["assigneeAgentId"],
                    {
                        "source": "on_demand",
                        "reason": "workbench_context_rollover",
                        "forceFreshSession": True,
                        "idempotencyKey": f"awb-rollover-{row['request_id']}-{row['node']}-{row['generation']}",
                        "payload": {
                            "issueId": issue["id"],
                            "workbenchCheckpoint": recovered,
                            "procedures": skills,
                            "requireUnderstandingCheck": True,
                        },
                    },
                )
                if response.get("status") == "skipped":
                    raise WorkbenchError("Coordinator skipped successor dispatch")
                new = response.get("id")
                if not new:
                    raise WorkbenchError(
                        "Coordinator returned no successor run identity"
                    )
                current_req = self.store.one(
                    "SELECT * FROM requests WHERE id=?", (req["id"],)
                )
                if (
                    current_req["revision"] != row["revision"]
                    or current_req["status"] == "cancelled"
                ):
                    self.api.cancel_run(new)
                    raise WorkbenchError(
                        "Scope changed during successor dispatch; cancellation sent and reconciliation required"
                    )
                state = "dispatched"
        except WorkbenchError as exc:
            state = "uncertain" if getattr(exc, "uncertain", False) else "blocked"
            error = str(exc)
        with self.store.transaction() as db:
            db.execute(
                "UPDATE rollovers SET state=?,old_run=?,new_run=?,error=? WHERE request_id=? AND node=? AND generation=?",
                (
                    state,
                    old,
                    new,
                    error,
                    row["request_id"],
                    row["node"],
                    row["generation"],
                ),
            )
            self.store.event(
                db,
                "rollover." + state,
                row["request_id"],
                {"generation": row["generation"], "error": error},
            )
        return {"state": state, "new_run": new, "error": error}

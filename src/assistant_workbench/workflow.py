"""Explicit grouping, obligations and revision-safe operator controls."""

from __future__ import annotations

import json
from .util import WorkbenchError, canonical, now, uid

SCHEMA = """
CREATE TABLE IF NOT EXISTS obligations(id TEXT PRIMARY KEY,request_id TEXT NOT NULL REFERENCES requests(id),revision INTEGER NOT NULL,description TEXT NOT NULL,acceptance TEXT NOT NULL,status TEXT NOT NULL,outcome_id TEXT REFERENCES outcomes(id));
CREATE TABLE IF NOT EXISTS obligation_origins(obligation_id TEXT PRIMARY KEY REFERENCES obligations(id),origin_request_id TEXT NOT NULL REFERENCES requests(id));
CREATE TABLE IF NOT EXISTS request_links(parent TEXT NOT NULL REFERENCES requests(id),child TEXT NOT NULL REFERENCES requests(id),kind TEXT NOT NULL,PRIMARY KEY(parent,child,kind));
CREATE TABLE IF NOT EXISTS project_policy(project_id TEXT PRIMARY KEY REFERENCES projects(id),priority INTEGER NOT NULL DEFAULT 50,paused INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS plan_versions(id TEXT PRIMARY KEY,request_id TEXT NOT NULL REFERENCES requests(id),revision INTEGER NOT NULL,plan TEXT NOT NULL,created REAL NOT NULL);
"""


class Workflow:
    def __init__(self, store):
        self.store = store
        db = store.connect()
        try:
            db.executescript(SCHEMA)
        finally:
            db.close()

    def obligations(self, request_id, revision, items):
        if not items or len(items) > 50:
            raise WorkbenchError(
                "Provide 1–50 explicit deliverables with acceptance criteria"
            )
        with self.store.transaction() as db:
            req = db.execute(
                "SELECT * FROM requests WHERE id=?", (request_id,)
            ).fetchone()
            if (
                not req
                or req["revision"] != revision
                or req["status"] in {"cancelled", "accepted", "grouped"}
            ):
                raise WorkbenchError("Obligations target a stale or closed revision")
            result = []
            for item in items:
                if (
                    not item.get("description", "").strip()
                    or not item.get("acceptance", "").strip()
                ):
                    raise WorkbenchError("Every deliverable needs acceptance criteria")
                ident = self.store._add_obligation(
                    db, request_id, revision,
                    item["description"], item["acceptance"], request_id,
                )
                result.append(ident)
            self.store.event(
                db,
                "obligations.saved",
                request_id,
                {"ids": result, "revision": revision},
            )
        return {"ids": result}

    def fulfill(self, obligation_id, outcome_id):
        with self.store.transaction() as db:
            item = db.execute(
                "SELECT * FROM obligations WHERE id=?", (obligation_id,)
            ).fetchone()
            out = db.execute(
                "SELECT * FROM outcomes WHERE id=?", (outcome_id,)
            ).fetchone()
            if (
                not item
                or not out
                or item["request_id"] != out["request_id"]
                or item["revision"] != out["revision"]
            ):
                raise WorkbenchError(
                    "Outcome does not address this obligation revision"
                )
            req = db.execute(
                "SELECT * FROM requests WHERE id=?", (item["request_id"],)
            ).fetchone()
            if (
                item["status"] == "accepted"
                and item["outcome_id"] == outcome_id
                and out["status"] == "accepted"
                and req["revision"] == item["revision"]
            ):
                return {"id": obligation_id, "status": "accepted"}
            if req["revision"] != item["revision"] or req["status"] in {
                "accepted", "cancelled", "grouped"
            }:
                raise WorkbenchError("Cannot change a closed or stale deliverable")
            if item["status"] == "accepted":
                raise WorkbenchError("An accepted deliverable cannot be reassigned")
            if out["status"] != "needs_review":
                raise WorkbenchError("Outcome is not reviewable")
            db.execute(
                "UPDATE obligations SET status='submitted',outcome_id=? WHERE id=?",
                (outcome_id, obligation_id),
            )
        return {"id": obligation_id, "status": "submitted"}

    def suggestions(self, request_id):
        import re

        req = self.store.one("SELECT * FROM requests WHERE id=?", (request_id,))
        words = set(re.findall(r"\w{4,}", req["body"].lower()))
        candidates = []
        for other in self.store.rows(
            "SELECT * FROM requests WHERE project_id=? AND id!=? AND status='queued'",
            (req["project_id"], request_id),
        ):
            w = set(re.findall(r"\w{4,}", other["body"].lower()))
            score = len(words & w) / max(1, len(words | w))
            if score >= 0.35:
                candidates.append(
                    {
                        "request_id": other["id"],
                        "overlap": round(score, 3),
                        "reason": "Same project and shared words; compatibility still requires review",
                    }
                )
        return {"candidates": candidates, "automatic_merge": False}

    def group(self, requests, reason):
        if (
            len(requests) < 2
            or len(requests) != len(set(requests))
            or not reason.strip()
        ):
            raise WorkbenchError(
                "Grouping requires distinct requests and a compatibility reason"
            )
        with self.store.transaction() as db:
            rows = []
            for ident in requests:
                row = db.execute(
                    "SELECT * FROM requests WHERE id=?", (ident,)
                ).fetchone()
                if not row or row["status"] != "queued" or row["remote_id"]:
                    raise WorkbenchError(
                        "Only undispatched queued requests can be grouped"
                    )
                if db.execute(
                    "SELECT 1 FROM outbox WHERE request_id=? AND status='delivering'",
                    (ident,),
                ).fetchone():
                    raise WorkbenchError("Delivery is in progress; grouping must wait")
                if db.execute(
                    "SELECT 1 FROM request_links WHERE parent=? AND kind='group'",
                    (ident,),
                ).fetchone():
                    raise WorkbenchError(
                        "Nested request groups are not supported; group the original independent requests"
                    )
                rows.append(row)
            if len({r["project_id"] for r in rows}) != 1:
                raise WorkbenchError(
                    "Grouping across projects requires separate dependency planning"
                )
            leader = uid("r_")
            body = "\n\n".join(f"Request {r['id']}:\n{r['body']}" for r in rows)
            db.execute(
                "INSERT INTO requests(id,project_id,title,body,status,created,updated) VALUES(?,?,?,?,?,?,?)",
                (
                    leader,
                    rows[0]["project_id"],
                    rows[0]["title"],
                    body,
                    "queued",
                    now(),
                    now(),
                ),
            )
            for row in rows:
                db.execute(
                    "INSERT INTO request_links VALUES(?,?,'group')", (leader, row["id"])
                )
                db.execute(
                    "UPDATE requests SET status='grouped',updated=? WHERE id=?",
                    (now(), row["id"]),
                )
                db.execute(
                    "UPDATE outbox SET status='superseded' WHERE request_id=? AND status IN ('pending','retry','blocked')",
                    (row["id"],),
                )
                self.store._add_obligation(
                    db, leader, 1, row["body"],
                    "Address this original request and its constraints", row["id"],
                )
                self.store._copy_obligations(
                    db, row["id"], row["revision"], leader, 1,
                    origin_override=row["id"],
                )
            db.execute(
                "INSERT INTO outbox(id,request_id,revision,operation,payload) VALUES(?,?,1,'submit','{}')",
                (uid("ob_"), leader),
            )
            self.store.event(
                db, "requests.grouped", leader, {"requests": requests, "reason": reason}
            )
        return {
            "request_id": leader,
            "original_requests": requests,
            "original_prompts_preserved": True,
        }

    def project_policy(self, project, priority=50, paused=False):
        pid = self.store.project(project)["id"]
        if not 0 <= priority <= 100:
            raise WorkbenchError("Priority must be between 0 and 100")
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO project_policy VALUES(?,?,?) ON CONFLICT(project_id) DO UPDATE SET priority=excluded.priority,paused=excluded.paused",
                (pid, priority, int(paused)),
            )
            self.store.event(
                db, "project.policy", pid, {"priority": priority, "paused": paused}
            )
        return {
            "project_id": pid,
            "paused": paused,
            "notice": "Pause prevents new dispatch; already-running work remains under Paperclip control.",
        }

    def save_plan(self, request_id, revision, plan):
        from .policy import validate_plan
        from .config import config

        validate_plan(plan, config(self.store.home))
        with self.store.transaction() as db:
            req = db.execute(
                "SELECT * FROM requests WHERE id=?", (request_id,)
            ).fetchone()
            if not req or req["revision"] != revision or req["status"] != "queued":
                raise WorkbenchError("Plan must target the current queued request")
            if plan.get("request_id") != request_id or plan.get("revision") != revision:
                raise WorkbenchError("Plan identity does not match request")
            if db.execute(
                "SELECT 1 FROM outbox WHERE request_id=? AND status='delivering'",
                (request_id,),
            ).fetchone():
                raise WorkbenchError("Plan is already being dispatched")
            db.execute(
                "INSERT INTO plan_versions VALUES(?,?,?,?,?)",
                (uid("plan_"), request_id, revision, canonical(plan), now()),
            )
            db.execute(
                "UPDATE requests SET plan=? WHERE id=?", (canonical(plan), request_id)
            )
        return {"request_id": request_id, "revision": revision}

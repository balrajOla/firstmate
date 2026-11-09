"""Durable intake/evidence ledger. Agent execution state remains in Paperclip."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager, closing
from pathlib import Path
from .util import WorkbenchError, canonical, digest, now, uid

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,name TEXT NOT NULL UNIQUE,repository TEXT,aliases TEXT NOT NULL,remote_id TEXT,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,project_id TEXT REFERENCES projects(id),title TEXT NOT NULL,body TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,status TEXT NOT NULL,remote_id TEXT,plan TEXT,created REAL NOT NULL,updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS prompts(id TEXT PRIMARY KEY,client_key TEXT NOT NULL UNIQUE,payload_hash TEXT NOT NULL,request_id TEXT NOT NULL REFERENCES requests(id),body TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT NOT NULL,entity TEXT NOT NULL,payload TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY,request_id TEXT REFERENCES requests(id),revision INTEGER NOT NULL,operation TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',lease REAL,attempts INTEGER NOT NULL DEFAULT 0,error TEXT,UNIQUE(request_id,revision,operation));
CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),locator TEXT NOT NULL,hash TEXT NOT NULL,media_type TEXT NOT NULL,authority TEXT NOT NULL,revoked INTEGER NOT NULL DEFAULT 0,supersedes TEXT REFERENCES sources(id),created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sources_text(id TEXT PRIMARY KEY REFERENCES sources(id),text TEXT NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS source_search USING fts5(id UNINDEXED,project_id UNINDEXED,text);
CREATE TABLE IF NOT EXISTS coverage(project_id TEXT NOT NULL REFERENCES projects(id),locator TEXT NOT NULL,state TEXT NOT NULL,source_id TEXT REFERENCES sources(id),error TEXT,PRIMARY KEY(project_id,locator));
CREATE TABLE IF NOT EXISTS outcomes(id TEXT PRIMARY KEY,request_id TEXT NOT NULL REFERENCES requests(id),revision INTEGER NOT NULL,body TEXT NOT NULL,claims TEXT NOT NULL,status TEXT NOT NULL,checks TEXT NOT NULL,author TEXT NOT NULL,pinned INTEGER NOT NULL DEFAULT 0,is_read INTEGER NOT NULL DEFAULT 0,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY,outcome_id TEXT NOT NULL REFERENCES outcomes(id),reviewer TEXT NOT NULL,verdict TEXT NOT NULL,notes TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS checkpoints(id TEXT PRIMARY KEY,request_id TEXT NOT NULL REFERENCES requests(id),revision INTEGER NOT NULL,generation INTEGER NOT NULL,iteration INTEGER NOT NULL,progress_hash TEXT NOT NULL,payload TEXT NOT NULL,created REAL NOT NULL,UNIQUE(request_id,generation));
CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),sender TEXT NOT NULL,recipient TEXT NOT NULL,body TEXT NOT NULL,client_key TEXT NOT NULL UNIQUE,acked REAL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS grants(project_id TEXT NOT NULL REFERENCES projects(id),target_id TEXT NOT NULL REFERENCES projects(id),PRIMARY KEY(project_id,target_id));
CREATE TABLE IF NOT EXISTS effects(id TEXT PRIMARY KEY,request_id TEXT NOT NULL REFERENCES requests(id),generation INTEGER NOT NULL,kind TEXT NOT NULL,state TEXT NOT NULL,receipt TEXT,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY,gate TEXT NOT NULL,profile TEXT NOT NULL,result TEXT NOT NULL,details TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS schedules(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),prompt TEXT NOT NULL,next_run REAL NOT NULL,interval_seconds INTEGER NOT NULL,enabled INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS message_receipts(message_id TEXT NOT NULL REFERENCES messages(id),stage TEXT NOT NULL,recipient TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(message_id,stage));
"""


class Store:
    def __init__(self, home: Path):
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.home / "workbench.sqlite"
        with closing(self.connect()) as db:
            db.executescript(SCHEMA)
            version = db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
            if version and version[0] != "1":
                raise WorkbenchError(
                    "Unsupported database version; preserve this database and upgrade the program"
                )
            db.execute("INSERT OR IGNORE INTO meta VALUES('schema','1')")
        self.path.chmod(0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=15000")
        return db

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def rows(self, sql, params=()):
        with closing(self.connect()) as db:
            return [dict(row) for row in db.execute(sql, params)]

    def one(self, sql, params=()):
        result = self.rows(sql, params)
        if not result:
            raise WorkbenchError("Record not found")
        return result[0]

    def event(self, db, kind, entity, payload):
        db.execute(
            "INSERT INTO events(kind,entity,payload,created) VALUES(?,?,?,?)",
            (kind, entity, canonical(payload), now()),
        )

    def _has_workflow(self, db):
        return (
            db.execute("SELECT 1 FROM sqlite_master WHERE name='obligations'")
            .fetchone() is not None
        )

    def _add_obligation(self, db, request_id, revision, description, acceptance, origin):
        ident = uid("obl_")
        db.execute(
            "INSERT INTO obligations VALUES(?,?,?,?,?,'pending',NULL)",
            (ident, request_id, revision, description, acceptance),
        )
        db.execute("INSERT INTO obligation_origins VALUES(?,?)", (ident, origin))
        return ident

    def _legacy_obligation_origin(self, db, item):
        """Recover old generated group criteria only when ownership is clear."""
        source = item["request_id"]
        children = list(
            db.execute(
                "SELECT r.* FROM request_links l JOIN requests r ON r.id=l.child WHERE l.parent=? AND l.kind='group'",
                (source,),
            )
        )
        generated_acceptance = {
            "Address this original request and its constraints",
            "Address surviving demand and its constraints",
        }
        if not children or item["acceptance"] not in generated_acceptance:
            return source
        # Explicitly supplied parent criteria have a saved event and must not be
        # reassigned just because their wording resembles a generated criterion.
        for event in db.execute(
            "SELECT payload FROM events WHERE entity=? AND kind='obligations.saved'",
            (source,),
        ):
            if item["id"] in json.loads(event["payload"]).get("ids", []):
                return source
        matches = []
        for child in children:
            bodies = {child["body"]}
            body = ""
            for prompt in db.execute(
                "SELECT body FROM prompts WHERE request_id=? ORDER BY rowid",
                (child["id"],),
            ):
                body = (
                    body + "\n\nAmendment:\n" + prompt["body"]
                    if body else prompt["body"]
                )
                bodies.add(body)
            if item["description"] in bodies:
                matches.append(child["id"])
        if len(matches) != 1:
            raise WorkbenchError(
                "Legacy group criterion ownership is ambiguous; reopen the group as independent work before changing its scope"
            )
        return matches[0]

    def _copy_obligations(
        self, db, source, source_revision, target, revision,
        exclude_origins=(), origin_override=None,
    ):
        if not self._has_workflow(db):
            return []
        rows = list(
            db.execute(
                "SELECT o.*,p.origin_request_id FROM obligations o LEFT JOIN obligation_origins p ON p.obligation_id=o.id WHERE o.request_id=? AND o.revision=? ORDER BY o.rowid",
                (source, source_revision),
            )
        )
        result = []
        for item in rows:
            origin = origin_override or item["origin_request_id"]
            if origin is None:
                origin = self._legacy_obligation_origin(db, item)
                db.execute(
                    "INSERT INTO obligation_origins VALUES(?,?)", (item["id"], origin)
                )
            if origin in exclude_origins:
                continue
            result.append(
                self._add_obligation(
                    db, target, revision, item["description"], item["acceptance"], origin
                )
            )
        return result

    def _refresh_group(self, db, parent_id, changed_child, withdrawn=False):
        """Rebuild one active group without discarding its direct amendments."""
        parent = db.execute(
            "SELECT * FROM requests WHERE id=?", (parent_id,)
        ).fetchone()
        if not parent or parent["status"] in {"accepted", "cancelled", "grouped"}:
            raise WorkbenchError(
                "Grouped request is closed or nested; reopen its active parent explicitly"
            )
        children = list(
            db.execute(
                "SELECT r.* FROM request_links l JOIN requests r ON r.id=l.child WHERE l.parent=? AND l.kind='group' ORDER BY l.rowid",
                (parent_id,),
            )
        )
        survivors = [
            c for c in children if c["status"] != "cancelled"
            and not (withdrawn and c["id"] == changed_child)
        ]
        direct = list(
            db.execute(
                "SELECT body FROM prompts WHERE request_id=? ORDER BY rowid", (parent_id,)
            )
        )
        parts = ["Request " + c["id"] + ":\n" + c["body"] for c in survivors]
        parts.extend("Group amendment:\n" + p["body"] for p in direct)
        revision = parent["revision"] + 1
        db.execute(
            "UPDATE requests SET body=?,revision=?,plan=NULL,status=?,updated=? WHERE id=?",
            (
                "\n\n".join(parts), revision,
                "queued" if survivors else "cancelled", now(), parent_id,
            ),
        )
        db.execute(
            "UPDATE outbox SET status='superseded' WHERE request_id=? AND status IN ('pending','retry','blocked')",
            (parent_id,),
        )
        db.execute("UPDATE outcomes SET status='superseded' WHERE request_id=?", (parent_id,))
        db.execute(
            "INSERT INTO outbox(id,request_id,revision,operation,payload) VALUES(?,?,?,?,?)",
            (uid("ob_"), parent_id, revision, "submit" if survivors else "cancel", "{}"),
        )
        # Parent-specific criteria survive. Criteria owned by a child are rebuilt
        # from that child's latest revision, so withdrawal cannot remove others.
        self._copy_obligations(
            db, parent_id, parent["revision"], parent_id, revision,
            exclude_origins={c["id"] for c in children},
        )
        for child in survivors:
            self._add_obligation(
                db, parent_id, revision, child["body"],
                "Address this original request and its constraints", child["id"],
            )
            self._copy_obligations(
                db, child["id"], child["revision"], parent_id, revision,
                origin_override=child["id"],
            )
        self.event(
            db, "group.scope_changed", parent_id,
            {"withdrawn" if withdrawn else "amended": changed_child, "revision": revision},
        )
        return {"request_id": parent_id, "revision": revision}

    def add_project(self, name, repository=None, aliases=()):
        name = name.strip()
        if not name or len(name) > 160:
            raise WorkbenchError("Project name must contain 1–160 characters")
        if repository:
            repository = str(Path(repository).expanduser().resolve(strict=True))
        project = {
            "id": uid("p_"),
            "name": name,
            "repository": repository,
            "aliases": canonical(list(aliases)),
            "created": now(),
        }
        with self.transaction() as db:
            db.execute(
                "INSERT INTO projects(id,name,repository,aliases,created) VALUES(:id,:name,:repository,:aliases,:created)",
                project,
            )
            self.event(db, "project.created", project["id"], {"name": name})
        return project

    def project(self, identity):
        return self.one(
            "SELECT * FROM projects WHERE id=? OR name=?", (identity, identity)
        )

    def route(self, text, explicit=None, reply_to=None):
        if explicit:
            return self.project(explicit)["id"], "explicit project"
        if reply_to:
            request = self.one("SELECT * FROM requests WHERE id=?", (reply_to,))
            return request["project_id"], "reply to saved request"
        import re

        hits = []
        for project in self.rows("SELECT * FROM projects"):
            names = [project["name"], *json.loads(project["aliases"])]
            if any(
                re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text, re.I)
                for name in names
                if name
            ):
                hits.append(project["id"])
        return (
            (hits[0], "unique named project")
            if len(hits) == 1
            else (None, "ambiguous project" if hits else "project required")
        )

    def submit(
        self, text, client_key, project=None, reply_to=None, expected_revision=None
    ):
        if not isinstance(text, str) or not text.strip() or len(text) > 1_000_000:
            raise WorkbenchError("Prompt must contain 1–1,000,000 characters")
        if not client_key or len(client_key) > 240:
            raise WorkbenchError(
                "A stable client key of at most 240 characters is required"
            )
        payload_hash = digest(canonical([text, project, reply_to, expected_revision]))
        group_parent = None
        refreshed_group = None
        with self.transaction() as db:
            old = db.execute(
                "SELECT * FROM prompts WHERE client_key=?", (client_key,)
            ).fetchone()
            if old:
                if old["payload_hash"] != payload_hash:
                    raise WorkbenchError(
                        "Client key already belongs to a different prompt"
                    )
                return {
                    "prompt_id": old["id"],
                    "request_id": old["request_id"],
                    "duplicate": True,
                }
            project_id, reason = self.route(text, project, reply_to)
            if reply_to:
                prior = db.execute(
                    "SELECT * FROM requests WHERE id=?", (reply_to,)
                ).fetchone()
                if not prior or prior["status"] in {"cancelled", "accepted"}:
                    raise WorkbenchError(
                        "Request is closed; reopen it explicitly before amending"
                    )
                if prior["project_id"] != project_id:
                    raise WorkbenchError(
                        "A reply cannot silently move to another project"
                    )
                if expected_revision != prior["revision"]:
                    raise WorkbenchError(
                        "Revision changed; reread the request before amending"
                    )
                if prior["status"] == "grouped":
                    parents = list(
                        db.execute(
                            "SELECT r.* FROM request_links l JOIN requests r ON r.id=l.parent WHERE l.child=? AND l.kind='group' AND r.status NOT IN ('accepted','cancelled')",
                            (reply_to,),
                        )
                    )
                    if len(parents) != 1 or parents[0]["status"] == "grouped":
                        raise WorkbenchError(
                            "Grouped request has no single active parent; reopen the group explicitly"
                        )
                    group_parent = parents[0]["id"]
                request_id = reply_to
                revision = prior["revision"] + 1
                body = prior["body"] + "\n\nAmendment:\n" + text
                db.execute(
                    "UPDATE requests SET body=?,revision=?,status=?,plan=NULL,updated=? WHERE id=?",
                    (
                        body, revision,
                        "grouped" if group_parent else "queued" if project_id else "needs_project",
                        now(), request_id,
                    ),
                )
                self._copy_obligations(
                    db, request_id, prior["revision"], request_id, revision
                )
                db.execute(
                    "UPDATE outbox SET status='superseded' WHERE request_id=? AND status IN ('pending','retry','blocked')",
                    (request_id,),
                )
                db.execute(
                    "UPDATE outcomes SET status='superseded' WHERE request_id=?",
                    (request_id,),
                )
            else:
                request_id = uid("r_")
                revision = 1
                db.execute(
                    "INSERT INTO requests(id,project_id,title,body,status,created,updated) VALUES(?,?,?,?,?,?,?)",
                    (
                        request_id,
                        project_id,
                        text.strip()[:100],
                        text,
                        "queued" if project_id else "needs_project",
                        now(),
                        now(),
                    ),
                )
            prompt_id = uid("pr_")
            db.execute(
                "INSERT INTO prompts VALUES(?,?,?,?,?,?)",
                (prompt_id, client_key, payload_hash, request_id, text, now()),
            )
            if project_id and not group_parent:
                db.execute(
                    "INSERT INTO outbox(id,request_id,revision,operation,payload) VALUES(?,?,?,?,?)",
                    (uid("ob_"), request_id, revision, "submit", canonical({})),
                )
            self.event(
                db,
                "prompt.saved",
                request_id,
                {"prompt_id": prompt_id, "revision": revision, "routing": reason},
            )
            if group_parent:
                refreshed_group = self._refresh_group(db, group_parent, request_id)
        return {
            "prompt_id": prompt_id,
            "request_id": request_id,
            "revision": revision,
            "routing": reason,
            "duplicate": False,
            **({"group": refreshed_group} if refreshed_group else {}),
        }

    def assign(self, request_id, project):
        project_id = self.project(project)["id"]
        with self.transaction() as db:
            req = db.execute(
                "SELECT * FROM requests WHERE id=?", (request_id,)
            ).fetchone()
            if not req or req["status"] != "needs_project":
                raise WorkbenchError("Only an unresolved request can be assigned")
            db.execute(
                "UPDATE requests SET project_id=?,status='queued',updated=? WHERE id=?",
                (project_id, now(), request_id),
            )
            db.execute(
                "INSERT INTO outbox(id,request_id,revision,operation,payload) VALUES(?,?,?,?,?)",
                (uid("ob_"), request_id, req["revision"], "submit", "{}"),
            )
            self.event(db, "request.assigned", request_id, {"project_id": project_id})

    def cancel(self, request_id):
        with self.transaction() as db:
            req = db.execute(
                "SELECT * FROM requests WHERE id=?", (request_id,)
            ).fetchone()
            if not req:
                raise WorkbenchError("Request not found")
            if req["status"] == "cancelled":
                return {
                    "request_id": request_id,
                    "status": "cancelled",
                    "duplicate": True,
                }
            # Withdrawing one grouped demand invalidates its parent's scope. Other
            # demands remain explicit obligations in a new parent revision.
            has_links = db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='request_links'"
            ).fetchone()
            parents = (
                list(
                    db.execute(
                        "SELECT parent FROM request_links WHERE child=? AND kind='group'",
                        (request_id,),
                    )
                )
                if has_links
                else []
            )
            for link in parents:
                parent = db.execute(
                    "SELECT * FROM requests WHERE id=?", (link["parent"],)
                ).fetchone()
                if parent["status"] not in {"cancelled", "accepted"}:
                    self._refresh_group(db, parent["id"], request_id, withdrawn=True)
            db.execute(
                "UPDATE requests SET status='cancelled',revision=revision+1,updated=? WHERE id=?",
                (now(), request_id),
            )
            self._copy_obligations(
                db, request_id, req["revision"], request_id, req["revision"] + 1
            )
            db.execute(
                "UPDATE outbox SET status='superseded' WHERE request_id=? AND status IN ('pending','retry','blocked')",
                (request_id,),
            )
            db.execute(
                "UPDATE outcomes SET status='cancelled' WHERE request_id=? AND status IN ('needs_review','needs_revision')",
                (request_id,),
            )
            db.execute(
                "INSERT OR IGNORE INTO outbox(id,request_id,revision,operation,payload) VALUES(?,?,?,?,?)",
                (uid("ob_"), request_id, req["revision"] + 1, "cancel", "{}"),
            )
            self.event(db, "request.cancelled", request_id, {})
        return {
            "request_id": request_id, "status": "cancelled",
            "revision": req["revision"] + 1, "duplicate": False,
        }

    def reopen(self, request_id):
        # Creation, lineage and requirements are one transaction: an execution
        # poller cannot observe the reopened work before its criteria exist.
        with self.transaction() as db:
            req = db.execute(
                "SELECT * FROM requests WHERE id=?", (request_id,)
            ).fetchone()
            if not req:
                raise WorkbenchError("Request not found")
            if not req["body"].strip():
                raise WorkbenchError(
                    "No active group scope remains; reopen a specific original request"
                )
            ident, prompt_id, key = uid("r_"), uid("pr_"), uid("reopen_")
            db.execute(
                "INSERT INTO requests(id,project_id,title,body,status,created,updated) VALUES(?,?,?,?,?,?,?)",
                (
                    ident, req["project_id"], req["title"], req["body"],
                    "queued" if req["project_id"] else "needs_project", now(), now(),
                ),
            )
            db.execute(
                "INSERT INTO prompts VALUES(?,?,?,?,?,?)",
                (
                    prompt_id, key,
                    digest(canonical([req["body"], req["project_id"], None, None])),
                    ident, req["body"], now(),
                ),
            )
            if self._has_workflow(db):
                latest = req["revision"]
                grouped = db.execute(
                    "SELECT COUNT(*),SUM(r.status!='cancelled') FROM request_links l JOIN requests r ON r.id=l.child WHERE l.parent=? AND l.kind='group'",
                    (request_id,),
                ).fetchone()
                # Older installations did not copy criteria on cancellation.
                # Never resurrect a group's deliberately withdrawn demands.
                if req["status"] == "cancelled" and (not grouped[0] or grouped[1]):
                    latest = db.execute(
                        "SELECT MAX(revision) FROM obligations WHERE request_id=? AND revision<=?",
                        (request_id, req["revision"]),
                    ).fetchone()[0]
                if latest is not None:
                    self._copy_obligations(
                        db, request_id, latest, ident, 1, origin_override=ident
                    )
                db.execute(
                    "INSERT INTO request_links VALUES(?,?,'reopen')", (ident, request_id)
                )
            if req["project_id"]:
                db.execute(
                    "INSERT INTO outbox(id,request_id,revision,operation,payload) VALUES(?,?,1,'submit','{}')",
                    (uid("ob_"), ident),
                )
            self.event(
                db, "request.reopened", ident,
                {"original_request": request_id, "original_revision": req["revision"]},
            )
            self.event(
                db, "prompt.saved", ident,
                {"prompt_id": prompt_id, "revision": 1, "routing": "historical request"},
            )
        return {
            "prompt_id": prompt_id, "request_id": ident, "revision": 1,
            "routing": "historical request", "duplicate": False,
            "reopened_from": request_id,
        }

    def grant(self, project, target):
        a, b = self.project(project)["id"], self.project(target)["id"]
        with self.transaction() as db:
            db.execute("INSERT OR IGNORE INTO grants VALUES(?,?)", (a, b))
            self.event(db, "scope.granted", a, {"target": b})

    def can_read(self, project, target):
        return project == target or bool(
            self.rows(
                "SELECT 1 FROM grants WHERE project_id=? AND target_id=?",
                (project, target),
            )
        )

    def send(self, project, sender, recipient, body, client_key):
        project_id = self.project(project)["id"]
        if (
            not sender
            or not recipient
            or not client_key
            or not body.strip()
            or len(body) > 100_000
        ):
            raise WorkbenchError(
                "Messages require identities, a client key and a bounded body"
            )
        payload = (project_id, sender, recipient, body)
        with self.transaction() as db:
            existing = db.execute(
                "SELECT * FROM messages WHERE client_key=?", (client_key,)
            ).fetchone()
            if existing:
                if (
                    tuple(
                        existing[k]
                        for k in ["project_id", "sender", "recipient", "body"]
                    )
                    != payload
                ):
                    raise WorkbenchError("Message key conflict")
                return dict(existing)
            if (
                db.execute(
                    "SELECT COUNT(*) FROM messages WHERE project_id=? AND acked IS NULL",
                    (project_id,),
                ).fetchone()[0]
                >= 1000
            ):
                raise WorkbenchError(
                    "Project mailbox is full; acknowledge or archive existing messages"
                )
            message_id = uid("m_")
            db.execute(
                "INSERT INTO messages(id,project_id,sender,recipient,body,client_key,created) VALUES(?,?,?,?,?,?,?)",
                (message_id, *payload, client_key, now()),
            )
            self.event(
                db,
                "message.sent",
                message_id,
                {"project": project_id, "recipient": recipient},
            )
        return {"id": message_id}

    def receipt(self, message_id, recipient, stage):
        stages = ["delivered", "read", "acted"]
        if stage not in stages:
            raise WorkbenchError("Receipt stage must be delivered, read or acted")
        with self.transaction() as db:
            msg = db.execute(
                "SELECT * FROM messages WHERE id=? AND recipient=?",
                (message_id, recipient),
            ).fetchone()
            if not msg:
                raise WorkbenchError("Message does not belong to this recipient")
            index = stages.index(stage)
            if (
                index
                and not db.execute(
                    "SELECT 1 FROM message_receipts WHERE message_id=? AND stage=?",
                    (message_id, stages[index - 1]),
                ).fetchone()
            ):
                raise WorkbenchError("Receipt stages must be recorded in order")
            db.execute(
                "INSERT OR IGNORE INTO message_receipts VALUES(?,?,?,?)",
                (message_id, stage, recipient, now()),
            )
        return {"message_id": message_id, "stage": stage}

    def ack(self, message_id, recipient):
        with self.transaction() as db:
            count = db.execute(
                "UPDATE messages SET acked=? WHERE id=? AND recipient=?",
                (now(), message_id, recipient),
            ).rowcount
            if not count:
                raise WorkbenchError("Message does not belong to this recipient")

    def snapshot(self):
        return {
            "generated_at": now(),
            "projects": self.rows("SELECT * FROM projects ORDER BY created"),
            "requests": self.rows(
                "SELECT * FROM requests ORDER BY updated DESC LIMIT 200"
            ),
            "outcomes": self.rows(
                "SELECT * FROM outcomes ORDER BY pinned DESC,created DESC LIMIT 200"
            ),
            "sources": self.rows(
                "SELECT * FROM sources ORDER BY created DESC LIMIT 200"
            ),
            "attention": self.rows(
                "SELECT * FROM outbox WHERE status IN ('blocked','uncertain','retry') ORDER BY rowid"
            ) + self.worker_blocked(),
            "events": self.rows("SELECT * FROM events ORDER BY seq DESC LIMIT 50"),
        }

    WORKER_BLOCKED_NOTICE = (
        "A worker reported it could not complete its task; "
        "amend the request with what would unblock it, or cancel it"
    )

    def worker_blocked(self):
        """Running requests whose current revision has a task the worker
        itself reported blocked. Nothing else moves them, so they need the
        operator even though no delivery failed."""
        rows = self.rows(
            "SELECT r.id AS request_id, r.revision, m.key, m.value FROM requests r JOIN meta m "
            # Keys from 'report:<id>:<revision>:' up to, not including, the
            # same prefix ending in ';', the character after ':'.
            "ON m.key > 'report:' || r.id || ':' || r.revision || ':' "
            "AND m.key < 'report:' || r.id || ':' || r.revision || ';' "
            "WHERE r.status='running' ORDER BY r.created, m.key"
        )
        blocked = {}
        for row in rows:
            if json.loads(row["value"]).get("disposition") != "blocked":
                continue
            item = blocked.setdefault(row["request_id"], {
                "id": "worker-blocked:" + row["request_id"],
                "request_id": row["request_id"],
                "revision": row["revision"],
                "status": "worker_blocked",
                "nodes": [],
            })
            item["nodes"].append(row["key"].rsplit(":", 1)[1])
        for item in blocked.values():
            item["error"] = self.WORKER_BLOCKED_NOTICE + " (blocked: " + ", ".join(item["nodes"]) + ")"
        return list(blocked.values())

    def record_evidence(self, gate, profile, result, details):
        if result not in {"passed", "failed", "blocked", "not_run"}:
            raise WorkbenchError("Invalid evidence result")
        with self.transaction() as db:
            db.execute(
                "INSERT INTO evidence VALUES(?,?,?,?,?,?)",
                (uid("e_"), gate, profile, result, canonical(details), now()),
            )

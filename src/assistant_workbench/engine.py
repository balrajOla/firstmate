from __future__ import annotations

import json
import time
import fcntl
from .config import config
from .paperclip import Paperclip, CoordinatorError
from .policy import compile_plan
from .util import WorkbenchError, canonical, digest, now, uid


class Engine:
    def __init__(self, store, coordinator=None):
        from .workflow import Workflow

        Workflow(store)
        self.store = store
        self.settings = config(store.home)
        self.api = coordinator or Paperclip(self.settings["coordinator"])

    def claim(self):
        with self.store.transaction() as db:
            # A crashed mutation is uncertain until idempotent replay/reconciliation.
            db.execute(
                "UPDATE outbox SET status='retry',error='Previous delivery lease expired' WHERE status='delivering' AND lease<?",
                (now(),),
            )
            row = db.execute(
                "SELECT o.* FROM outbox o JOIN requests r ON r.id=o.request_id LEFT JOIN project_policy p ON p.project_id=r.project_id WHERE o.status IN ('pending','retry') AND (o.operation='cancel' OR COALESCE(p.paused,0)=0) ORDER BY CASE WHEN o.operation='cancel' THEN 0 ELSE 1 END,o.attempts, (COALESCE(p.priority,50) - ((strftime('%s','now')-r.created)/60.0)),o.rowid LIMIT 1"
            ).fetchone()
            if not row:
                return None
            db.execute(
                "UPDATE outbox SET status='delivering',lease=?,attempts=attempts+1 WHERE id=?",
                (now() + 180, row["id"]),
            )
            return dict(row)

    def _cache(self, key, value=None):
        with self.store.transaction() as db:
            if value is not None:
                db.execute(
                    "INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, canonical(value)),
                )
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def ensure_agent(self, node, plan):
        model = plan["model"]
        runtime = {
            k: model.get(k)
            for k in ("id", "harness", "model", "effort", "auth", "adapter_config")
        }
        key = (
            "awb:"
            + digest(canonical([runtime, node["role"], node.get("slot", 0)]))[:24]
        )
        existing = self._cache("agent:" + key)
        agents = self.api.agents()
        if not isinstance(agents, list):
            raise WorkbenchError("Unexpected Paperclip agent-list contract")
        matched = [
            a for a in agents if (a.get("metadata") or {}).get("workbenchKey") == key
        ]
        if len(matched) > 1:
            raise WorkbenchError(
                "Duplicate managed agent identities require reconciliation"
            )
        if matched:
            if matched[0].get("status") in {"paused", "terminated"}:
                raise WorkbenchError(
                    "Selected managed agent is paused or terminated; reconcile its capacity explicitly"
                )
            self._cache("agent:" + key, {"id": matched[0]["id"]})
            return matched[0]["id"]
        if existing and existing.get("id"):
            raise WorkbenchError(
                "Cached managed agent is missing from the coordinator; reconcile before replacing it"
            )
        if existing and existing.get("state") == "creating":
            raise CoordinatorError(
                "Agent creation has an uncertain outcome; inspect Paperclip before retrying",
                True,
            )
        managed = [a for a in agents if (a.get("metadata") or {}).get("workbenchKey")]
        if len(managed) >= self.settings["limits"]["max_agents"]:
            raise WorkbenchError(
                "Managed agent pool is full; reuse or explicitly retire a role/profile"
            )
        adapter = {
            "claude": "claude_local",
            "codex": "codex_local",
            "process": "process",
        }[model["harness"]]
        adapter_config = dict(model.get("adapter_config", {}))
        adapter_config.update(
            model=model["model"], timeoutSec=self.settings["limits"]["max_seconds"]
        )
        if model["harness"] == "claude":
            adapter_config.setdefault("dangerouslySkipPermissions", False)
        if model["harness"] == "codex":
            adapter_config.setdefault("dangerouslyBypassApprovalsAndSandbox", False)
        field = {"claude": "effort", "codex": "modelReasoningEffort"}.get(
            model["harness"]
        )
        if model.get("effort_field") and model["effort_field"] != field:
            raise WorkbenchError("Effort mapping does not match the pinned adapter")
        if field and model["effort"] != "default":
            adapter_config[field] = model["effort"]
        text = (
            "You are the "
            + node["role"]
            + " in Assistant Workbench. Read your Paperclip issue and current revision. "
            "Use awb for scoped source retrieval, messages, checkpoints and proposed outcomes. "
            "Preserve evidence and qualifications. Never mark an unreviewed outcome verified. "
            "Obey cancellation, role limits and repository permissions. The issue carries procedure revisions. "
            "Reviewers must be distinct from authors. Keep reports concise and state missing evidence. "
            "At each start call continuation.recover if a checkpoint exists. Restate current requirements and source hashes before writes. "
            "Before context exhaustion commit a checkpoint and request lifecycle.request, then stop. Never self-launch a replacement process."
        )
        body = {
            "name": "AWB " + node["role"] + " " + model["id"],
            "role": node["role"],
            "adapterType": adapter,
            "adapterConfig": adapter_config,
            "instructionsBundle": {
                "entryFile": "AGENTS.md",
                "files": {"AGENTS.md": text},
            },
            "permissions": {"canCreateAgents": False, "canCreateSkills": False},
            "metadata": {"workbenchKey": key},
            "runtimeConfig": {
                "heartbeat": {"enabled": True, "intervalSec": 0, "maxConcurrentRuns": 1}
            },
        }
        self._cache("agent:" + key, {"state": "creating"})
        try:
            agent = self.api.create_agent(body)
        except CoordinatorError as exc:
            if not exc.uncertain:
                self._cache("agent:" + key, {"state": "blocked"})
            raise
        self._cache("agent:" + key, {"id": agent["id"]})
        return agent["id"]

    def _check_current(self, request_id, revision):
        current = self.store.one("SELECT * FROM requests WHERE id=?", (request_id,))
        if current["revision"] != revision or current["status"] == "cancelled":
            raise WorkbenchError("Request superseded during delivery")
        return current

    def deliver(self, job):
        req = self.store.one("SELECT * FROM requests WHERE id=?", (job["request_id"],))
        issue_map = self._cache("issues:" + req["id"]) or {}
        if job["operation"] == "cancel":
            for identity in issue_map.values():
                self.api.update_issue(identity, {"status": "cancelled"})
            return "delivered"
        if req["revision"] != job["revision"] or req["status"] == "cancelled":
            return "superseded"
        project = self.store.project(req["project_id"])
        if not project["remote_id"]:
            body = {
                "name": project["name"],
                "description": "Assistant Workbench project " + project["id"],
                "idempotencyKey": "awb-project-" + project["id"],
            }
            if project["repository"]:
                body["workspace"] = {
                    "name": "local",
                    "cwd": project["repository"],
                    "isPrimary": True,
                }
            remote = self.api.create_project(body)
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE projects SET remote_id=? WHERE id=?",
                    (remote["id"], project["id"]),
                )
            project["remote_id"] = remote["id"]
        plan = (
            json.loads(req["plan"])
            if req.get("plan")
            else compile_plan(req, self.settings)
        )
        with self.store.transaction() as db:
            db.execute(
                "UPDATE requests SET plan=? WHERE id=? AND revision=?",
                (canonical(plan), req["id"], req["revision"]),
            )
        # Old-revision work is cancelled by its coordinator before replacement tasks.
        if req["revision"] > 1:
            for key, identity in issue_map.items():
                if not key.startswith(str(req["revision"]) + ":"):
                    self.api.update_issue(identity, {"status": "cancelled"})
        created = {}
        for node in plan["nodes"]:
            self._check_current(req["id"], req["revision"])
            agent = self.ensure_agent(node, plan)
            node_key = str(req["revision"]) + ":" + node["id"]
            description = (
                req["body"]
                + "\n\n## Workbench execution contract\n"
                + canonical(
                    {
                        "request": req["id"],
                        "revision": req["revision"],
                        "project": project["id"],
                        "node": node,
                        "limits": plan["limits"],
                        "audience": plan["audience"],
                        "home": str(self.store.home),
                    }
                )
                + "\n\n"
                + "\n\n".join(
                    "## Procedure " + s["name"] + " (" + s["sha256"] + ")\n" + s["text"]
                    for s in plan["skills"]
                )
            )
            if len(description) > self.settings["limits"]["context_chars"]:
                raise WorkbenchError(
                    "Execution packet exceeds the configured character budget; create a bounded evidence/coverage plan before dispatch. Original prompts remain saved."
                )
            body = {
                "projectId": project["remote_id"],
                "title": node["id"] + ": " + req["title"],
                "description": description,
                "status": "todo",
                "assigneeAgentId": agent,
                "blockedByIssueIds": [created[d] for d in node["depends_on"]],
                "reviewPolicy": "not_creator",
                "idempotencyKey": "awb-" + req["id"] + "-" + node_key,
            }
            if project["repository"]:
                body["executionWorkspacePreference"] = "isolated_workspace"
                if len(node["depends_on"]) == 1:
                    body["inheritExecutionWorkspaceFromIssueId"] = created[
                        node["depends_on"][0]
                    ]
            remote = self.api.create_issue(body)
            created[node["id"]] = remote["id"]
            issue_map[node_key] = remote["id"]
            self._cache("issues:" + req["id"], issue_map)
            current = self.store.one("SELECT * FROM requests WHERE id=?", (req["id"],))
            if (
                current["revision"] != req["revision"]
                or current["status"] == "cancelled"
            ):
                self.api.update_issue(remote["id"], {"status": "cancelled"})
                return "superseded"
        with self.store.transaction() as db:
            db.execute(
                "UPDATE requests SET remote_id=?,status='running',updated=? WHERE id=? AND revision=? AND status!='cancelled'",
                (next(iter(created.values())), now(), req["id"], req["revision"]),
            )
            self.store.event(
                db,
                "request.delivered",
                req["id"],
                {"issues": created, "revision": req["revision"]},
            )
        return "delivered"

    def tick(self):
        # Serialize bridge admission and agent creation across local processes.
        # Paperclip still runs independent issues concurrently.
        with open(self.store.home / "bridge.lock", "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"status": "busy"}
            try:
                from .lifecycle import Lifecycle

                continuation = Lifecycle(self.store, self.api).tick()
                delivered = self._tick()
                return delivered or continuation
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _tick(self):
        job = self.claim()
        if not job:
            return None
        try:
            status = self.deliver(job)
            error = None
        except WorkbenchError as exc:
            status = "uncertain" if getattr(exc, "uncertain", False) else "blocked"
            error = str(exc)
        with self.store.transaction() as db:
            db.execute(
                "UPDATE outbox SET status=?,error=?,lease=NULL WHERE id=?",
                (status, error, job["id"]),
            )
            self.store.event(
                db, "delivery." + status, job["request_id"], {"error": error}
            )
        return {"id": job["id"], "status": status, "error": error}

    def retry(self, request_id):
        with self.store.transaction() as db:
            db.execute(
                "UPDATE outbox SET status='retry' WHERE request_id=? AND status='blocked'",
                (request_id,),
            )
        return {
            "request_id": request_id,
            "notice": "Uncertain mutations require reconciliation; they were not blindly retried.",
        }

    def observe(self):
        from .util import atomic

        observations = {}
        for req in self.store.rows(
            "SELECT * FROM requests WHERE status IN ('running','needs_review','needs_revision') LIMIT 100"
        ):
            mapping = self._cache("issues:" + req["id"]) or {}
            items = {}
            for node, identity in mapping.items():
                if not node.startswith(str(req["revision"]) + ":"):
                    continue
                issue = self.api.issue(identity)
                items[node.split(":", 1)[1]] = {
                    k: issue.get(k)
                    for k in (
                        "id",
                        "status",
                        "assigneeAgentId",
                        "executionRunId",
                        "updatedAt",
                    )
                }
            observations[req["id"]] = items
            if items and all(i["status"] == "done" for i in items.values()):
                # Execution completion never substitutes for an accepted Workbench outcome.
                key = "observed-finished:" + req["id"] + ":" + str(req["revision"])
                if not self._cache(key):
                    with self.store.transaction() as db:
                        self.store.event(
                            db,
                            "execution.finished_unverified",
                            req["id"],
                            {
                                "issues": list(items),
                                "notice": "Outcome review is still required",
                            },
                        )
                    self._cache(key, True)
        atomic(self.store.home / "observations.json", canonical(observations))
        return observations

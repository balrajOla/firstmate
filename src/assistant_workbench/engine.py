from __future__ import annotations

import json
import re
import shlex
import sys
import time
import fcntl
from .config import config
from .paperclip import Paperclip, CoordinatorError
from .policy import TEMPLATES, compile_plan, select_model, validate_plan
from .runtime_backends import select_runtime
from .util import WorkbenchError, canonical, digest, now, uid


class SupersededDelivery(WorkbenchError):
    """The saved scope changed; this outbox job must not be retried."""


# Pinned Paperclip's native runner replaces official CLIs with its bundled
# provider binary (without this machine's sign-in) and defaults to approving
# every tool. Official-CLI workers therefore use its legacy CLI runner.
# --bare keeps the user's personal plugins, hooks and CLAUDE.md out of
# product workers; acceptEdits allows workspace edits; local commits on the
# task branch hand work to dependent tasks; the disposition command closes the
# worker's own task. Other tools stay unapproved.
WORKER_ADAPTERS = {"claude": "claude_local", "codex": "codex_local"}
CLAUDE_WORKER_ARGS = [
    "--bare", "--permission-mode", "acceptEdits", "--allowedTools",
    "Bash(git status:*)", "Bash(git diff:*)", "Bash(git add:*)", "Bash(git commit:*)",
]
BYPASS_KEYS = {
    "claude": "dangerouslySkipPermissions",
    "codex": "dangerouslyBypassApprovalsAndSandbox",
}
# Provider keys a subscription profile must not inherit; the profile probe
# and Temporal workers remove the same keys.
SUBSCRIPTION_BLOCKED_KEYS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AZURE_OPENAI_API_KEY")
# Profile CLI flags that would undo that boundary.
BYPASS_FLAGS = {
    "--dangerously-skip-permissions",
    "--allow-dangerously-skip-permissions",
    "--permission-mode",
    "--dangerously-bypass-approvals-and-sandbox",
    "--yolo",
}


def disposition_command():
    """The exact command a Claude worker may run to record its disposition."""
    return shlex.quote(sys.executable) + " -m assistant_workbench.worker_disposition"


def granted_commands(args):
    """Shell commands that Claude CLI arguments allow through --allowedTools."""
    commands, granting = [], False
    for arg in args:
        if arg in ("--allowedTools", "--allowed-tools"):
            granting = True
            continue
        if arg.startswith(("--allowedTools=", "--allowed-tools=")):
            arg, granting = arg.split("=", 1)[1], False
        elif arg.startswith("-"):
            granting = False
            continue
        elif not granting:
            continue
        for match in re.finditer(r"Bash\(([^)]*)\)", arg):
            command = match.group(1).removesuffix(":*").strip()
            if command and command not in commands:
                commands.append(command)
    return commands


def task_branch(request_id, revision, node_id):
    """Name a task's branch before it runs, already in Paperclip's branch form,
    so a dependent can be created on its predecessor's branch."""
    name = "awb/" + request_id + "-r" + str(revision) + "-" + node_id
    return re.sub(r"-+", "-", re.sub(r"[^A-Za-z0-9._/-]+", "-", name)).strip("-/.")[:120]


def verify_worker_agent(agent, harness):
    """Refuse a coordinator agent that is not the requested official CLI."""
    if harness not in WORKER_ADAPTERS:
        return
    config = agent.get("adapterConfig") or {}
    if agent.get("adapterType") != WORKER_ADAPTERS[harness]:
        raise WorkbenchError(
            "Paperclip agent uses " + str(agent.get("adapterType"))
            + " instead of the official " + harness
            + " CLI runner; reconcile or retire it before dispatch"
        )
    if config.get("engine") != "cli":
        raise WorkbenchError(
            "Paperclip agent is not on the " + harness
            + " CLI engine; reconcile or retire it before dispatch"
        )
    if config.get(BYPASS_KEYS[harness]) is not False:
        raise WorkbenchError(
            "Paperclip agent permission bypass is not explicitly disabled; reconcile it before dispatch"
        )


class Engine:
    def __init__(self, store, coordinator=None):
        from .workflow import Workflow

        Workflow(store)
        self.store = store
        self.settings = config(store.home)
        select_runtime(store, self.settings, "paperclip")
        self.api = coordinator or Paperclip(self.settings["coordinator"])

    def _runtime_guard(self):
        settings = config(self.store.home)
        select_runtime(self.store, settings, "paperclip")
        return settings

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
        timeout = plan["limits"]["max_seconds"]
        runtime = {
            k: model.get(k)
            for k in ("id", "harness", "model", "effort", "auth", "adapter_config")
        }
        # Narrow per-plan limits need their own runtime identity. Preserve the
        # existing default-budget identity, with its timeout checked on reuse.
        if timeout != self.settings["limits"]["max_seconds"]:
            runtime["timeout_seconds"] = timeout
        adapter = {**WORKER_ADAPTERS, "process": "process"}[model["harness"]]
        adapter_config = dict(model.get("adapter_config", {}))
        adapter_config.update(
            model=model["model"], timeoutSec=timeout
        )
        if model["harness"] in BYPASS_KEYS:
            adapter_config.setdefault(BYPASS_KEYS[model["harness"]], False)
            extra = [str(a) for a in adapter_config.get("extraArgs", [])]
            # Refuse before any remote call, so no bypassing agent is created.
            if (
                adapter_config[BYPASS_KEYS[model["harness"]]] is not False
                or {a.split("=", 1)[0] for a in extra} & BYPASS_FLAGS
                or any("danger-full-access" in a for a in extra)
            ):
                raise WorkbenchError(
                    "Profile adapter_config would bypass worker permissions; "
                    "Paperclip workers run with them enforced"
                )
        if model["harness"] in WORKER_ADAPTERS and model.get("auth") == "subscription":
            # Paperclip starts workers with its own environment. A provider key
            # there would make a --bare Claude worker, which never reads a
            # subscription sign-in, bill that key instead.
            env = dict(adapter_config.get("env") or {})
            if any(env.get(k) for k in SUBSCRIPTION_BLOCKED_KEYS):
                raise WorkbenchError(
                    "A subscription profile cannot pass a provider API key to Paperclip workers"
                )
            adapter_config["env"] = {**env, **{k: "" for k in SUBSCRIPTION_BLOCKED_KEYS}}
        if model["harness"] in WORKER_ADAPTERS:
            # Both adapters otherwise default to an embedded engine that does
            # not use the official CLI or its sign-in.
            adapter_config["engine"] = "cli"
        if model["harness"] == "claude":
            adapter_config["extraArgs"] = CLAUDE_WORKER_ARGS + [
                "--allowedTools", "Bash(" + disposition_command() + ":*)"
            ] + list(adapter_config.get("extraArgs", []))
        field = {"claude": "effort", "codex": "modelReasoningEffort"}.get(
            model["harness"]
        )
        if model.get("effort_field") and model["effort_field"] != field:
            raise WorkbenchError("Effort mapping does not match the pinned adapter")
        if field and model["effort"] != "default":
            adapter_config[field] = model["effort"]
        # Only instruct what this worker can actually do: it has its task
        # packet and workspace, not the Workbench CLI or other projects.
        text = (
            "You are the "
            + node["role"]
            + " in an Assistant Workbench team. Your Paperclip task carries the saved request, "
            "its revision, the execution contract and pinned procedures; follow them. "
            "Work only in the task's workspace; do not touch other projects or unrelated files. "
            "Commit your changes to the task branch (git add, git commit; never push) before reporting, "
            "so dependent tasks start from your work. "
            "Your final message is your report. Begin it with one sentence that states the result, then give "
            "what you changed, the commands you ran and their results, "
            "open problems and missing evidence. The Workbench records that report for independent review, "
            "so never call the work verified or accepted. "
            + (
                "Before your final message, record your task disposition by running exactly `"
                + disposition_command()
                + " done`, or `"
                + disposition_command()
                + ' blocked "what would unblock it"` if you could not complete the task. '
                if model["harness"] == "claude"
                else "Record your task disposition if Paperclip lets you; otherwise end with the report. "
            )
            + (
                "Apart from editing files you can run only "
                + ", ".join("`" + c + "`" for c in granted_commands(adapter_config["extraArgs"]))
                + ". Other commands are refused, so name any check you could not run in your report. "
                if model["harness"] == "claude"
                else ""
            )
            + "A reviewer checks the author's work instead of redoing it. "
            "Stop when the task is cancelled and never start a replacement process."
        )
        # The identity covers everything sent to the coordinator, so a changed
        # worker definition (defaults, flags, instructions) is never served by
        # an older agent that still runs the previous one.
        key = (
            "awb:"
            + digest(
                canonical(
                    [runtime, node["role"], node.get("slot", 0), adapter, adapter_config, digest(text)]
                )
            )[:24]
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
            verify_worker_agent(matched[0], model["harness"])
            if matched[0].get("status") in {"paused", "terminated"}:
                raise WorkbenchError(
                    "Selected managed agent is paused or terminated; reconcile its capacity explicitly"
                )
            if (matched[0].get("adapterConfig") or {}).get("timeoutSec") != timeout:
                raise WorkbenchError(
                    "Managed agent timeout differs from the execution budget; reconcile the existing agent before reuse"
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
        name = "AWB " + node["role"] + " " + model["id"]
        managed = [
            a for a in agents
            if (a.get("metadata") or {}).get("workbenchKey") and a.get("status") != "terminated"
        ]
        if len(managed) >= self.settings["limits"]["max_agents"]:
            # Earlier definitions of this role and profile are superseded by
            # this one. Reclaim those that are idle and hold no open task, so
            # an upgrade does not stall unattended work; busy ones are kept.
            superseded = [a["id"] for a in managed if a.get("name") == name]
            if superseded:
                self.retire_agents(agent_ids=superseded, reason="superseded by a changed definition")
                managed = [
                    a for a in self.api.agents()
                    if (a.get("metadata") or {}).get("workbenchKey") and a.get("status") != "terminated"
                ]
        if len(managed) >= self.settings["limits"]["max_agents"]:
            raise WorkbenchError(
                "Managed agent pool is full; reuse or explicitly retire a role/profile"
            )
        body = {
            "name": name,
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
        if model["harness"] in WORKER_ADAPTERS:
            body["runner"] = "legacy"
        self._check_current(plan["request_id"], plan["revision"])
        self._cache("agent:" + key, {"state": "creating"})
        try:
            agent = self.api.create_agent(body)
        except CoordinatorError as exc:
            if not exc.uncertain:
                self._cache("agent:" + key, {"state": "blocked"})
            raise
        self._cache("agent:" + key, {"id": agent["id"]})
        verify_worker_agent(agent, model["harness"])
        return agent["id"]

    def retire_agents(self, agent_ids=None, idle=False, reason=None):
        """Terminate managed agents the operator no longer needs.

        Only Workbench-managed agents that are not running and hold no open
        task are retired, so no work is cancelled. Cached identities whose
        agent is no longer active are forgotten, so the next dispatch creates
        a fresh agent instead of stopping on a missing one. Uncertain
        creations stay cached for explicit inspection."""
        if not agent_ids and not idle:
            raise WorkbenchError("Name agent_ids, or set idle to retire every idle managed agent")
        agents = self.api.agents()
        if not isinstance(agents, list):
            raise WorkbenchError("Unexpected Paperclip agent-list contract")
        managed = {
            a["id"]: a for a in agents
            if (a.get("metadata") or {}).get("workbenchKey") and a.get("status") != "terminated"
        }
        retired, skipped = [], []
        for identity in list(agent_ids or managed):
            agent = managed.get(identity)
            if not agent:
                skipped.append({"id": identity, "reason": "not an active Workbench-managed agent"})
                continue
            if agent.get("status") == "running":
                skipped.append({"id": identity, "reason": "agent is running"})
                continue
            tasks = self.api.agent_issues(identity)
            if not isinstance(tasks, list):
                raise WorkbenchError("Unexpected Paperclip issue-list contract")
            open_tasks = [
                str(t.get("identifier") or t.get("id")) for t in tasks
                if t.get("status") not in {"done", "cancelled"}
            ]
            if open_tasks:
                skipped.append({"id": identity, "reason": "open tasks: " + ", ".join(open_tasks)})
                continue
            result = self.api.terminate_agent(identity)
            if result.get("status") != "terminated":
                raise CoordinatorError(
                    "Paperclip did not confirm terminating agent " + identity, True
                )
            key = agent["metadata"]["workbenchKey"]
            with self.store.transaction() as db:
                self.store.event(
                    db, "coordinator.agent_retired", identity,
                    {"workbench_key": key, "name": agent.get("name"),
                     **({"reason": reason} if reason else {})},
                )
            managed.pop(identity)
            retired.append({"id": identity, "name": agent.get("name")})
        forgotten = []
        with self.store.transaction() as db:
            for row in db.execute("SELECT key, value FROM meta WHERE key LIKE 'agent:%'").fetchall():
                cached = json.loads(row[1]) or {}
                if cached.get("id") and cached["id"] not in managed:
                    db.execute("DELETE FROM meta WHERE key=?", (row[0],))
                    forgotten.append(cached["id"])
        return {"retired": retired, "skipped": skipped, "forgotten": forgotten}

    def _check_current(self, request_id, revision):
        self._runtime_guard()
        current = self.store.one("SELECT * FROM requests WHERE id=?", (request_id,))
        if current["revision"] != revision or current["status"] in {
            "cancelled", "accepted", "grouped", "needs_project"
        }:
            raise SupersededDelivery("Request superseded during delivery")
        return current

    def _execution_packet(self, req, project, plan, node):
        return (
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

    def _preflight(self, req, project):
        """Validate the entire saved team and every packet before remote writes."""
        try:
            if req.get("plan"):
                plan = json.loads(req["plan"])
            else:
                from .routing import workflow_choice

                # A confident semantic decision chooses the workflow; otherwise
                # the lexical policy does, as before.
                kind, routing = workflow_choice(self.store, self.settings, req) or (None, None)
                plan = compile_plan(req, self.settings, kind, routing=routing)
            if (
                plan.get("schema") != 1
                or plan.get("request_id") != req["id"]
                or plan.get("revision") != req["revision"]
                or plan.get("workflow") not in TEMPLATES
            ):
                raise WorkbenchError("Plan identity, schema or workflow is invalid for this request")
            for key, ceiling in self.settings["limits"].items():
                value = plan["limits"].get(key)
                if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= ceiling:
                    raise WorkbenchError("Plan limit exceeds the configured budget or is invalid: " + key)
            # A plan may narrow the configured limits, never enlarge them.
            validate_plan(plan, {**self.settings, "limits": plan["limits"]})
            select_model(self.settings, plan["workflow"], plan["model"]["id"])
            field = {"claude": "effort", "codex": "modelReasoningEffort"}.get(
                plan["model"]["harness"]
            )
            if plan["model"].get("effort_field") and plan["model"]["effort_field"] != field:
                raise WorkbenchError("Effort mapping does not match the pinned adapter")
            if not isinstance(plan["model"].get("adapter_config", {}), dict):
                raise WorkbenchError("Adapter configuration must be an object")
            audience = plan["audience"]
            if (
                any(not isinstance(audience.get(k), str) or not audience[k].strip() for k in ("reader", "style"))
                or not isinstance(audience.get("max_words"), int)
                or isinstance(audience["max_words"], bool)
                or audience["max_words"] < 1
            ):
                raise WorkbenchError("Plan requires a reader, style and positive word budget")
            if not isinstance(plan["skills"], list):
                raise WorkbenchError("Plan procedures must be a list")
            for skill in plan["skills"]:
                if (
                    not all(isinstance(skill.get(k), str) for k in ("name", "text", "sha256"))
                    or digest(skill["text"]) != skill["sha256"]
                ):
                    raise WorkbenchError("Plan procedure content no longer matches its pinned hash")
            for node in plan["nodes"]:
                if (
                    any(not isinstance(node.get(k), str) or not node[k].strip() for k in ("id", "role"))
                    or not isinstance(node["depends_on"], list)
                    or not isinstance(node.get("slot", 0), int)
                    or isinstance(node.get("slot", 0), bool)
                    or node.get("slot", 0) < 0
                ):
                    raise WorkbenchError("Plan nodes require identities, roles, dependency lists and nonnegative slots")
            pending = list(plan["nodes"])
            ordered, visited = [], set()
            while pending:
                ready = [n for n in pending if set(n["depends_on"]) <= visited]
                if not ready:
                    raise WorkbenchError("Plan has unresolved dependencies")
                ordered.extend(ready)
                visited.update(n["id"] for n in ready)
                pending = [n for n in pending if n["id"] not in visited]
            packets = {n["id"]: self._execution_packet(req, project, plan, n) for n in ordered}
            if any(len(packet) > plan["limits"]["context_chars"] for packet in packets.values()):
                raise WorkbenchError(
                    "Execution packet exceeds the configured character budget; create a bounded evidence/coverage plan before dispatch. Original prompts remain saved."
                )
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise WorkbenchError("Saved execution plan is malformed; revise it before dispatch") from exc
        return plan, ordered, packets

    def deliver(self, job):
        self.settings = self._runtime_guard()
        req = self.store.one("SELECT * FROM requests WHERE id=?", (job["request_id"],))
        if req["revision"] != job["revision"]:
            return "superseded"
        issue_map = self._cache("issues:" + req["id"]) or {}
        if job["operation"] == "cancel":
            if req["status"] != "cancelled":
                return "superseded"
            for identity in issue_map.values():
                self.api.update_issue(identity, {"status": "cancelled"})
            return "delivered"
        if req["status"] in {"cancelled", "accepted", "grouped", "needs_project"}:
            return "superseded"
        project = self.store.project(req["project_id"])
        plan, ordered, packets = self._preflight(req, project)
        # Paperclip silently runs "isolated" tasks in the project's own
        # checkout unless its isolated-workspace setting is enabled.
        if project["repository"] and not (
            self.api.experimental_settings() or {}
        ).get("enableIsolatedWorkspaces"):
            raise WorkbenchError(
                "Paperclip isolated workspaces are disabled; enable them "
                "(Instance settings > Experimental) so agents do not edit the "
                "registered repository directly"
            )
        self._check_current(req["id"], req["revision"])
        with self.store.transaction() as db:
            changed = db.execute(
                "UPDATE requests SET plan=? WHERE id=? AND revision=? AND status NOT IN ('cancelled','accepted','grouped','needs_project')",
                (canonical(plan), req["id"], req["revision"]),
            ).rowcount
            if not changed:
                return "superseded"
        if not project["remote_id"]:
            self._check_current(req["id"], req["revision"])
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
        # Old-revision work is cancelled by its coordinator before replacement tasks.
        if req["revision"] > 1:
            for key, identity in issue_map.items():
                if not key.startswith(str(req["revision"]) + ":"):
                    self._check_current(req["id"], req["revision"])
                    self.api.update_issue(identity, {"status": "cancelled"})
        created = {}
        for node in ordered:
            self._check_current(req["id"], req["revision"])
            agent = self.ensure_agent(node, plan)
            node_key = str(req["revision"]) + ":" + node["id"]
            body = {
                "projectId": project["remote_id"],
                "title": node["id"] + ": " + req["title"],
                "description": packets[node["id"]],
                "status": "todo",
                "assigneeAgentId": agent,
                "blockedByIssueIds": [created[d] for d in node["depends_on"]],
                "reviewPolicy": "not_creator",
                "idempotencyKey": "awb-" + req["id"] + "-" + node_key,
                # Paperclip otherwise reuses any recent open issue with the
                # same title; requests may share their first 100 characters.
                "allowDuplicate": True,
            }
            if project["repository"]:
                # Paperclip resolves the run workspace from these settings; the
                # older executionWorkspacePreference field does not isolate.
                # Each task gets its own worktree on a branch named here, so a
                # single-dependency task starts from its predecessor's commits
                # even when Paperclip wakes it the moment that task is done.
                strategy = {
                    "type": "git_worktree",
                    "branchTemplate": task_branch(req["id"], req["revision"], node["id"]),
                }
                if len(node["depends_on"]) == 1:
                    strategy["baseRef"] = task_branch(
                        req["id"], req["revision"], node["depends_on"][0]
                    )
                body["executionWorkspaceSettings"] = {
                    "mode": "isolated_workspace",
                    "workspaceStrategy": strategy,
                }
            self._check_current(req["id"], req["revision"])
            remote = self.api.create_issue(body)
            # The packet names this request, revision and node, so only this
            # work's own issue (including an idempotent replay) can match it.
            if remote.get("description") != body["description"]:
                raise WorkbenchError(
                    "Paperclip returned an existing issue that belongs to different work ("
                    + str(remote.get("deduplicationReason") or "content differs")
                    + "); reconcile it before dispatch"
                )
            if project["repository"] and (
                remote.get("executionWorkspaceSettings") or {}
            ).get("mode") != "isolated_workspace":
                self.api.update_issue(remote["id"], {"status": "cancelled"})
                raise WorkbenchError(
                    "Paperclip did not keep the isolated workspace for this task; "
                    "it was cancelled before it could edit the repository"
                )
            created[node["id"]] = remote["id"]
            issue_map[node_key] = remote["id"]
            self._cache("issues:" + req["id"], issue_map)
            current = self.store.one("SELECT * FROM requests WHERE id=?", (req["id"],))
            if (
                current["revision"] != req["revision"]
                or current["status"] in {"cancelled", "accepted", "grouped", "needs_project"}
            ):
                self.api.update_issue(remote["id"], {"status": "cancelled"})
                return "superseded"
        with self.store.transaction() as db:
            changed = db.execute(
                "UPDATE requests SET remote_id=?,status=CASE WHEN status='queued' THEN 'running' ELSE status END,updated=? WHERE id=? AND revision=? AND status NOT IN ('cancelled','accepted','grouped','needs_project')",
                (next(iter(created.values())), now(), req["id"], req["revision"]),
            ).rowcount
            if not changed:
                return "superseded"
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
        self.settings = self._runtime_guard()
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
        except SupersededDelivery:
            status, error = "superseded", None
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
            reset = db.execute(
                "UPDATE outbox SET status='retry' WHERE request_id=? AND status='blocked'",
                (request_id,),
            ).rowcount
        if not reset:
            # A task a worker reported blocked is not a blocked delivery.
            return {
                "request_id": request_id,
                "reset": 0,
                "notice": "No blocked delivery to retry. Uncertain mutations need reconciliation; "
                "a task a worker reported blocked needs an amendment with what would unblock it, "
                "or cancellation.",
            }
        return {
            "request_id": request_id,
            "reset": reset,
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
                report = self._capture_report(req, node, issue)
                items[node.split(":", 1)[1]]["report"] = report
                if report != "failed" and self._cache("failure:" + req["id"] + ":" + node):
                    with self.store.transaction() as db:
                        db.execute("DELETE FROM meta WHERE key=?", ("failure:" + req["id"] + ":" + node,))
            observations[req["id"]] = items
            if items and all(i["report"] == "captured" for i in items.values()):
                self._propose_worker_outcome(req, list(items))
            blocked = sorted(n for n, i in items.items() if i["report"] == "blocked")
            key = "observed-blocked:" + req["id"] + ":" + str(req["revision"]) + ":" + ",".join(blocked)
            if blocked and not self._cache(key):
                with self.store.transaction() as db:
                    self.store.event(
                        db,
                        "execution.node_blocked",
                        req["id"],
                        {
                            "nodes": blocked,
                            "notice": self.store.WORKER_BLOCKED_NOTICE,
                        },
                    )
                self._cache(key, True)
            failed = sorted(n for n, i in items.items() if i["report"] == "failed")
            key = "observed-failed:" + req["id"] + ":" + str(req["revision"]) + ":" + ",".join(failed)
            if failed and not self._cache(key):
                with self.store.transaction() as db:
                    self.store.event(
                        db, "execution.node_failed", req["id"],
                        {"nodes": failed, "notice": self.store.EXECUTION_FAILED_NOTICE},
                    )
                self._cache(key, True)
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

    def _capture_report(self, req, node, issue):
        """Keep the worker's own final report from its first successful run.

        A worker that reported itself blocked keeps its task and dependents
        blocked. When a worker could not record any disposition, Paperclip
        blocks the task after its repair runs; the Workbench then records the
        report and closes the task. Acceptance still requires independent
        review."""
        key = "report:" + req["id"] + ":" + node
        cached = self._cache(key)
        if cached:
            return "blocked" if cached.get("disposition") == "blocked" else "captured"
        if issue.get("status") not in {"done", "blocked", "in_review"}:
            return "pending"
        runs = self.api.issue_runs(issue["id"])
        if not isinstance(runs, list):
            raise WorkbenchError("Unexpected Paperclip run-list contract")
        succeeded = sorted(
            (r for r in runs if r.get("status") == "succeeded"),
            key=lambda r: r.get("createdAt") or "",
        )
        # A worker records its disposition before its final message, so its
        # run can still be active when the task already reads done or blocked.
        active = any(r.get("status") in {"queued", "running", "scheduled_retry"} for r in runs)
        unfinished = "pending" if active or issue["status"] != "blocked" else "failed"
        detail = self.api.run(succeeded[0]["runId"]) if succeeded else {}
        text = str((detail.get("resultJson") or {}).get("result") or "").strip()
        if not text:
            if unfinished == "failed":
                self._note_failure(key.replace("report:", "failure:", 1), issue, runs)
            return unfinished
        usage = [
            {
                "run": r.get("runId"),
                "source": r.get("invocationSource"),
                "input_tokens": (r.get("usageJson") or {}).get("inputTokens"),
                "output_tokens": (r.get("usageJson") or {}).get("outputTokens"),
                "billing": (r.get("usageJson") or {}).get("billingType"),
            }
            for r in runs
        ]
        worker_blocked = issue["status"] == "blocked" and self._worker_blocked(issue)
        report = {
            "run": succeeded[0]["runId"],
            "agent": succeeded[0].get("agentId") or issue.get("assigneeAgentId"),
            "text": text[:20000],
            "truncated": len(text) > 20000,
            "sha256": digest(text),
            "usage": usage,
            "disposition": "blocked" if worker_blocked else "done",
        }
        if issue["status"] != "done" and not worker_blocked:
            self.api.update_issue(
                issue["id"],
                {
                    "status": "done",
                    "comment": "Assistant Workbench captured the worker report from run "
                    + succeeded[0]["runId"]
                    + ". Independent review is still required.",
                },
            )
        self._cache(key, report)
        with self.store.transaction() as db:
            self.store.event(
                db,
                "execution.node_reported",
                req["id"],
                {
                    "node": node,
                    "run": report["run"],
                    "agent": report["agent"],
                    "sha256": report["sha256"],
                    "characters": len(text),
                    "runs": len(runs),
                    "disposition": report["disposition"],
                },
            )
        return "blocked" if worker_blocked else "captured"

    def _note_failure(self, key, issue, runs):
        """Keep why the coordinator stopped a task that no worker reported
        on, for the attention list. A later report clears it."""
        latest = max(runs, key=lambda r: r.get("createdAt") or "", default={})
        failure = {
            "run": latest.get("runId"),
            "agent": latest.get("agentId") or issue.get("assigneeAgentId"),
            "code": latest.get("errorCode"),
            "reason": latest.get("livenessReason") or latest.get("error"),
        }
        cached = self._cache(key)
        if cached and {k: cached.get(k) for k in failure} == failure:
            return
        # The agent's own error usually names the fix, such as a missing sign-in.
        agent = next((a for a in self.api.agents() if a.get("id") == failure["agent"]), {})
        self._cache(key, {**failure, "agent_error": agent.get("errorReason")})

    def _worker_blocked(self, issue):
        """Whether the assigned worker itself set the task to blocked, rather
        than Paperclip after failed disposition repairs. The newest
        status-changing activity decides."""
        activity = self.api.issue_activity(issue["id"])
        if not isinstance(activity, list):
            raise WorkbenchError("Unexpected Paperclip activity contract")
        for row in sorted(activity, key=lambda r: r.get("createdAt") or "", reverse=True):
            change = ((row.get("details") or {}).get("changes") or {}).get("status") or {}
            if change or row.get("action") == "issue.disposition_repair_escalated":
                return (
                    row.get("actorType") == "agent"
                    and row.get("agentId") == issue.get("assigneeAgentId")
                    and change.get("to") == "blocked"
                )
        return False

    def _propose_worker_outcome(self, req, nodes):
        from .quality import Outcomes

        key = "proposed:" + req["id"] + ":" + str(req["revision"])
        if self._cache(key):
            return
        plan = json.loads(req["plan"]) if req.get("plan") else {"nodes": []}
        roles = {n["id"]: n["role"] for n in plan["nodes"]}
        sections, agents = [], []
        for node in nodes:
            report = self._cache("report:" + req["id"] + ":" + str(req["revision"]) + ":" + node)
            agents.append(str(report["agent"]))
            sections.append(
                "## " + node + " (" + roles.get(node, "worker") + ", run " + report["run"] + ")\n\n"
                + report["text"]
                + ("\n\n[Report truncated at 20,000 characters.]" if report["truncated"] else "")
            )
        # Outcome lists show the first line. Lead with the opening sentence of
        # the final step, which reviews the team's work, not a section header.
        depended = {d for n in plan["nodes"] for d in n.get("depends_on", [])}
        final = ([n for n in nodes if n not in depended] or nodes)[-1]
        lead = self._cache("report:" + req["id"] + ":" + str(req["revision"]) + ":" + final)["text"]
        headline = next(
            (line for line in (
                re.sub(r"[*_`]+", "", raw).strip(" >-") for raw in lead.splitlines()
                if not raw.lstrip().startswith("#")
            ) if line),
            req["title"],
        )[:200]
        outcome = Outcomes(self.store).propose(
            req["id"],
            headline + "\n\n" + "\n\n".join(sections),
            [],
            "paperclip-agent:" + ",".join(dict.fromkeys(agents)),
            req["revision"],
        )
        self._cache(key, {"outcome": outcome["id"]})

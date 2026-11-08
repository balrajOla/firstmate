import json
import pytest
from assistant_workbench.app import Application
from assistant_workbench.util import canonical


@pytest.fixture
def app(tmp_path):
    return Application(tmp_path / "state")


@pytest.fixture
def project(app, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    return app.store.add_project("Cedar", str(repo), ["cedar-app"])


@pytest.fixture
def configured(app):
    p = app.home / "config.json"
    c = json.loads(p.read_text())
    c["models"] = [
        {
            "id": "test-process",
            "harness": "process",
            "model": "fixture",
            "effort": "default",
            "efforts": ["default"],
            "auth": "local",
            "classes": ["all"],
            "verified": True,
            "fixture_only": True,
            "priority": 1,
            "adapter_config": {"command": "python", "args": ["-c", 'print("fixture")']},
        }
    ]
    p.write_text(canonical(c))
    return c


class FakePaperclip:
    """Contract fixture; deliberately not evidence of live Paperclip or LLM execution."""

    def __init__(self):
        self.projects_ = {}
        self.agents_ = []
        self.issues_ = {}
        self.wakes = []
        self.runs = {}
        self.activity = {}
        self.stop_ack = True

    def agents(self):
        return self.agents_[:]

    def experimental_settings(self):
        return {"enableIsolatedWorkspaces": True}

    def create_agent(self, body):
        item = {"id": "agent-" + str(len(self.agents_)), "status": "idle", **body}
        self.agents_.append(item)
        return item

    def create_project(self, body):
        key = body["idempotencyKey"]
        self.projects_.setdefault(
            key, {"id": "project-" + str(len(self.projects_)), **body}
        )
        return self.projects_[key]

    def create_issue(self, body):
        key = body["idempotencyKey"]
        self.issues_.setdefault(key, {"id": "issue-" + str(len(self.issues_)), **body})
        return self.issues_[key]

    def issue(self, identity):
        return next(i for i in self.issues_.values() if i["id"] == identity)

    def update_issue(self, identity, body):
        self.issue(identity).update(body)
        return self.issue(identity)

    def cancel_run(self, identity):
        self.runs[identity] = {
            "status": "cancelled",
            "resultJson": {
                "executionCancellation": {
                    "state": "acknowledged" if self.stop_ack else "requested"
                }
            },
        }
        return self.runs[identity]

    def run(self, identity):
        return self.runs[identity]

    def terminate_agent(self, identity):
        agent = next(a for a in self.agents_ if a["id"] == identity)
        agent["status"] = "terminated"
        return agent

    def agent_issues(self, identity):
        return [i for i in {i["id"]: i for i in self.issues_.values()}.values()
                if i.get("assigneeAgentId") == identity]

    def issue_runs(self, identity):
        return [r for r in self.runs.values() if r.get("issueId") == identity]

    def issue_activity(self, identity):
        return list(self.activity.get(identity, []))

    def finish(self, issue_id, status, results, agent="agent-0", by_worker=False):
        """Record runs for an issue as the coordinator would after execution.

        by_worker records the status change as the assigned worker's own;
        otherwise a blocked status is Paperclip's escalation after repairs."""
        for i, (outcome, text) in enumerate(results):
            run_id = issue_id + "-run-" + str(len(self.runs))
            self.runs[run_id] = {
                "runId": run_id, "issueId": issue_id, "agentId": agent,
                "status": outcome, "createdAt": "2026-11-08T10:00:0" + str(i),
                "invocationSource": "assignment" if i == 0 else "automation",
                "usageJson": {"inputTokens": 100 + i, "outputTokens": 10, "billingType": "subscription_included"},
                "resultJson": {"result": text} if text is not None else {},
            }
        issue = self.issue(issue_id)
        if by_worker:
            row = {"actorType": "agent", "agentId": issue.get("assigneeAgentId"),
                   "action": "issue.updated",
                   "details": {"changes": {"status": {"from": issue.get("status"), "to": status}}}}
        else:
            row = {"actorType": "system", "actorId": "recovery", "agentId": None,
                   "action": "issue.disposition_repair_escalated", "details": {"status": status}}
        self.activity.setdefault(issue_id, []).append(
            {"createdAt": "2026-11-08T10:01:0" + str(len(self.activity.get(issue_id, []))), **row})
        issue["status"] = status

    def wakeup(self, agent, body):
        prior = next(
            (w for w in self.wakes if w["idempotencyKey"] == body["idempotencyKey"]),
            None,
        )
        if prior:
            return prior
        item = {"id": "new-" + str(len(self.wakes)), **body}
        self.wakes.append(item)
        return item


class PinnedPaperclip(FakePaperclip):
    """Contract fixture for behaviors observed in the pinned Paperclip release
    (d69631b): official-CLI agents become its native runner unless runner is
    legacy, and that runner rejects bypass keys even when false
    (agent_runner_config_incompatible). Issue creation reuses a recent open issue
    with the same normalized title unless allowDuplicate is true, binding the
    new idempotency key to it (server/src/services/issues.ts). With isolated
    workspaces disabled it keeps isolated issues in the shared workspace. Its
    company agent list omits terminated agents (server/src/services/agents.ts).
    Not evidence of live Paperclip or model execution."""

    RUNNER_INCOMPATIBLE = ("dangerouslySkipPermissions", "dangerouslyBypassApprovalsAndSandbox")

    def agents(self):
        # The company agent list omits terminated agents by default.
        return [a for a in self.agents_ if a.get("status") != "terminated"]
    isolated_workspaces = True

    def experimental_settings(self):
        return {"enableIsolatedWorkspaces": self.isolated_workspaces}

    def create_agent(self, body):
        from assistant_workbench.paperclip import CoordinatorError

        if body.get("runner") == "legacy" or body["adapterType"] not in {"claude_local", "codex_local"}:
            return super().create_agent(body)
        # Without an explicit legacy runner, official-CLI adapters become the
        # native runner with its own permission default.
        if set(self.RUNNER_INCOMPATIBLE) & set(body.get("adapterConfig", {})):
            raise CoordinatorError("Paperclip returned HTTP 422 for POST /agents")
        native = {**body, "adapterType": "paperclip_runner"}
        native["adapterConfig"] = {**body["adapterConfig"], "provider": "acpx", "acpxPermissionMode": "approve-all"}
        return super().create_agent(native)

    def create_issue(self, body):
        key = body.get("idempotencyKey")
        if key in self.issues_:
            return {**self.issues_[key], "deduplicated": True, "deduplicationReason": "idempotency_key"}
        title = " ".join(body["title"].split()).lower()
        if not body.get("allowDuplicate"):
            for existing in self.issues_.values():
                if (" ".join(existing["title"].split()).lower() == title
                        and existing.get("status") not in {"done", "cancelled"}):
                    self.issues_[key] = existing
                    return {**existing, "deduplicated": True, "deduplicationReason": "recent_open_title"}
        item = {"id": "issue-" + str(len({i["id"] for i in self.issues_.values()})), **body}
        if not self.isolated_workspaces and "executionWorkspaceSettings" in item:
            # Disabled isolation is silently downgraded to the shared workspace.
            item["executionWorkspaceSettings"] = {"mode": "shared_workspace"}
        self.issues_[key] = item
        return item

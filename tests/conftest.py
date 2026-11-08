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
        self.stop_ack = True

    def agents(self):
        return self.agents_[:]

    def create_agent(self, body):
        item = {"id": "agent-" + str(len(self.agents_)), **body}
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
    (agent_runner_config_incompatible). Not
    evidence of live Paperclip or model execution."""

    RUNNER_INCOMPATIBLE = ("dangerouslySkipPermissions", "dangerouslyBypassApprovalsAndSandbox")

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

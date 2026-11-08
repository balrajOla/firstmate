"""Harness contracts use labelled doubles, not a real model qualification."""

import io
import json
import pytest

from assistant_workbench import profiles
from assistant_workbench.mcp import TOOLS, serve_stdio
from assistant_workbench.util import WorkbenchError


def call(app, name, arguments):
    sink = io.StringIO()
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    serve_stdio(app.home, io.StringIO(json.dumps(request) + "\n"), sink)
    return json.loads(sink.getvalue())


def test_mcp_new_project_execution_and_scoped_tools(app, project):
    projects = call(app, "awb_projects", {})
    assert project["id"] in projects["result"]["content"][0]["text"]
    workspace = call(app, "awb_execution_workspace", {"project": project["id"]})
    assert not workspace["result"]["isError"]
    blocked = call(
        app,
        "awb_backend_recall",
        {"backend": "hindsight", "project": project["id"], "query": "evidence"},
    )
    assert "error" in blocked and "not enabled" in blocked["error"]["message"]
    assert "awb_rollover" in TOOLS and "awb_recover" in TOOLS
    assert not any("token" in spec[2] for spec in TOOLS.values())


@pytest.mark.parametrize("value", [[], False, 0, "", None])
def test_mcp_rejects_nonobject_arguments(app, value):
    assert "error" in call(app, "awb_projects", value)


@pytest.mark.parametrize(
    "arguments",
    [
        {"text": 42},
        {"text": "test", "max_words": True},
        {"text": "test", "extra": "not in schema"},
    ],
)
def test_mcp_validates_advertised_types(app, arguments):
    assert "error" in call(app, "awb_writing_check", arguments)


def profile(app):
    return profiles.add(
        app.home,
        "canary-contract",
        "codex",
        "explicit-model",
        "low",
        "subscription",
        ["all"],
    )


def test_failed_reprobe_revokes_old_qualification(app, monkeypatch):
    model = profile(app)
    settings = json.loads((app.home / "config.json").read_text())
    settings["models"][0]["verified"] = True
    (app.home / "config.json").write_text(json.dumps(settings))
    monkeypatch.setattr(profiles.shutil, "which", lambda _: "/test/codex")

    def failed(*args, **kwargs):
        raise WorkbenchError("CLI authentication failed")

    monkeypatch.setattr(profiles, "run_process", failed)
    with pytest.raises(WorkbenchError, match="authentication"):
        profiles.probe(app.home, model["id"])
    saved = json.loads((app.home / "config.json").read_text())["models"][0]
    receipt = json.loads((app.home / "profiles/canary-contract.json").read_text())
    assert not saved["verified"] and receipt["result"] == "failed"
    assert not profiles.readiness(saved)[0]


@pytest.mark.parametrize(
    "mode", ["echo", "refusal", "incomplete", "failed_turn", "success"]
)
def test_probe_requires_exact_successful_assistant_completion(app, monkeypatch, mode):
    model = profile(app)
    monkeypatch.setattr(profiles.shutil, "which", lambda _: "/test/codex")

    def fixture(argv, **kwargs):
        if "--version" in argv:
            return {"stdout": "fixture-cli", "exit_code": 0}
        marker = argv[-1].split()[-1]
        text = marker if mode != "refusal" else "I cannot produce " + marker
        events = (
            [{"type": "user.input", "text": marker}]
            if mode == "echo"
            else [
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": text},
                }
            ]
        )
        if mode != "incomplete":
            events.append(
                {"type": "turn.failed" if mode == "failed_turn" else "turn.completed"}
            )
        return {"stdout": "\n".join(json.dumps(x) for x in events), "exit_code": 0}

    monkeypatch.setattr(profiles, "run_process", fixture)
    if mode == "success":
        assert profiles.probe(app.home, model["id"])["result"] == "passed"
    else:
        with pytest.raises(WorkbenchError):
            profiles.probe(app.home, model["id"])
        assert not json.loads((app.home / "config.json").read_text())["models"][0][
            "verified"
        ]

"""Bridge behavior against the pinned Paperclip contract fixture (not live)."""

import pytest
from assistant_workbench.engine import Engine
from conftest import PinnedPaperclip


def remote_issues(app, request_id):
    import json

    rows = app.store.rows("SELECT value FROM meta WHERE key=?", ("issues:" + request_id,))
    return json.loads(rows[0]["value"]) if rows else {}


def test_identical_prompts_keep_separate_remote_issues(app, project, configured):
    # Two separate requests may share their first 100 characters; Paperclip's
    # recent-title reuse must not merge their execution.
    text = "Add hello.py that prints a greeting and a pytest test for it."
    first = app.store.submit(text, "a", project["id"])["request_id"]
    second = app.store.submit(text, "b", project["id"])["request_id"]
    api = PinnedPaperclip()
    engine = Engine(app.store, api)
    assert engine.tick()["status"] == "delivered"
    assert engine.tick()["status"] == "delivered"
    one, two = remote_issues(app, first), remote_issues(app, second)
    assert one and two and not set(one.values()) & set(two.values())
    assert all(i.get("allowDuplicate") is True for i in api.issues_.values())


def test_reused_issue_belonging_to_other_work_is_refused(app, project, configured):
    first = app.store.submit("Fix the login bug", "a", project["id"])["request_id"]
    second = app.store.submit("Fix the signup bug", "b", project["id"])["request_id"]
    api = PinnedPaperclip()
    engine = Engine(app.store, api)
    assert engine.tick()["status"] == "delivered"
    # A coordinator may already bind a key to another request's issue (older
    # bridge titles, a different client, or ignored allowDuplicate).
    foreign = next(iter(api.issues_.values()))
    for node in ("implement", "verify"):
        api.issues_["awb-" + second + "-1:" + node] = foreign
    result = engine.tick()
    assert result["status"] == "blocked" and "different work" in result["error"]
    assert app.store.one("SELECT status FROM requests WHERE id=?", (second,))["status"] == "queued"
    assert remote_issues(app, second) == {}
    assert remote_issues(app, first)


def test_replay_of_own_issue_after_lost_cache_is_accepted(app, project, configured):
    request = app.store.submit("Fix bug", "a", project["id"])["request_id"]
    api = PinnedPaperclip()
    engine = Engine(app.store, api)
    assert engine.tick()["status"] == "delivered"
    original = remote_issues(app, request)
    # Simulate a crash after remote creation but before the local cache write.
    with app.store.transaction() as db:
        db.execute("DELETE FROM meta WHERE key=?", ("issues:" + request,))
        db.execute("UPDATE outbox SET status='retry' WHERE request_id=?", (request,))
    assert engine.tick()["status"] == "delivered"
    assert remote_issues(app, request) == original
    assert len({i["id"] for i in api.issues_.values()}) == 2


def official_cli_profile(app, configured, harness="claude", adapter_config=None):
    import json
    from assistant_workbench.util import now

    profile = configured["models"][0]
    profile.update(harness=harness, model="model-from-cli", effort="medium",
                   efforts=["medium"], auth="subscription")
    profile.pop("fixture_only", None)
    profile.pop("adapter_config", None)
    if adapter_config is not None:
        profile["adapter_config"] = adapter_config
    receipt = app.home / "probe.json"
    receipt.write_text(json.dumps({
        "result": "passed", "created": now(),
        "profile": {k: profile[k] for k in ("id", "harness", "model", "effort", "auth")},
    }))
    profile["qualification"] = str(receipt)
    (app.home / "config.json").write_text(json.dumps(configured))


def test_claude_workers_use_the_official_cli_runner_with_isolation(
    app, project, configured
):
    official_cli_profile(app, configured, adapter_config={"extraArgs": ["--allowedTools", "Bash(pytest:*)"]})
    app.store.submit("Fix bug", "a", project["id"])
    api = PinnedPaperclip()
    assert Engine(app.store, api).tick()["status"] == "delivered"
    agent = api.agents_[0]
    config = agent["adapterConfig"]
    assert agent["adapterType"] == "claude_local" and agent["runner"] == "legacy"
    assert config["engine"] == "cli" and config["dangerouslySkipPermissions"] is False
    # Isolation defaults come first; the profile may add narrower grants.
    assert config["extraArgs"] == [
        "--bare", "--permission-mode", "acceptEdits", "--allowedTools", "Bash(pytest:*)"
    ]
    assert config["model"] == "model-from-cli" and config["effort"] == "medium"


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_converted_worker_agent_is_refused(app, project, configured, harness):
    official_cli_profile(app, configured, harness)
    app.store.submit("Fix bug", "a", project["id"])
    api = PinnedPaperclip()
    original = api.create_agent

    def ignores_runner(body):
        # A coordinator that silently ignores the runner choice.
        return original({k: v for k, v in body.items() if k != "runner"} | {
            "adapterConfig": {k: v for k, v in body["adapterConfig"].items()
                              if k not in PinnedPaperclip.RUNNER_INCOMPATIBLE}})

    api.create_agent = ignores_runner
    engine = Engine(app.store, api)
    result = engine.tick()
    assert result["status"] == "blocked" and "paperclip_runner" in result["error"]
    assert not api.issues_
    # The converted agent stays visible and is refused again on reuse.
    request = app.store.one("SELECT id FROM requests")["id"]
    engine.retry(request)
    result = engine.tick()
    assert result["status"] == "blocked" and "paperclip_runner" in result["error"]
    assert not api.issues_


def test_worker_reuse_requires_explicitly_disabled_bypass(app, project, configured):
    official_cli_profile(app, configured)
    first = app.store.submit("Fix bug", "a", project["id"])["request_id"]
    api = PinnedPaperclip()
    engine = Engine(app.store, api)
    assert engine.tick()["status"] == "delivered"
    for agent in api.agents_:
        agent["adapterConfig"]["dangerouslySkipPermissions"] = True
    app.store.submit("Fix another bug", "b", project["id"])
    result = engine.tick()
    assert result["status"] == "blocked" and "bypass" in result["error"]
    assert first


def test_changed_worker_definition_is_not_served_by_an_old_agent(
    app, project, configured, monkeypatch
):
    from assistant_workbench import engine as engine_module

    official_cli_profile(app, configured)
    app.store.submit("Fix bug", "a", project["id"])
    api = PinnedPaperclip()
    assert Engine(app.store, api).tick()["status"] == "delivered"
    first = {a["id"] for a in api.agents_}
    # A bridge upgrade changes the default worker flags (or instructions).
    monkeypatch.setattr(engine_module, "CLAUDE_WORKER_ARGS", engine_module.CLAUDE_WORKER_ARGS + ["--new-default"])
    app.store.submit("Fix another bug", "b", project["id"])
    assert Engine(app.store, api).tick()["status"] == "delivered"
    second = {a["id"] for a in api.agents_} - first
    assert len(second) == 2
    assert all("--new-default" in a["adapterConfig"]["extraArgs"]
               for a in api.agents_ if a["id"] in second)


def test_terminated_agents_do_not_hold_pool_capacity(app, project, configured):
    configured["limits"]["max_agents"] = 2
    (app.home / "config.json").write_text(__import__("json").dumps(configured))
    api = PinnedPaperclip()
    for i in range(2):
        api.agents_.append({"id": "old-" + str(i), "status": "terminated",
                            "metadata": {"workbenchKey": "awb:retired-" + str(i)},
                            "adapterType": "process", "adapterConfig": {}})
    app.store.submit("Fix bug", "a", project["id"])
    assert Engine(app.store, api).tick()["status"] == "delivered"

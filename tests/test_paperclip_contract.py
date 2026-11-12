"""Bridge behavior against the pinned Paperclip contract fixture (not live)."""

import json

from assistant_workbench.util import WorkbenchError

import pytest
from assistant_workbench.engine import Engine
from conftest import FakePaperclip, PinnedPaperclip


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


def official_cli_profile(app, configured, harness="claude", adapter_config=None, auth="subscription"):
    import json
    from assistant_workbench.util import now

    profile = configured["models"][0]
    profile.update(harness=harness, model="model-from-cli", effort="medium",
                   efforts=["medium"], auth=auth)
    profile.pop("fixture_only", None)
    profile.pop("adapter_config", None)
    if adapter_config is not None:
        profile["adapter_config"] = adapter_config
    receipt = app.home / "probe.json"
    from assistant_workbench.profiles import claude_sign_in

    receipt.write_text(json.dumps({
        "result": "passed", "created": now(),
        "profile": {k: profile[k] for k in ("id", "harness", "model", "effort", "auth")},
        **({"claude_sign_in": claude_sign_in(profile)} if harness == "claude" else {}),
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
    from assistant_workbench.engine import CLAUDE_WORKER_ARGS, disposition_command
    from assistant_workbench.profiles import CLAUDE_SIGN_IN

    assert config["extraArgs"] == CLAUDE_SIGN_IN["account"] + CLAUDE_WORKER_ARGS + [
        "--allowedTools", "Bash(" + disposition_command() + ":*)",
        "--allowedTools", "Bash(pytest:*)",
    ]
    # The instructions name exactly the commands the worker is allowed to run,
    # including the profile's grant, so it does not guess at refused ones.
    instructions = agent["instructionsBundle"]["files"]["AGENTS.md"]
    assert "`" + disposition_command() + " done`" in instructions
    assert ("you can run only `git status`, `git diff`, `git add`, `git commit`, `"
            + disposition_command() + "`, `pytest`. Other commands are refused") in instructions
    # A subscription worker keeps the CLI's own sign-in but loads no user or
    # project settings and no MCP servers.
    assert config["extraArgs"][:6] == ["--setting-sources", "", "--strict-mcp-config", "--disable-slash-commands",
                                       "--permission-mode", "acceptEdits"]
    # Without it the CLI lists every skill at start-up, and Paperclip's sign-in
    # check took a skill description containing "unauthorized" for a failed
    # login and paused the agent.
    assert "--disable-slash-commands" in config["extraArgs"]
    assert "--bare" not in config["extraArgs"]
    assert not any("push" in a for a in config["extraArgs"])
    assert config["model"] == "model-from-cli" and config["effort"] == "medium"
    # Paperclip fails a finished run whose CLI is still open 5 s after its
    # final message; observed live, managed plugins kept it open about 12 s.
    assert config["terminalResultCleanupGraceMs"] == 30_000


def test_codex_workers_use_the_official_cli_engine(app, project, configured):
    official_cli_profile(app, configured, "codex")
    app.store.submit("Fix bug", "a", project["id"])
    api = PinnedPaperclip()
    assert Engine(app.store, api).tick()["status"] == "delivered"
    agent = api.agents_[0]
    config = agent["adapterConfig"]
    # Unset, the pinned adapter runs its bundled ACP server, not the codex CLI
    # and its sign-in (packages/adapters/codex-local/src/index.ts).
    assert agent["adapterType"] == "codex_local" and agent["runner"] == "legacy"
    assert config["engine"] == "cli"
    assert config["dangerouslyBypassApprovalsAndSandbox"] is False
    assert config["modelReasoningEffort"] == "medium"


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_agent_moved_off_the_cli_engine_is_refused(app, project, configured, harness):
    official_cli_profile(app, configured, harness)
    app.store.submit("Fix bug", "a", project["id"])
    api = PinnedPaperclip()
    original = api.create_agent

    def default_engine(body):
        # A coordinator that drops the engine choice falls back to ACP.
        config = {k: v for k, v in body["adapterConfig"].items() if k != "engine"}
        return original(body | {"adapterConfig": config})

    api.create_agent = default_engine
    result = Engine(app.store, api).tick()
    assert result["status"] == "blocked" and "CLI engine" in result["error"]
    assert not api.issues_


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


@pytest.mark.parametrize("harness,adapter_config", [
    ("claude", {"dangerouslySkipPermissions": True}),
    ("claude", {"extraArgs": ["--dangerously-skip-permissions"]}),
    ("claude", {"extraArgs": ["--permission-mode", "bypassPermissions"]}),
    ("codex", {"dangerouslyBypassApprovalsAndSandbox": True}),
    ("codex", {"extraArgs": ["--sandbox=danger-full-access"]}),
], ids=["claude-key", "claude-skip-flag", "claude-permission-mode", "codex-key", "codex-full-access"])
def test_profile_bypass_is_refused_before_any_agent_exists(
    app, project, configured, harness, adapter_config
):
    official_cli_profile(app, configured, harness, adapter_config)
    app.store.submit("Fix bug", "a", project["id"])
    api = PinnedPaperclip()
    result = Engine(app.store, api).tick()
    assert result["status"] == "blocked" and "bypass" in result["error"]
    assert not api.agents_ and not api.issues_


def test_subscription_workers_never_inherit_a_provider_api_key(app, project, configured):
    official_cli_profile(app, configured)
    app.store.submit("Fix bug", "a", project["id"])
    api = PinnedPaperclip()
    assert Engine(app.store, api).tick()["status"] == "delivered"
    env = api.agents_[0]["adapterConfig"]["env"]
    assert env == {"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "", "AZURE_OPENAI_API_KEY": ""}


def test_api_profiles_keep_their_own_provider_environment(app, project, configured):
    official_cli_profile(app, configured, auth="api")
    app.store.submit("Fix bug", "a", project["id"])
    api = PinnedPaperclip()
    assert Engine(app.store, api).tick()["status"] == "delivered"
    assert "env" not in api.agents_[0]["adapterConfig"]


def test_subscription_profile_passing_a_provider_key_is_refused(app, project, configured):
    official_cli_profile(app, configured, adapter_config={"env": {"ANTHROPIC_API_KEY": "sk-test"}})
    app.store.submit("Fix bug", "a", project["id"])
    api = PinnedPaperclip()
    result = Engine(app.store, api).tick()
    assert result["status"] == "blocked" and "provider API key" in result["error"]
    assert not api.agents_


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


def delivered_code_request(app, project, configured):
    request = app.store.submit("Fix the login bug", "a", project["id"])["request_id"]
    api = PinnedPaperclip()
    engine = Engine(app.store, api)
    assert engine.tick()["status"] == "delivered"
    issues = remote_issues(app, request)
    return request, api, engine, issues["1:implement"], issues["1:verify"]


def test_worker_reports_become_one_unverified_outcome(app, project, configured):
    request, api, engine, implement, verify = delivered_code_request(app, project, configured)
    # Without permission bypass the worker cannot close its own task: Paperclip
    # repairs twice and then blocks it, although the first run succeeded.
    api.finish(implement, "blocked", [("succeeded", "Changed login.py; tests pass."),
                                      ("succeeded", "Could not record disposition."),
                                      ("succeeded", "Still cannot record disposition.")])
    api.finish(verify, "done", [("succeeded", "Verified: 3 tests pass.")], agent="agent-1")
    observed = engine.observe()[request]
    assert {n["report"] for n in observed.values()} == {"captured"}
    assert api.issue(implement)["status"] == "done"
    outcome = app.store.one("SELECT * FROM outcomes WHERE request_id=?", (request,))
    assert outcome["status"] == "needs_review"
    assert outcome["author"] == "paperclip-agent:agent-0,agent-1"
    assert "Changed login.py; tests pass." in outcome["body"]
    assert "Verified: 3 tests pass." in outcome["body"]
    # Lists show the first line: the final reviewer's result, not a header.
    assert outcome["body"].splitlines()[0] == "Verified: 3 tests pass."
    assert "Could not record disposition" not in outcome["body"]
    assert app.store.one("SELECT status FROM requests WHERE id=?", (request,))["status"] == "needs_review"
    engine.observe()
    assert len(app.store.rows("SELECT id FROM outcomes WHERE request_id=?", (request,))) == 1
    reported = app.store.rows("SELECT payload FROM events WHERE kind='execution.node_reported'")
    assert len(reported) == 2


def test_outcome_headline_skips_headers_and_markup(app, project, configured):
    request, api, engine, implement, verify = delivered_code_request(app, project, configured)
    api.finish(implement, "done", [("succeeded", "Changed login.py.")], by_worker=True)
    api.finish(verify, "done", [("succeeded", "## QA report\n\n**All 3 login tests pass** on the branch.")],
               agent="agent-1", by_worker=True)
    engine.observe()
    outcome = app.store.one("SELECT body FROM outcomes WHERE request_id=?", (request,))
    assert outcome["body"].splitlines()[0] == "All 3 login tests pass on the branch."


@pytest.mark.parametrize("auth, sign_in, expected", [
    ("subscription", None, ["--setting-sources", "", "--strict-mcp-config", "--disable-slash-commands"]),
    ("subscription", "key", ["--bare"]),
    ("api", None, ["--bare"]),
    ("api", "account", ["--setting-sources", "", "--strict-mcp-config", "--disable-slash-commands"]),
], ids=["subscription-default", "subscription-key", "api-default", "api-account"])
def test_claude_workers_sign_in_the_way_the_profile_says(app, project, configured, auth, sign_in, expected):
    official_cli_profile(app, configured, auth=auth)
    if sign_in:
        configured["models"][0]["claude_sign_in"] = sign_in
        official_cli_profile(app, configured, auth=auth)
    app.store.submit("Fix bug", "a", project["id"])
    api = PinnedPaperclip()
    assert Engine(app.store, api).tick()["status"] == "delivered"
    args = api.agents_[0]["adapterConfig"]["extraArgs"]
    assert args[:len(expected)] == expected and args[len(expected)] == "--permission-mode"
    # Exactly one mode: --bare never reads an account sign-in.
    assert ("--bare" in args) != ("--setting-sources" in args)


def test_granted_commands_follow_the_cli_argument_forms():
    from assistant_workbench.engine import granted_commands

    assert granted_commands([
        "--bare", "--allowedTools", "Bash(git status:*)", "Edit", "Bash(npm test)",
        "--model", "Bash(not-a-grant:*)", "--allowed-tools=Bash(/v/bin/python -m pytest:*) Read",
        "--allowedTools", "Bash(git status:*),Bash(cargo test:*)",
    ]) == ["git status", "npm test", "/v/bin/python -m pytest", "cargo test"]
    # An unscoped grant allows every command, in any of the CLI's forms.
    for grant in (["--allowedTools", "Bash"], ["--allowedTools", "Edit,Bash"], ["--allowedTools", "Edit Bash Write"],
                  ["--allowedTools=Bash(*)"], ["--allowedTools", "Bash(:*)"]):
        assert granted_commands(["--allowedTools", "Bash(git status:*)", *grant]) == ["*"], grant
    for narrow in (["--allowedTools", "BashOutput"], ["--model", "Bash"], ["--allowedTools", "Bash(npm *)"]):
        assert granted_commands(narrow) != ["*"], narrow


def test_a_profile_that_grants_every_command_says_so_to_its_workers(app, project, configured):
    official_cli_profile(app, configured, adapter_config={"extraArgs": ["--allowedTools", "Bash", "Edit", "Write"]})
    app.store.submit("Fix bug", "a", project["id"])
    api = PinnedPaperclip()
    assert Engine(app.store, api).tick()["status"] == "delivered"
    agent = api.agents_[0]
    instructions = agent["instructionsBundle"]["files"]["AGENTS.md"]
    assert "run any command the task needs" in instructions
    assert "you can run only" not in instructions
    # The grant widens tools; it never turns on a permission bypass.
    config = agent["adapterConfig"]
    assert config["dangerouslySkipPermissions"] is False
    assert config["extraArgs"][config["extraArgs"].index("--permission-mode") + 1] == "acceptEdits"


def test_worker_instructions_ask_for_a_leading_result_sentence(app, project, configured):
    official_cli_profile(app, configured)
    app.store.submit("Fix bug", "a", project["id"])
    api = PinnedPaperclip()
    Engine(app.store, api).tick()
    text = api.agents_[0]["instructionsBundle"]["files"]["AGENTS.md"]
    assert "Begin it with one sentence that states the result" in text


def test_partial_reports_unblock_dependents_without_an_outcome(app, project, configured):
    request, api, engine, implement, verify = delivered_code_request(app, project, configured)
    api.finish(implement, "blocked", [("succeeded", "Changed login.py.")])
    observed = engine.observe()[request]
    assert observed["implement"]["report"] == "captured"
    assert observed["verify"]["report"] == "pending"
    assert api.issue(implement)["status"] == "done"
    assert not app.store.rows("SELECT id FROM outcomes WHERE request_id=?", (request,))


def test_failed_or_empty_runs_are_never_captured(app, project, configured):
    request, api, engine, implement, verify = delivered_code_request(app, project, configured)
    api.finish(implement, "blocked", [("failed", "partial output"), ("succeeded", "")])
    observed = engine.observe()[request]
    assert observed["implement"]["report"] == "failed"
    assert api.issue(implement)["status"] == "blocked"
    assert not app.store.rows("SELECT id FROM outcomes WHERE request_id=?", (request,))


def test_task_stopped_by_the_coordinator_needs_the_operator(app, project, configured):
    from assistant_workbench.terminal import status

    request, api, engine, implement, verify = delivered_code_request(app, project, configured)
    # Observed live: Paperclip refused to start a Codex worker without a
    # sign-in, failed the run and blocked the task. No worker reported.
    agent = api.issue(implement)["assigneeAgentId"]
    next(a for a in api.agents_ if a["id"] == agent)["errorReason"] = "no Codex credentials available"
    api.finish(implement, "blocked", [("failed", None)], agent=agent)
    run = next(r for r in api.runs.values() if r["issueId"] == implement)
    run.update(errorCode="configuration_incomplete", livenessReason="Run ended with failed (configuration_incomplete)")
    assert engine.observe()[request]["implement"]["report"] == "failed"
    engine.observe()
    assert app.dispatch("request.get", {"request_id": request})["status"] == "running"
    [item] = app.store.snapshot()["attention"]
    assert (item["request_id"], item["status"], item["nodes"]) == (request, "execution_failed", ["implement"])
    assert "implement: configuration_incomplete - no Codex credentials available" in item["error"]
    assert "resume the task in the coordinator or amend the request" in item["error"]
    assert status(app.store)["blocked"] == 1
    events = app.store.rows("SELECT payload FROM events WHERE kind='execution.node_failed'")
    assert len(events) == 1 and json.loads(events[0]["payload"])["nodes"] == ["implement"]
    # The operator fixed the sign-in and resumed the task; its worker reported.
    api.finish(implement, "blocked", [("succeeded", "Changed login.py.")])
    assert engine.observe()[request]["implement"]["report"] == "captured"
    assert app.store.snapshot()["attention"] == []


def test_repository_work_requires_isolated_workspaces(app, project, configured):
    # Paperclip downgrades isolated_workspace to the project's own checkout
    # when its experimental setting is off; workers would edit the repository.
    request = app.store.submit("Fix the login bug", "a", project["id"])["request_id"]
    api = PinnedPaperclip()
    api.isolated_workspaces = False
    result = Engine(app.store, api).tick()
    assert result["status"] == "blocked" and "isolated workspaces" in result["error"]
    assert not api.issues_ and not api.agents_ and not api.projects_
    assert app.store.one("SELECT status FROM requests WHERE id=?", (request,))["status"] == "queued"


def test_downgraded_workspace_issue_is_cancelled(app, project, configured):
    request = app.store.submit("Fix the login bug", "a", project["id"])["request_id"]
    api = PinnedPaperclip()
    original = api.create_issue

    def downgrades(body):
        api.isolated_workspaces = False
        try:
            return original(body)
        finally:
            api.isolated_workspaces = True

    api.create_issue = downgrades
    result = Engine(app.store, api).tick()
    assert result["status"] == "blocked" and "isolated workspace" in result["error"]
    assert [i["status"] for i in {i["id"]: i for i in api.issues_.values()}.values()] == ["cancelled"]
    assert remote_issues(app, request) == {}


def test_unbound_project_work_does_not_need_isolation(app, configured):
    unbound = app.store.add_project("Notes")
    app.store.submit("Summarize the notes", "a", unbound["id"])
    api = PinnedPaperclip()
    api.isolated_workspaces = False
    assert Engine(app.store, api).tick()["status"] == "delivered"


def test_dependents_start_from_the_predecessor_branch(app, project, configured):
    from assistant_workbench.engine import task_branch

    request, api, engine, implement, verify = delivered_code_request(app, project, configured)
    # Branches are fixed at creation, so the reviewer starts from the
    # implementer's commits even if Paperclip wakes it the moment the
    # implementer records done, before the Workbench observes anything.
    branch = task_branch(request, 1, "implement")
    assert api.issue(implement)["executionWorkspaceSettings"] == {
        "mode": "isolated_workspace",
        "workspaceStrategy": {"type": "git_worktree", "branchTemplate": branch},
    }
    assert api.issue(verify)["executionWorkspaceSettings"] == {
        "mode": "isolated_workspace",
        "workspaceStrategy": {
            "type": "git_worktree",
            "branchTemplate": task_branch(request, 1, "verify"),
            "baseRef": branch,
        },
    }
    assert "inheritExecutionWorkspaceFromIssueId" not in api.issue(verify)


def test_amendment_stops_earlier_revision_work_before_planning(app, project, configured, monkeypatch):
    import assistant_workbench.engine as engine_module

    request, api, engine, implement, verify = delivered_code_request(app, project, configured)
    original = engine_module.compile_plan
    seen = []

    def plan_after_checking(*args, **kwargs):
        # Planning can wait on a routing model; the old worker must already
        # have been told to stop by then.
        seen.append({api.issue(i)["status"] for i in (implement, verify)})
        return original(*args, **kwargs)

    monkeypatch.setattr(engine_module, "compile_plan", plan_after_checking)
    app.store.submit("Also cover the logout path.", "amend", reply_to=request, expected_revision=1)
    assert engine.tick()["status"] == "delivered"
    assert seen == [{"cancelled"}]


def test_amendment_that_cannot_dispatch_still_stops_earlier_work(app, project, configured):
    request, api, engine, implement, verify = delivered_code_request(app, project, configured)
    app.store.submit("Checklist: " + "keep keyboard access. " * 3000, "amend", reply_to=request,
                     expected_revision=1)
    result = engine.tick()
    assert result["status"] == "blocked" and "character budget" in result["error"]
    assert {api.issue(i)["status"] for i in (implement, verify)} == {"cancelled"}


def test_task_branches_are_valid_and_distinct_per_revision():
    from assistant_workbench.engine import task_branch

    first = task_branch("r_0a1b", 1, "research a")
    assert first == "awb/r_0a1b-r1-research-a"
    assert task_branch("r_0a1b", 2, "research a") != first
    assert len(task_branch("r_" + "x" * 200, 1, "implement")) <= 120


def test_multi_dependency_nodes_keep_their_own_workspace(app, project, configured):
    request = app.store.submit("Research the project", "a", project["id"])["request_id"]
    api = PinnedPaperclip()
    engine = Engine(app.store, api)
    assert engine.tick()["status"] == "delivered"
    issues = remote_issues(app, request)
    strategy = api.issue(issues["1:synthesize"])["executionWorkspaceSettings"]["workspaceStrategy"]
    assert "baseRef" not in strategy and strategy["branchTemplate"].endswith("-synthesize")


def test_worker_reported_block_keeps_dependents_blocked(app, project, configured):
    request, api, engine, implement, verify = delivered_code_request(app, project, configured)
    api.finish(implement, "blocked", [("succeeded", "Blocked: the login service is missing.")],
               by_worker=True)
    observed = engine.observe()[request]
    assert observed["implement"]["report"] == "blocked"
    # The worker's own decision stands: its task is not closed for it.
    assert api.issue(implement)["status"] == "blocked"
    assert not app.store.rows("SELECT id FROM outcomes WHERE request_id=?", (request,))
    events = app.store.rows("SELECT payload FROM events WHERE kind='execution.node_blocked'")
    assert len(events) == 1 and json.loads(events[0]["payload"])["nodes"] == ["implement"]
    engine.observe()
    assert len(app.store.rows("SELECT payload FROM events WHERE kind='execution.node_blocked'")) == 1
    report = app.store.rows("SELECT payload FROM events WHERE kind='execution.node_reported'")
    assert json.loads(report[0]["payload"])["disposition"] == "blocked"
    # Only an amendment re-runs the team; retry says it has nothing to do.
    assert "amend the request with what would unblock it" in json.loads(events[0]["payload"])["notice"]
    retried = app.dispatch("request.retry", {"request_id": request})
    assert retried["reset"] == 0 and "No blocked delivery to retry" in retried["notice"]
    assert api.issue(implement)["status"] == "blocked"


def test_worker_reported_block_needs_the_operator(app, project, configured):
    from assistant_workbench.terminal import status

    request, api, engine, implement, verify = delivered_code_request(app, project, configured)
    assert status(app.store)["blocked"] == 0
    api.finish(implement, "blocked", [("succeeded", "Blocked: the login service is missing.")],
               by_worker=True)
    engine.observe()
    # No delivery failed and the request still reads running, yet nothing
    # will move it until the operator amends or cancels it.
    assert app.dispatch("request.get", {"request_id": request})["status"] == "running"
    attention = app.store.snapshot()["attention"]
    assert [(a["request_id"], a["status"], a["nodes"]) for a in attention] == [
        (request, "worker_blocked", ["implement"])]
    assert "amend the request with what would unblock it" in attention[0]["error"]
    assert status(app.store)["blocked"] == 1
    # An amendment starts a new revision, which is not blocked.
    app.store.submit("Use the login stub in tests/.", "amend", reply_to=request, expected_revision=1)
    assert app.store.snapshot()["attention"] == []


def test_disposition_recorded_before_the_final_message_waits_for_the_run(app, project, configured):
    request, api, engine, implement, verify = delivered_code_request(app, project, configured)
    api.finish(implement, "blocked", [("running", None)], by_worker=True)
    assert engine.observe()[request]["implement"]["report"] == "pending"
    assert not app.store.rows("SELECT payload FROM events WHERE kind='execution.node_blocked'")
    run = next(r for r in api.runs.values() if r["issueId"] == implement)
    run.update(status="succeeded", resultJson={"result": "Blocked: the file is missing."})
    assert engine.observe()[request]["implement"]["report"] == "blocked"


def test_paperclip_escalation_is_not_a_worker_block(app, project, configured):
    request, api, engine, implement, verify = delivered_code_request(app, project, configured)
    # A worker blocked earlier, then Paperclip escalated a later repair: the
    # newest status change is Paperclip's, so the report is captured.
    api.finish(implement, "blocked", [("succeeded", "Changed login.py.")], by_worker=True)
    api.finish(implement, "blocked", [])
    assert engine.observe()[request]["implement"]["report"] == "captured"
    assert api.issue(implement)["status"] == "done"


def test_role_agents_run_one_task_at_a_time_unless_configured(app, project, configured):
    import json as _json

    official_cli_profile(app, configured)
    app.store.submit("Fix bug", "a", project["id"])
    api = PinnedPaperclip()
    assert Engine(app.store, api).tick()["status"] == "delivered"
    default = {a["id"]: a for a in api.agents_}
    assert {a["runtimeConfig"]["heartbeat"]["maxConcurrentRuns"] for a in default.values()} == {1}
    # The same role then serves every request, so independent tasks of that
    # role run one after another. A higher limit gets new agents that allow it.
    configured["coordinator"]["max_concurrent_runs"] = 3
    (app.home / "config.json").write_text(_json.dumps(configured))
    app.store.submit("Fix another bug", "b", project["id"])
    assert Engine(app.store, api).tick()["status"] == "delivered"
    added = [a for a in api.agents_ if a["id"] not in default]
    assert added and {a["runtimeConfig"]["heartbeat"]["maxConcurrentRuns"] for a in added} == {3}
    assert {a["metadata"]["workbenchKey"] for a in added}.isdisjoint(
        a["metadata"]["workbenchKey"] for a in default.values())


@pytest.mark.parametrize("runs", [0, 51, "2", True])
def test_invalid_concurrent_run_limit_is_refused(app, configured, runs):
    import json as _json

    from assistant_workbench.config import config

    configured["coordinator"]["max_concurrent_runs"] = runs
    (app.home / "config.json").write_text(_json.dumps(configured))
    with pytest.raises(WorkbenchError, match="max_concurrent_runs"):
        config(app.home)


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
    # Some coordinators still list terminated agents.
    api = FakePaperclip()
    for i in range(2):
        api.agents_.append({"id": "old-" + str(i), "status": "terminated",
                            "metadata": {"workbenchKey": "awb:retired-" + str(i)},
                            "adapterType": "process", "adapterConfig": {}})
    app.store.submit("Fix bug", "a", project["id"])
    assert Engine(app.store, api).tick()["status"] == "delivered"


def test_workers_see_the_projects_earlier_requests_and_results(app, project, configured):
    from assistant_workbench.quality import Outcomes

    official_cli_profile(app, configured)
    api = PinnedPaperclip()
    engine = Engine(app.store, api)
    first = app.store.submit("Add a login form", "h1", project["id"])["request_id"]
    engine.tick()
    Outcomes(app.store).propose(first, "The login form validates email and password.\nDetails follow.", [], "fixture", 1)
    dropped = app.store.submit("Add a dark mode", "h2", project["id"])["request_id"]
    app.store.cancel(dropped)
    second = app.store.submit("Add a password reset link to the login form", "h3", project["id"])["request_id"]
    for _ in range(4):  # the cancellation and the delivery each take a tick
        engine.tick()
    app.store.submit("Add a footer", "h4", project["id"])
    description = next(i["description"] for i in api.issues_.values() if second in i["idempotencyKey"])
    history = description.split("## Project history\n", 1)[1].split("\n\n## ", 1)[0]
    assert "Add a login form (needs_review, ticket " + first + ")" in history
    assert "Result (unverified): The login form validates email and password." in history
    assert "Details follow" not in history and "dark mode" not in history
    # Requests saved later never change an earlier request's task text.
    assert "footer" not in description
    # The first request had no history.
    first_description = next(i["description"] for i in api.issues_.values() if first in i["idempotencyKey"])
    assert "## Project history" not in first_description


def test_project_history_fits_its_share_of_the_context_budget(app, project, configured):
    official_cli_profile(app, configured)
    for n in range(8):
        app.store.submit("Earlier request " + str(n) + " " + "x" * 150, "e" + str(n), project["id"])
    request = app.store.submit("The newest request", "new", project["id"])["request_id"]
    engine = Engine(app.store, PinnedPaperclip())
    req = app.store.one("SELECT * FROM requests WHERE id=?", (request,))
    history = engine._project_history(req, project, 600)
    assert 0 < len(history) - len(history.split("\n- ", 1)[0]) <= 600 + 2
    assert history.count("\n- ") < 8 and "Earlier request 7" in history


def test_a_profile_can_set_its_own_claude_exit_grace(app, project, configured):
    official_cli_profile(app, configured, adapter_config={"terminalResultCleanupGraceMs": 60_000})
    app.store.submit("Fix bug", "g", project["id"])
    api = PinnedPaperclip()
    assert Engine(app.store, api).tick()["status"] == "delivered"
    assert api.agents_[0]["adapterConfig"]["terminalResultCleanupGraceMs"] == 60_000

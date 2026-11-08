"""Contract tests plus opt-in real pinned-package tests; never synthetic LLM evidence."""

from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import threading
import time

import pytest

from assistant_workbench.integrations import Integrations, run_process
from assistant_workbench.util import WorkbenchError, digest


def configure(app, name, settings):
    path = app.home / "config.json"
    config = json.loads(path.read_text())
    config["integrations"][name] = {"enabled": True, **settings}
    path.write_text(json.dumps(config))
    return Integrations(app.store)


def command_result(data, code=0):
    return {"stdout": json.dumps(data), "stderr": "", "exit_code": code, "seconds": 0}


@pytest.fixture
def memory_command(monkeypatch):
    calls = []

    def invoke(argv, **kw):
        calls.append((argv, kw))
        pid = argv[argv.index("--project") + 1]
        root = Path(kw["env"]["BASIC_MEMORY_HOME"]).parents[2] / "knowledge" / pid
        root.mkdir(parents=True, exist_ok=True)
        action = argv[argv.index("tool") + 1]
        name = (
            argv[argv.index("--identifier") + 1]
            if action == "write"
            else argv[argv.index("read") + 1]
        )
        target = root / name
        if action == "write":
            checksum = (
                argv[argv.index("--expected-checksum") + 1]
                if "--expected-checksum" in argv
                else None
            )
            if target.exists() and checksum != digest(target.read_bytes()):
                return command_result({"error": "NOTE_REVISION_CONFLICT"}, 1)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(kw["input_text"])
        return command_result(
            {
                "file_path": name,
                "content": target.read_text(),
                "checksum": digest(target.read_bytes()),
            }
        )

    monkeypatch.setattr("assistant_workbench.integrations.run_process", invoke)
    return calls


def test_basic_memory_checksum_guards_concurrent_replacement(
    app, project, memory_command
):
    api = configure(app, "basic-memory", {"command": ["bm"], "allow_write": True})
    first = api.basic_memory(
        project["id"],
        "write",
        identifier="decision.md",
        content="Refunds are excluded.",
    )
    with pytest.raises(WorkbenchError, match="checksum"):
        api.basic_memory(
            project["id"], "write", identifier="decision.md", content="Blind overwrite"
        )
    checksum = first["data"]["checksum"]

    def change(text):
        try:
            return api.basic_memory(
                project["id"],
                "write",
                identifier="decision.md",
                content=text,
                checksum=checksum,
            )["exit_code"]
        except WorkbenchError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                change,
                [
                    "Refunds still excluded; tax added.",
                    "Refunds still excluded; shipping added.",
                ],
            )
        )
    assert sorted(map(str, results)) == ["0", "conflict"]
    assert all("--content" not in argv for argv, _ in memory_command)
    assert all("--local" in argv for argv, _ in memory_command)


@pytest.mark.parametrize(
    "identifier",
    [
        "../other.md",
        "/tmp/other.md",
        "memory://other/secret.md",
        "other\\secret.md",
        "notes/%2e%2e/x.md",
        "-x.md",
        "a//x.md",
        "note",
    ],
)
def test_basic_memory_rejects_scope_escape_before_execution(
    app, project, identifier, monkeypatch
):
    api = configure(app, "basic-memory", {"command": ["bm"], "allow_write": True})
    monkeypatch.setattr(
        "assistant_workbench.integrations.run_process",
        lambda *a, **kw: pytest.fail("Unsafe path reached process"),
    )
    with pytest.raises(WorkbenchError):
        api.basic_memory(project["id"], "read", identifier=identifier)


def test_basic_memory_forces_private_local_configuration(app, project, memory_command):
    api = configure(
        app,
        "basic-memory",
        {
            "command": ["bm"],
            "allow_write": True,
            "env": {
                "BASIC_MEMORY_CONFIG_DIR": "/foreign",
                "BASIC_MEMORY_FORCE_LOCAL": "false",
                "BASIC_MEMORY_WRITE_NOTE_OVERWRITE_DEFAULT": "true",
            },
        },
    )
    api.basic_memory(project["id"], "write", identifier="note.md", content="Owned note")
    env = memory_command[0][1]["env"]
    assert env["BASIC_MEMORY_FORCE_LOCAL"] == "true"
    assert env["BASIC_MEMORY_WRITE_NOTE_OVERWRITE_DEFAULT"] == "false"
    assert env["BASIC_MEMORY_CONFIG_DIR"].startswith(str(app.home))


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"error": "conflict"},
        {"checksum": "short"},
        {"file_path": "different.md", "checksum": "f" * 64},
    ],
)
def test_basic_memory_malformed_or_conflicting_results_are_errors(
    app, project, payload, monkeypatch
):
    api = configure(app, "basic-memory", {"command": ["bm"]})
    note = app.home / "knowledge" / project["id"] / "note.md"
    note.parent.mkdir(parents=True)
    note.write_text("A scoped note")
    monkeypatch.setattr(
        "assistant_workbench.integrations.run_process",
        lambda *a, **kw: command_result(payload),
    )
    with pytest.raises(WorkbenchError):
        api.basic_memory(project["id"], "read", identifier="note.md")


@pytest.mark.parametrize(
    "mode,command", [("exact", "search"), ("semantic", "vsearch"), ("hybrid", "query")]
)
def test_qmd_distinguishes_lexical_vector_and_hybrid_contracts(
    app, project, mode, command, monkeypatch
):
    calls = []

    def invoke(argv, **kw):
        calls.append(argv)
        return command_result(
            [
                {
                    "file": "qmd://project/refunds.md",
                    "score": 0.7,
                    "snippet": "Refunds excluded",
                }
            ]
        )

    monkeypatch.setattr("assistant_workbench.integrations.run_process", invoke)
    api = configure(
        app,
        "qmd",
        {
            "command": ["qmd"],
            "collections": {project["id"]: "project"},
            "allow_model_inference": True,
        },
    )
    result = api.qmd(project["id"], "refunds", mode=mode, limit=3)
    assert calls[0][1] == command and calls[0][-4:] == ["project", "-n", "3", "--json"]
    assert result["results"][0]["review_required"]


def test_qmd_model_modes_need_explicit_activation_and_unique_scope(
    app, project, monkeypatch
):
    monkeypatch.setattr(
        "assistant_workbench.integrations.run_process",
        lambda *a, **kw: pytest.fail("Unconfigured inference started"),
    )
    api = configure(
        app, "qmd", {"command": ["qmd"], "collections": {project["id"]: "project"}}
    )
    with pytest.raises(WorkbenchError, match="allow_model_inference"):
        api.qmd(project["id"], "question", mode="semantic")
    other = app.store.add_project("Other")
    api = configure(
        app,
        "qmd",
        {
            "command": ["qmd"],
            "collections": {project["id"]: "project", other["id"]: "PROJECT"},
        },
    )
    with pytest.raises(WorkbenchError, match="distinct"):
        api.qmd(project["id"], "question")


@pytest.mark.parametrize(
    "payload",
    [
        {"error": "index missing"},
        ["invalid"],
        [{"file": "qmd://other/private.md"}],
        [{"file": "qmd://project/%2e%2e/private.md"}],
        [{"file": "/private.md"}],
    ],
)
def test_qmd_malformed_and_cross_project_results_fail_closed(
    app, project, payload, monkeypatch
):
    monkeypatch.setattr(
        "assistant_workbench.integrations.run_process",
        lambda *a, **kw: command_result(payload),
    )
    api = configure(
        app, "qmd", {"command": ["qmd"], "collections": {project["id"]: "project"}}
    )
    with pytest.raises(WorkbenchError):
        api.qmd(project["id"], "question")


@pytest.fixture
def lcm_contract():
    state = {"calls": [], "override": None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["calls"].append((self.path, body))
            result = {
                "/ingest": {"ingested": 1, "totalTokens": 20},
                "/search": {
                    "episodic": [
                        {
                            "messageId": 1,
                            "snippet": "Scoped fact",
                            "project": {"id": digest(body["cwd"]), "cwd": body["cwd"]},
                        },
                        {
                            "messageId": 2,
                            "snippet": "OTHER-SECRET",
                            "project": {"id": "other", "cwd": "/other"},
                        },
                    ],
                    "promoted": [],
                },
                "/expand": {
                    "expansions": [],
                    "citedIds": [],
                    "totalTokens": 0,
                    "truncated": False,
                },
                "/compact": {
                    "summary": "Summarizer disabled",
                    "providerId": "disabled",
                    "replayOutcome": "disabled",
                },
            }[self.path]
            raw = json.dumps(
                state["override"] if state["override"] is not None else result
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
    )
    thread.start()
    state["url"] = f"http://127.0.0.1:{server.server_port}"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def lcm_api(app, project, server, **extra):
    return configure(
        app,
        "lcm",
        {
            "url": server["url"],
            "auth": "local",
            "allow_write": True,
            "summary_provider": "disabled",
            "projects": {project["id"]: {"cwd": project["repository"]}},
            **extra,
        },
    )


def test_lcm_capture_archives_original_search_filters_scope_and_summary_reports_disabled(
    app, project, lcm_contract
):
    api = lcm_api(app, project, lcm_contract)
    result = api.history(
        project["id"],
        "capture",
        client="codex",
        session_id="one",
        messages=[{"role": "user", "content": "Refunds excluded", "tokenCount": 5}],
    )
    assert result["status"] == "captured"
    assert (
        app.dispatch(
            "context.source",
            {"project": project["id"], "source_id": result["source_id"]},
        )["hash"]
        == result["original_sha256"]
    )
    result = api.history(project["id"], "search", query="refunds")
    assert (
        len(result["episodic"]) == 1
        and result["omitted"] == 1
        and "OTHER-SECRET" not in json.dumps(result)
    )
    assert (
        api.history(project["id"], "summarize", client="codex", session_id="one")[
            "status"
        ]
        == "not_generated"
    )
    assert api.history(project["id"], "expand", nodeId="sum_a", depth=2)[
        "review_required"
    ]
    assert lcm_contract["calls"][-1][1]["projectId"] == digest(
        str(Path(project["repository"]).resolve())
    )


@pytest.mark.parametrize(
    "action,args",
    [
        ("expand", {"nodeId": "sum_a", "cwd": "/foreign"}),
        ("expand", {"nodeId": "sum_a", "projectId": "foreign"}),
        (
            "capture",
            {
                "session_id": "s",
                "client": "codex",
                "messages": [
                    {"role": "invented", "content": "drop me", "tokenCount": 1}
                ],
            },
        ),
        ("capture", {"session_id": "s", "messages": []}),
        ("summarize", {"session_id": "../s", "client": "codex"}),
    ],
)
def test_lcm_rejects_scope_overrides_and_silently_dropped_messages(
    app, project, lcm_contract, action, args
):
    with pytest.raises(WorkbenchError):
        lcm_api(app, project, lcm_contract).history(project["id"], action, **args)
    assert not lcm_contract["calls"]


@pytest.mark.parametrize(
    "payload",
    [
        {"error": "failure"},
        {"errors": ["index failed"], "episodic": []},
        {"incomplete": True},
        {"episodic": "bad"},
    ],
)
def test_lcm_success_http_status_cannot_hide_operation_failure(
    app, project, lcm_contract, payload
):
    lcm_contract["override"] = payload
    with pytest.raises(WorkbenchError):
        lcm_api(app, project, lcm_contract).history(
            project["id"], "search", query="query"
        )


def test_lcm_refuses_missing_shared_or_overlapping_scope(app, project, lcm_contract):
    with pytest.raises(WorkbenchError, match="binding"):
        lcm_api(app, project, lcm_contract, projects={}).history(
            project["id"], "search", query="query"
        )
    other = app.store.add_project("Other", project["repository"])
    mappings = {p["id"]: {"cwd": p["repository"]} for p in [project, other]}
    with pytest.raises(WorkbenchError, match="disjoint"):
        lcm_api(app, project, lcm_contract, projects=mappings).history(
            project["id"], "search", query="query"
        )


def test_lcm_summary_contract_marks_derived_output_and_rejects_provider_drift(
    app, project, lcm_contract
):
    """Synthetic response contract only; does not assert a real model invocation."""
    api = lcm_api(
        app,
        project,
        lcm_contract,
        auth="subscription",
        summary_provider="codex-process",
    )
    lcm_contract["override"] = {
        "summary": "Compaction receipt",
        "latestSummaryContent": "Refund exclusions remain in effect.",
        "latestSummaryId": "sum_one",
        "providerId": "codex-process",
        "replayOutcome": "compacted",
        "llmUsage": {"calls": 1},
    }
    result = api.history(project["id"], "summarize", session_id="one", client="codex")
    assert (
        result["status"] == "needs_review"
        and result["derived"]
        and result["review_required"]
    )
    assert result["latestSummaryId"] == "sum_one" and result["model_call_reported"]
    lcm_contract["override"]["providerId"] = "anthropic"
    with pytest.raises(WorkbenchError, match="different summary provider"):
        api.history(project["id"], "summarize", session_id="one", client="codex")


def test_lcm_summary_provider_needs_matching_auth_profile(app, project, lcm_contract):
    api = lcm_api(
        app, project, lcm_contract, auth="local", summary_provider="codex-process"
    )
    with pytest.raises(WorkbenchError, match="subscription profile"):
        api.history(project["id"], "summarize", session_id="one", client="codex")
    assert not lcm_contract["calls"]


def test_basic_memory_read_cannot_resolve_another_project_identifier(
    app, project, monkeypatch
):
    other = app.store.add_project("Other memory")
    secret = app.home / "knowledge" / other["id"] / "secret.md"
    secret.parent.mkdir(parents=True)
    secret.write_text("Other project")
    api = configure(app, "basic-memory", {"command": ["bm"]})
    monkeypatch.setattr(
        "assistant_workbench.integrations.run_process",
        lambda *a, **kw: pytest.fail("A foreign identifier reached Basic Memory"),
    )
    with pytest.raises(WorkbenchError, match="not a local file"):
        api.basic_memory(project["id"], "read", identifier=other["id"] + "/secret.md")


def installed_path(variable):
    value = os.environ.get(variable)
    if not value or not Path(value).exists():
        pytest.skip("Set " + variable + " for the real pinned-package probe")
    return Path(value).resolve()


def test_real_basic_memory_concurrent_revision_guards_and_project_isolation(
    app, project
):
    binary = installed_path("AWB_TEST_BASIC_MEMORY")
    api = configure(
        app,
        "basic-memory",
        {
            "command": [str(binary)],
            "auth": "local",
            "allow_write": True,
            "env": {
                "BASIC_MEMORY_ENV": "test",
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
            },
        },
    )
    other = app.store.add_project("Other memory")
    api.basic_memory(project["id"], "register")
    api.basic_memory(other["id"], "register")
    first = api.basic_memory(
        project["id"],
        "write",
        identifier="decision.md",
        content="# Decision\n\nORCHARD-MEMORY refunds excluded.",
    )
    checksum = first["data"]["checksum"]

    def change(text):
        try:
            api.basic_memory(
                project["id"],
                "write",
                identifier="decision.md",
                content=text,
                checksum=checksum,
            )
            return "written"
        except WorkbenchError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(
            executor.map(
                change,
                [
                    "# Decision\n\nRefund exclusions persist. Tax recorded.",
                    "# Decision\n\nRefund exclusions persist. Shipping recorded.",
                ],
            )
        ) == ["conflict", "written"]
    assert (
        "Refund exclusions persist"
        in api.basic_memory(project["id"], "read", identifier="decision.md")["data"][
            "content"
        ]
    )
    assert (
        api.basic_memory(other["id"], "search", query="ORCHARD-MEMORY")["data"][
            "results"
        ]
        == []
    )


@pytest.fixture
def real_lcm(tmp_path, monkeypatch):
    package = installed_path("AWB_TEST_LCM_PACKAGE")
    home = tmp_path / "lcm-state"
    ready = tmp_path / "ready.json"
    script = tmp_path / "start.mjs"
    script.write_text(
        "import {writeFileSync} from 'node:fs';\n"
        + f"const {{createDaemon}}=await import({json.dumps((package/'dist/src/daemon/server.js').as_uri())});\n"
        + f"const {{loadDaemonConfig}}=await import({json.dumps((package/'dist/src/daemon/config.js').as_uri())});\n"
        + f"const {{createLcmPaths}}=await import({json.dumps((package/'dist/src/lcm-paths.js').as_uri())});\n"
        + f"const {{ensureAuthToken}}=await import({json.dumps((package/'dist/src/daemon/auth.js').as_uri())});\n"
        + f"const paths=createLcmPaths({json.dumps(str(home))});ensureAuthToken(paths.tokenPath);\n"
        + f"const config=loadDaemonConfig({json.dumps(str(home))},{{daemon:{{port:0}},llm:{{provider:'disabled'}}}});\n"
        + "const daemon=await createDaemon(config,{paths,tokenPath:paths.tokenPath});\n"
        + f"writeFileSync({json.dumps(str(ready))},JSON.stringify({{port:daemon.address().port,tokenPath:paths.tokenPath}}));\n"
        + "process.on('SIGTERM',async()=>{await daemon.stop();process.exit(0)});\n"
    )
    with (tmp_path / "daemon.log").open("w") as log:
        process = subprocess.Popen(
            ["node", str(script)],
            env={**os.environ, "LCM_HOME": str(home)},
            stdout=log,
            stderr=log,
        )
        try:
            for _ in range(100):
                if ready.exists() or process.poll() is not None:
                    break
                time.sleep(0.1)
            assert ready.exists(), (tmp_path / "daemon.log").read_text()[-2000:]
            state = json.loads(ready.read_text())
            monkeypatch.setenv(
                "AWB_TEST_LCM_TOKEN", Path(state["tokenPath"]).read_text().strip()
            )
            yield {
                "url": "http://127.0.0.1:" + str(state["port"]),
                "package": package,
                "home": home,
            }
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def test_real_lcm_capture_isolation_and_disabled_summary_are_observed(
    app, project, real_lcm, tmp_path
):
    other_repo = tmp_path / "other"
    other_repo.mkdir()
    other = app.store.add_project("Other LCM", str(other_repo))
    mappings = {p["id"]: {"cwd": p["repository"]} for p in [project, other]}
    api = lcm_api(
        app, project, real_lcm, token_env="AWB_TEST_LCM_TOKEN", projects=mappings
    )
    captured = api.history(
        project["id"],
        "capture",
        session_id="awb-real",
        client="codex",
        messages=[
            {
                "role": "user",
                "content": "ORCHARD-LCM refunds remain excluded.",
                "tokenCount": 10,
                "timestamp": "2026-11-08T00:00:00Z",
            }
        ],
    )
    assert captured["ingested"] == 1
    assert "ORCHARD-LCM" in json.dumps(
        api.history(project["id"], "search", query="ORCHARD-LCM")
    )
    assert api.history(other["id"], "search", query="ORCHARD-LCM")["episodic"] == []
    summary = api.history(
        project["id"], "summarize", session_id="awb-real", client="codex"
    )
    assert summary["status"] == "not_generated" and summary["providerId"] == "disabled"
    assert summary["model_call_reported"] is False


def test_real_lcm_connector_merges_private_project_hooks_with_checksum(
    app, project, real_lcm
):
    target = Path(project["repository"]) / ".codex/hooks.json"
    target.parent.mkdir()
    unrelated = {
        "hooks": {
            "Stop": [
                {"hooks": [{"type": "command", "command": "printf existing-hook"}]}
            ]
        }
    }
    target.write_text(json.dumps(unrelated))
    checksum = digest(target.read_bytes())
    api = lcm_api(
        app,
        project,
        real_lcm,
        command=["node", str(real_lcm["package"] / "dist/bin/lcm.js")],
        allow_hook_install=True,
        env={"LCM_HOME": str(real_lcm["home"])},
    )
    with pytest.raises(WorkbenchError, match="current SHA-256"):
        api.history(project["id"], "install_hooks", harness="codex")
    result = api.history(
        project["id"], "install_hooks", harness="codex", expected_sha256=checksum
    )
    assert result["installed"] and result["activation"] == "unknown"
    assert "existing-hook" in target.read_text() and "codex-hook" in target.read_text()
    repeated = api.history(
        project["id"],
        "install_hooks",
        harness="codex",
        expected_sha256=result["sha256"],
    )
    assert repeated["sha256"] == result["sha256"]


def test_real_qmd_exact_and_vector_query_use_bound_collection(app, project, tmp_path):
    binary = installed_path("AWB_TEST_QMD_BINARY")
    model = installed_path("AWB_TEST_QMD_EMBED_MODEL")
    expansion = installed_path("AWB_TEST_QMD_GENERATE_MODEL")
    docs = tmp_path / "documents"
    docs.mkdir()
    (docs / "refunds.md").write_text(
        "# Refund policy\nCancelled purchases are reimbursed within five business days."
    )
    (docs / "deploy.md").write_text(
        "# Deployment\nRelease builds reach production after unit tests pass."
    )
    env = {
        "QMD_CONFIG_DIR": str(tmp_path / "qmd-config"),
        "INDEX_PATH": str(tmp_path / "qmd.sqlite"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "QMD_FORCE_CPU": "1",
        "QMD_EMBED_PARALLELISM": "1",
        "QMD_EMBED_MODEL": str(model),
        "QMD_GENERATE_MODEL": str(expansion),
    }
    api = configure(
        app,
        "qmd",
        {
            "command": ["node", str(binary)],
            "auth": "local",
            "collections": {project["id"]: "project"},
            "allow_model_inference": True,
            "env": env,
        },
    )
    _, item = api.command("qmd")
    run_process(
        ["node", str(binary), "collection", "add", str(docs), "--name", "project"],
        env=api._env(item),
    )
    run_process(["node", str(binary), "embed"], env=api._env(item), timeout=180)
    assert api.qmd(project["id"], "reimbursed")["results"][0]["file"].endswith(
        "refunds.md"
    )
    result = api.qmd(project["id"], "How do I get my money back?", mode="semantic")
    assert result["retrieval"] == "vector" and result["results"][0]["file"].endswith(
        "refunds.md"
    )

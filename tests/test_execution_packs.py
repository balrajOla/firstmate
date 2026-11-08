"""Real local processes and files; no LLM or browser/device certification."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import time
import zipfile

import pytest

from assistant_workbench.context import Context
from assistant_workbench.execution_packs import ExecutionPacks
from assistant_workbench.util import WorkbenchError


def workspace(app, tmp_path, files):
    repo = tmp_path / "source"
    repo.mkdir()
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    project = app.store.add_project("Execution project", str(repo))
    packs = ExecutionPacks(app.store)
    created = packs.create_workspace(project["id"])
    return packs, created, Path(created["path"]), project, repo


def test_python_real_pass_fail_and_source_isolation(app, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-secret-must-not-reach-test")
    packs, ws, path, _, source = workspace(
        app,
        tmp_path,
        {
            "test_sample.py": "import os\ndef test_truth():\n    assert 'OPENAI_API_KEY' not in os.environ\n    assert 2 + 2 == 4\n"
        },
    )
    plan = packs.plan(ws["id"])
    result = packs.run(plan["id"], plan["sha256"])
    assert result["status"] == "passed" and result["tests_observed"] == 1
    assert "fixture-secret" not in json.dumps(result)
    assert result["semantic_acceptance"] == "requires independent review"
    (path / "test_sample.py").write_text("def test_truth():\n    assert 2 + 2 == 5\n")
    with pytest.raises(WorkbenchError, match="changed after inspection"):
        packs.run(plan["id"], plan["sha256"])
    replacement = packs.plan(ws["id"])
    failed = packs.run(replacement["id"], replacement["sha256"])
    assert failed["status"] == "failed" and failed["exit_code"] != 0
    assert "== 4" in (source / "test_sample.py").read_text()


def test_empty_and_zero_test_suites_do_not_pass(app, tmp_path):
    packs, ws, path, _, _ = workspace(
        app, tmp_path, {"app.py": "print('application')\n"}
    )
    blocked = packs.plan(ws["id"])
    assert packs.run(blocked["id"], blocked["sha256"])["status"] == "blocked"
    (path / "test_empty.py").write_text("VALUE = 1\n")
    plan = packs.plan(ws["id"])
    result = packs.run(plan["id"], plan["sha256"])
    assert result["status"] == "failed" and result["exit_code"] == 5


@pytest.mark.skipif(
    not shutil.which("node"), reason="A real Node executable is required"
)
def test_node_builtin_real_pass_and_seeded_failure(app, tmp_path):
    packs, ws, path, _, _ = workspace(
        app,
        tmp_path,
        {
            "unit.test.cjs": "const test = require('node:test'); const assert = require('node:assert/strict'); test('sum', () => assert.equal(2 + 2, 4));\n"
        },
    )
    plan = packs.plan(ws["id"])
    result = packs.run(plan["id"], plan["sha256"])
    assert result["status"] == "passed" and result["tests_observed"] == 1
    (path / "unit.test.cjs").write_text(
        "const test = require('node:test'); test('fail', () => { throw new Error('seeded failure'); });\n"
    )
    plan = packs.plan(ws["id"])
    assert packs.run(plan["id"], plan["sha256"])["status"] == "failed"


@pytest.mark.skipif(not shutil.which("npm"), reason="A real npm executable is required")
def test_package_script_needs_review_and_zero_exit_is_not_tests(app, tmp_path):
    packs, ws, _, _, _ = workspace(
        app,
        tmp_path,
        {
            "package.json": json.dumps(
                {"scripts": {"test": "node -e \"console.log('No tests exist')\""}}
            )
        },
    )
    blocked = packs.plan(ws["id"])
    assert blocked["status"] == "blocked" and "Review" in blocked["reason"]
    plan = packs.plan(ws["id"], allow_package_scripts=True)
    result = packs.run(plan["id"], plan["sha256"])
    assert result["exit_code"] == 0 and result["status"] == "not_run"


@pytest.mark.skipif(not shutil.which("npm"), reason="A real npm executable is required")
@pytest.mark.parametrize("message", ["Tests: 42", "# tests 42"])
def test_arbitrary_scripts_cannot_qualify_by_printing_test_totals(
    app, tmp_path, message
):
    packs, ws, _, _, _ = workspace(
        app,
        tmp_path,
        {
            "package.json": json.dumps({"scripts": {"test": "node print.cjs"}}),
            "print.cjs": "console.log(" + json.dumps(message) + ");\n",
            "app.test.cjs": "// A filename alone does not make an executed test.\n",
        },
    )
    plan = packs.plan(ws["id"], allow_package_scripts=True)
    result = packs.run(plan["id"], plan["sha256"])
    assert result["exit_code"] == 0 and result["status"] == "not_run"
    assert result["tests_observed"] is None


@pytest.mark.skipif(not shutil.which("npm"), reason="A real npm executable is required")
def test_declared_node_test_script_runs_actual_test(app, tmp_path):
    packs, ws, _, _, _ = workspace(
        app,
        tmp_path,
        {
            "package.json": json.dumps(
                {"scripts": {"test": "node --test --test-reporter=tap"}}
            ),
            "app.test.cjs": "require('node:test')('real test', () => require('node:assert/strict').equal(1 + 1, 2));\n",
        },
    )
    plan = packs.plan(ws["id"], allow_package_scripts=True)
    assert plan["framework"] == "node"
    result = packs.run(plan["id"], plan["sha256"])
    assert result["status"] == "passed" and result["tests_observed"] == 1


def test_workspace_rejects_source_symlink_and_excludes_secret_and_git_files(
    app, tmp_path
):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "file.py").write_text("VALUE = 1")
    (repo / ".env").write_text("TOKEN=private")
    (repo / ".git").write_text("gitdir: /outside/repository")
    (repo / "escape").symlink_to(tmp_path)
    project = app.store.add_project("Source", str(repo))
    packs = ExecutionPacks(app.store)
    with pytest.raises(WorkbenchError, match="symlink"):
        packs.create_workspace(project["id"])
    (repo / "escape").unlink()
    ws = packs.create_workspace(project["id"])
    assert not (Path(ws["path"]) / ".env").exists()
    assert not (Path(ws["path"]) / ".git").exists()


def test_registered_workspace_cannot_be_replaced_by_external_symlink(app, tmp_path):
    packs, ws, path, _, _ = workspace(app, tmp_path, {"app.py": "pass"})
    backup = path.with_name(path.name + "-old")
    path.rename(backup)
    path.symlink_to(backup, target_is_directory=True)
    with pytest.raises(WorkbenchError, match="workspace"):
        packs.inspect(ws["id"])


def test_real_process_wall_timeout_and_output_limit(app, tmp_path):
    packs, ws, path, _, _ = workspace(
        app, tmp_path, {"test_busy.py": "def test_busy():\n    while True: pass\n"}
    )
    plan = packs.plan(ws["id"])
    result = packs.run(plan["id"], plan["sha256"], timeout=0.5)
    assert result["status"] == "timed_out" and result["seconds"] < 3
    (path / "test_busy.py").write_text(
        "def test_output():\n    import os\n    for _ in range(100000): os.write(1, b'x' * 8192)\n"
    )
    # pytest captures test output by default; disable capture in the reviewed project config.
    (path / "pytest.ini").write_text("[pytest]\naddopts = -s\n")
    plan = packs.plan(ws["id"])
    result = packs.run(plan["id"], plan["sha256"], timeout=5, max_output_bytes=1024)
    assert result["status"] == "output_limit" and result["captured_bytes"] <= 1024


def test_running_process_can_be_cancelled_from_another_operator(app, tmp_path):
    packs, ws, _, _, _ = workspace(
        app,
        tmp_path,
        {"test_slow.py": "import time\ndef test_slow():\n    time.sleep(30)\n"},
    )
    plan = packs.plan(ws["id"])
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(packs.run, plan["id"], plan["sha256"], timeout=10)
        deadline = time.monotonic() + 3
        runs = []
        while not runs and time.monotonic() < deadline:
            runs = app.store.rows("SELECT * FROM execution_runs WHERE status='running'")
            time.sleep(0.01)
        assert runs, "The run must become observable before cancellation"
        assert packs.cancel(runs[0]["id"])["status"] == "stop_requested"
        result = future.result(timeout=3)
    assert result["status"] == "cancelled"
    assert packs.cancel(result["run_id"])["status"] == "cancelled"


def test_plan_hash_and_resource_parameters_are_enforced(app, tmp_path):
    packs, ws, _, _, _ = workspace(
        app, tmp_path, {"test_one.py": "def test_one(): assert True"}
    )
    plan = packs.plan(ws["id"])
    with pytest.raises(WorkbenchError, match="receipt"):
        packs.run(plan["id"], "invented")
    for settings in [
        {"timeout": 0},
        {"timeout": 601},
        {"max_output_bytes": -1},
        {"memory_mb": 1},
    ]:
        with pytest.raises(WorkbenchError, match="bound"):
            packs.run(plan["id"], plan["sha256"], **settings)


def test_python_virtual_memory_limit_is_observed(app, tmp_path):
    packs, ws, _, _, _ = workspace(
        app,
        tmp_path,
        {
            "test_memory.py": "def test_memory():\n    value = bytearray(512 * 1024 * 1024)\n    assert len(value) > 0\n"
        },
    )
    plan = packs.plan(ws["id"])
    result = packs.run(plan["id"], plan["sha256"], timeout=10, memory_mb=128)
    assert result["status"] == "failed" and result["exit_code"] != 0
    assert "MemoryError" in result["stdout"] + result["stderr"]


def test_run_progress_and_result_survive_new_application_instance(app, tmp_path):
    packs, ws, _, project, _ = workspace(
        app, tmp_path, {"test_one.py": "def test_one(): assert True\n"}
    )
    plan = packs.plan(ws["id"])
    result = packs.run(plan["id"], plan["sha256"])
    from assistant_workbench.app import Application

    reopened = ExecutionPacks(Application(app.home).store)
    assert reopened.list_workspaces(project["id"])[0]["id"] == ws["id"]
    assert reopened.list_runs(ws["id"])[0]["status"] == "passed"
    assert reopened.get_run(result["run_id"])["result"]["tests_observed"] == 1


def test_conflicting_package_locks_and_missing_typescript_script_are_blocked(
    app, tmp_path
):
    packs, ws, path, _, _ = workspace(
        app,
        tmp_path,
        {
            "package.json": json.dumps({"scripts": {"test": "node --test"}}),
            "package-lock.json": "{}",
            "pnpm-lock.yaml": "lockfileVersion: '9.0'",
        },
    )
    assert packs.plan(ws["id"], allow_package_scripts=True)["status"] == "blocked"
    (path / "package.json").write_text("{}")
    (path / "tsconfig.json").write_text("{}")
    assert (
        packs.plan(ws["id"], language="typescript", goal="typecheck")["status"]
        == "blocked"
    )


@pytest.mark.skipif(
    not shutil.which("tsc"), reason="An installed TypeScript compiler is required"
)
def test_typescript_real_typecheck_and_seeded_type_error(app, tmp_path):
    packs, ws, path, _, _ = workspace(
        app,
        tmp_path,
        {
            "package.json": json.dumps({"scripts": {"typecheck": "tsc --noEmit"}}),
            "tsconfig.json": json.dumps(
                {
                    "compilerOptions": {"strict": True, "noEmit": True},
                    "files": ["app.ts"],
                }
            ),
            "app.ts": "const value: number = 42;\n",
        },
    )
    plan = packs.plan(
        ws["id"], language="typescript", goal="typecheck", allow_package_scripts=True
    )
    good = packs.run(plan["id"], plan["sha256"])
    assert good["status"] == "passed", good
    (path / "app.ts").write_text('const value: number = "seeded type error";\n')
    plan = packs.plan(
        ws["id"], language="typescript", goal="typecheck", allow_package_scripts=True
    )
    bad = packs.run(plan["id"], plan["sha256"])
    assert bad["status"] == "failed" and "TS2322" in bad["stdout"]


def test_rust_discovery_requires_runtime_and_reviewed_lock(app, tmp_path, monkeypatch):
    packs, ws, _, _, _ = workspace(
        app,
        tmp_path,
        {
            "Cargo.toml": '[package]\nname = "fixture"\nversion = "0.1.0"\n',
            "src/main.rs": 'fn main() { println!("hello"); }',
        },
    )
    original = shutil.which
    monkeypatch.setattr(
        shutil, "which", lambda name: None if name == "cargo" else original(name)
    )
    plan = packs.plan(ws["id"])
    assert plan["language"] == "rust" and plan["status"] == "blocked"
    assert "Cargo is not installed" in plan["reason"]


@pytest.mark.skipif(
    not shutil.which("cargo"), reason="An installed Rust toolchain is required"
)
def test_rust_real_test_and_seeded_assertion_failure(app, tmp_path):
    packs, ws, path, _, _ = workspace(
        app,
        tmp_path,
        {
            "Cargo.toml": '[package]\nname = "awb_fixture"\nversion = "0.1.0"\nedition = "2021"\n',
            "Cargo.lock": 'version = 3\n\n[[package]]\nname = "awb_fixture"\nversion = "0.1.0"\n',
            "src/lib.rs": "#[test]\nfn sum() { assert_eq!(2 + 2, 4); }\n",
        },
    )
    plan = packs.plan(ws["id"], language="rust")
    good = packs.run(plan["id"], plan["sha256"])
    assert good["status"] == "passed" and good["tests_observed"] == 1, good
    (path / "src/lib.rs").write_text("#[test]\nfn sum() { assert_eq!(2 + 2, 5); }\n")
    plan = packs.plan(ws["id"], language="rust")
    bad = packs.run(plan["id"], plan["sha256"])
    assert bad["status"] == "failed" and bad["exit_code"] != 0


def test_web_artifact_checks_follow_real_assets_and_detect_drift(app, tmp_path):
    packs, ws, path, _, _ = workspace(
        app,
        tmp_path,
        {
            "index.html": '<html><head><title>Fixture</title><link rel="stylesheet" href="site.css"></head><body><main>Hello</main></body></html>',
            "site.css": "body { color: navy; }",
        },
    )
    manifest = packs.artifacts(ws["id"], ["index.html"], kind="web")
    assert (
        manifest["mechanical_status"] == "passed"
        and manifest["status"] == "needs_review"
    )
    assert packs.verify_artifacts(manifest["id"])["integrity"] == "passed"
    (path / "site.css").write_text("body { display: none; }")
    with pytest.raises(WorkbenchError, match="Artifact changed"):
        packs.verify_artifacts(manifest["id"])
    (path / "site.css").unlink()
    assert (
        packs.artifacts(ws["id"], ["index.html"], kind="web")["mechanical_status"]
        == "failed"
    )


def test_artifact_paths_and_symlink_escape_are_rejected(app, tmp_path):
    packs, ws, path, _, _ = workspace(app, tmp_path, {"result.txt": "result"})
    outside = tmp_path / "outside.txt"
    outside.write_text("private")
    (path / "escape.txt").symlink_to(outside)
    for name in [str(outside), "../outside.txt", "escape.txt"]:
        with pytest.raises(WorkbenchError):
            packs.artifacts(ws["id"], [name])


def test_mobile_source_and_fake_archive_never_certify_a_working_app(app, tmp_path):
    packs, ws, path, _, _ = workspace(
        app,
        tmp_path,
        {
            "package.json": '{"dependencies":{"react-native":"0.0.0-fixture"}}',
            "app.apk": "not a mobile binary",
        },
    )
    source = packs.artifacts(
        ws["id"], ["package.json"], kind="mobile", platforms=["android", "ios"]
    )
    assert source["mechanical_status"] == "failed"
    fake = packs.artifacts(ws["id"], ["app.apk"], kind="mobile", platforms=["android"])
    assert fake["mechanical_status"] == "failed"
    with zipfile.ZipFile(path / "app.apk", "w") as output:
        output.writestr("AndroidManifest.xml", "synthetic structure fixture")
    shaped = packs.artifacts(
        ws["id"], ["app.apk"], kind="mobile", platforms=["android"]
    )
    assert shaped["mechanical_status"] == "incomplete"
    assert shaped["semantic_acceptance"] == "not granted"


def test_research_citations_require_real_scoped_sources_and_remain_reviewable(
    app, tmp_path
):
    packs, ws, path, project, _ = workspace(
        app, tmp_path, {"report.md": "# Result\nThe sample excludes refunds.\n"}
    )
    source_path = tmp_path / "evidence.txt"
    source_path.write_text("The sample excludes refunds.")
    context = Context(app.store)
    source = context.ingest(project["id"], str(source_path))
    no_claims = packs.artifacts(ws["id"], ["report.md"], kind="research")
    assert no_claims["mechanical_status"] == "failed"
    claims = [
        {
            "source_id": source["id"],
            "quote": "The sample excludes refunds.",
            "statement": "The sample excludes refunds.",
        }
    ]
    receipt = packs.artifacts(ws["id"], ["report.md"], kind="research", claims=claims)
    assert (
        receipt["mechanical_status"] == "passed"
        and receipt["semantic_acceptance"] == "not granted"
    )
    (path / "report.md").write_text("# Result\nThe sample includes refunds.\n")
    with pytest.raises(WorkbenchError, match="Artifact changed"):
        packs.verify_artifacts(receipt["id"])
    trap = packs.artifacts(ws["id"], ["report.md"], kind="research", claims=claims)
    assert trap["mechanical_status"] == "failed"


def test_research_manifest_rejects_cross_project_source(app, tmp_path):
    packs, ws, _, _, _ = workspace(app, tmp_path, {"report.md": "Confidential."})
    other = app.store.add_project("Other")
    source_path = tmp_path / "private.txt"
    source_path.write_text("Confidential.")
    source = Context(app.store).ingest(other["id"], str(source_path))
    with pytest.raises(WorkbenchError, match="scope"):
        packs.artifacts(
            ws["id"],
            ["report.md"],
            kind="research",
            claims=[
                {
                    "source_id": source["id"],
                    "quote": "Confidential.",
                    "statement": "Confidential.",
                }
            ],
        )

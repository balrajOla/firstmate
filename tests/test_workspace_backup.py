"""Portable owned artifacts, closed inventories and actual restore execution."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import time

import pytest

from assistant_workbench.app import Application
from assistant_workbench.execution_packs import ExecutionPacks
from assistant_workbench import operations
from assistant_workbench.util import WorkbenchError, canonical, digest


def prepared(app, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "test_app.py").write_text("def test_sum(): assert 2 + 2 == 4\n")
    project = app.store.add_project("Backup project", str(source))
    execution = ExecutionPacks(app.store)
    workspace = execution.create_workspace(project["id"])
    root = Path(workspace["path"])
    (root / "dist").mkdir()
    (root / "dist/index.html").write_text(
        "<html><title>Delivered</title><body>Saved artifact</body></html>"
    )
    (root / "empty").mkdir()
    manifest = execution.artifacts(workspace["id"], ["dist/index.html"], kind="web")
    return execution, workspace, root, manifest


def test_backup_restores_artifacts_and_rebinds_workspace_then_executes(app, tmp_path):
    execution, workspace, root, manifest = prepared(app, tmp_path)
    stored = app.store.one(
        "SELECT path FROM execution_workspaces WHERE id=?", (workspace["id"],)
    )
    assert stored["path"] == "workspaces/" + workspace["id"]
    snapshot = tmp_path / "backup"
    result = operations.backup_state(app.store, snapshot)
    assert result["workspace_count"] == 1
    restored_home = tmp_path / "restored"
    operations.restore_state(snapshot, restored_home)
    restored = ExecutionPacks(Application(restored_home).store)
    assert (
        restored.inspect(workspace["id"])["source_sha256"]
        == execution.inspect(workspace["id"])["source_sha256"]
    )
    relocated = restored_home / "workspaces" / workspace["id"]
    assert (relocated / "dist/index.html").read_bytes() == (
        root / "dist/index.html"
    ).read_bytes()
    assert (relocated / "empty").is_dir()
    assert restored.verify_artifacts(manifest["id"])["integrity"] == "passed"
    plan = restored.plan(workspace["id"])
    assert restored.run(plan["id"], plan["sha256"])["status"] == "passed"


def test_legacy_absolute_workspace_path_is_normalized_in_snapshot(app, tmp_path):
    _, workspace, root, _ = prepared(app, tmp_path)
    with app.store.transaction() as db:
        db.execute(
            "UPDATE execution_workspaces SET path=? WHERE id=?",
            (str(root), workspace["id"]),
        )
    snapshot = tmp_path / "backup"
    operations.backup_state(app.store, snapshot)
    restored_home = tmp_path / "restored"
    operations.restore_state(snapshot, restored_home)
    restored = ExecutionPacks(Application(restored_home).store)
    assert restored.inspect(workspace["id"])["workspace_id"] == workspace["id"]
    assert app.store.one(
        "SELECT path FROM execution_workspaces WHERE id=?", (workspace["id"],)
    )["path"] == str(root)


@pytest.mark.parametrize("directory", [False, True])
def test_backup_rejects_symlink_in_owned_workspace(app, tmp_path, directory):
    _, _, root, _ = prepared(app, tmp_path)
    outside = tmp_path / "outside"
    if directory:
        outside.mkdir()
        (outside / "secret.txt").write_text("outside")
    else:
        outside.write_text("outside")
    (root / "link").symlink_to(outside, target_is_directory=directory)
    with pytest.raises(WorkbenchError, match="symlink"):
        operations.backup_state(app.store, tmp_path / "backup")
    assert not (tmp_path / "backup").exists()


@pytest.mark.parametrize("entry", ["extra.txt", "extra-directory"])
def test_restore_rejects_entries_absent_from_inventory(app, tmp_path, entry):
    snapshot = tmp_path / "backup"
    operations.backup_state(app.store, snapshot)
    if entry.endswith("txt"):
        (snapshot / entry).write_text("unlisted data must not be copied")
    else:
        (snapshot / entry).mkdir()
    with pytest.raises(WorkbenchError, match="unlisted"):
        operations.restore_state(snapshot, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


@pytest.mark.parametrize("target_inside", [False, True])
def test_restore_rejects_symlink_even_when_target_is_inside_snapshot(
    app, tmp_path, target_inside
):
    snapshot = tmp_path / "backup"
    operations.backup_state(app.store, snapshot)
    if target_inside:
        target = snapshot / "workbench.sqlite"
    else:
        target = tmp_path / "outside"
        target.write_text("outside")
    (snapshot / "extra-link").symlink_to(target)
    with pytest.raises(WorkbenchError, match="symlink"):
        operations.restore_state(snapshot, tmp_path / "restored")


def test_restore_rejects_symlink_destination_ancestor_and_nested_source(app, tmp_path):
    snapshot = tmp_path / "backup"
    operations.backup_state(app.store, snapshot)
    actual = tmp_path / "actual"
    actual.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    with pytest.raises(WorkbenchError, match="symlink"):
        operations.restore_state(snapshot, alias / "restored")
    with pytest.raises(WorkbenchError, match="separate"):
        operations.restore_state(snapshot, snapshot / "nested")
    assert not (actual / "restored").exists()


@pytest.mark.parametrize(
    "bad_path",
    [
        "../escaped.txt",
        "/absolute.txt",
        "archive/../escaped.txt",
        "archive//file",
        "archive\\file",
    ],
)
def test_restore_validates_manifest_paths_before_copy(app, tmp_path, bad_path):
    snapshot = tmp_path / "backup"
    operations.backup_state(app.store, snapshot)
    manifest = json.loads((snapshot / "backup.json").read_text())
    manifest["files"][bad_path] = "0" * 64
    (snapshot / "backup.json").write_text(canonical(manifest))
    with pytest.raises(WorkbenchError, match="relative path"):
        operations.restore_state(snapshot, tmp_path / "restored")


@pytest.mark.parametrize("change", ["changed", "missing"])
def test_restore_detects_artifact_content_drift(app, tmp_path, change):
    _, workspace, _, _ = prepared(app, tmp_path)
    snapshot = tmp_path / "backup"
    operations.backup_state(app.store, snapshot)
    saved = snapshot / "workspaces" / workspace["id"] / "dist/index.html"
    if change == "changed":
        saved.write_text("corrupt artifact")
    else:
        saved.unlink()
    with pytest.raises(WorkbenchError, match="integrity"):
        operations.restore_state(snapshot, tmp_path / "restored")


def test_backup_requires_registered_workspaces(app, tmp_path):
    prepared(app, tmp_path)
    (app.home / "workspaces/unregistered").mkdir()
    with pytest.raises(WorkbenchError, match="unregistered"):
        operations.backup_state(app.store, tmp_path / "backup")


def test_backup_rejects_workspace_path_outside_owned_state(app, tmp_path):
    _, workspace, _, _ = prepared(app, tmp_path)
    with app.store.transaction() as db:
        db.execute(
            "UPDATE execution_workspaces SET path=? WHERE id=?",
            (str(tmp_path / "outside"), workspace["id"]),
        )
    with pytest.raises(WorkbenchError, match="outside"):
        operations.backup_state(app.store, tmp_path / "backup")


@pytest.mark.parametrize("database_kind", ["corrupt", "wrong_schema"])
def test_restore_validates_database_and_removes_failed_restore(
    app, tmp_path, database_kind
):
    snapshot = tmp_path / "backup"
    operations.backup_state(app.store, snapshot)
    database = snapshot / "workbench.sqlite"
    database.unlink()
    if database_kind == "corrupt":
        database.write_bytes(b"not a SQLite file")
    else:
        connection = sqlite3.connect(database)
        connection.execute("CREATE TABLE unrelated(value TEXT)")
        connection.commit()
        connection.close()
    manifest = json.loads((snapshot / "backup.json").read_text())
    manifest["files"]["workbench.sqlite"] = digest(database.read_bytes())
    (snapshot / "backup.json").write_text(canonical(manifest))
    with pytest.raises(WorkbenchError, match="database"):
        operations.restore_state(snapshot, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_restore_rejects_registered_but_missing_workspace_even_with_rehashed_manifest(
    app, tmp_path
):
    _, workspace, _, _ = prepared(app, tmp_path)
    snapshot = tmp_path / "backup"
    operations.backup_state(app.store, snapshot)
    import shutil

    shutil.rmtree(snapshot / "workspaces" / workspace["id"])
    manifest = json.loads((snapshot / "backup.json").read_text())
    manifest["files"] = {
        name: value
        for name, value in manifest["files"].items()
        if not name.startswith("workspaces/")
    }
    manifest["directories"] = [
        name for name in manifest["directories"] if not name.startswith("workspaces/")
    ]
    (snapshot / "backup.json").write_text(canonical(manifest))
    with pytest.raises(WorkbenchError, match="missing or unregistered"):
        operations.restore_state(snapshot, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


@pytest.mark.parametrize("writer", ["filesystem", "database"])
def test_backup_detects_concurrent_writes_and_removes_partial_copy(
    app, tmp_path, monkeypatch, writer
):
    _, _, root, _ = prepared(app, tmp_path)
    original = operations._copy_snapshot_files

    def changed(source, destination, inventory):
        original(source, destination, inventory)
        if writer == "filesystem":
            (root / "dist/index.html").write_text("changed while copying")
        else:
            with app.store.transaction() as db:
                app.store.event(db, "fixture.concurrent_write", "fixture", {})

    monkeypatch.setattr(operations, "_copy_snapshot_files", changed)
    with pytest.raises(WorkbenchError, match="changed during backup"):
        operations.backup_state(app.store, tmp_path / "backup")
    assert not (tmp_path / "backup").exists()


def test_backup_rejects_actual_running_process_until_cancelled(app, tmp_path):
    execution, workspace, root, _ = prepared(app, tmp_path)
    (root / "test_app.py").write_text("import time\ndef test_slow(): time.sleep(30)\n")
    plan = execution.plan(workspace["id"])
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(execution.run, plan["id"], plan["sha256"], timeout=10)
        runs = []
        deadline = time.monotonic() + 3
        while not runs and time.monotonic() < deadline:
            runs = app.store.rows(
                "SELECT id FROM execution_runs WHERE status='running'"
            )
            time.sleep(0.01)
        assert runs
        try:
            with pytest.raises(WorkbenchError, match="active execution"):
                operations.backup_state(app.store, tmp_path / "backup")
        finally:
            execution.cancel(runs[0]["id"])
        assert future.result(timeout=3)["status"] == "cancelled"
    assert (
        operations.backup_state(app.store, tmp_path / "backup")["workspace_count"] == 1
    )

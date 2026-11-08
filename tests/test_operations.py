import json
import os
import platform
import sqlite3
import subprocess
import sys
from pathlib import Path
import pytest
from assistant_workbench import migration
from assistant_workbench.operations import (
    backup_state,
    restore_state,
    service_definition,
)
from assistant_workbench.qualification import qualification, record, GATES
from assistant_workbench.terminal import (
    install as terminal_install,
    uninstall as terminal_uninstall,
)
from assistant_workbench.util import WorkbenchError, canonical, digest, now


def test_snapshot_restore_and_tamper_detection(app, project, tmp_path):
    app.store.submit("Persistent prompt", "a", project["id"])
    backup = tmp_path / "backup"
    backup_state(app.store, backup)
    result = restore_state(backup, tmp_path / "restored")
    assert Path(result["path"], "workbench.sqlite").exists()
    (backup / "workbench.sqlite").write_bytes(b"corrupt")
    with pytest.raises(WorkbenchError):
        restore_state(backup, tmp_path / "other")


def test_snapshot_database_is_self_contained_without_wal_sidecars(
    app, project, tmp_path
):
    # The live store uses WAL. Some SQLite builds (Apple's system library)
    # keep -wal/-shm files after close, which restore rightly rejects as
    # unsupported entries. A snapshot must be one rollback-journal file.
    app.store.submit("Persistent prompt", "a", project["id"])
    backup = tmp_path / "backup"
    backup_state(app.store, backup)
    manifest = json.loads((backup / "backup.json").read_text())
    assert not [
        name for name in manifest["files"]
        if name.startswith("workbench.sqlite") and name != "workbench.sqlite"
    ]
    header = (backup / "workbench.sqlite").read_bytes()[:20]
    assert header[18:20] == b"\x01\x01"
    restored = restore_state(backup, tmp_path / "restored")
    with sqlite3.connect(Path(restored["path"], "workbench.sqlite")) as db:
        assert db.execute(
            "SELECT count(*) FROM prompts WHERE body=?", ("Persistent prompt",)
        ).fetchone()[0] == 1


def _root_owned_system_symlink():
    for name in ("/tmp", "/var", "/etc", "/bin", "/lib", "/sbin"):
        path = Path(name)
        if path.is_symlink() and path.lstat().st_uid == 0:
            return path
    pytest.skip("No root-owned top-level symlink on this platform")


def test_snapshot_paths_accept_os_symlinks_but_reject_user_symlinks(
    app, project, tmp_path
):
    # macOS /tmp and /var are root-owned links; refusing them made every
    # temporary-directory backup fail while adding no protection.
    from assistant_workbench.operations import _plain_path

    system = _root_owned_system_symlink()
    assert _plain_path(system / "awb-not-created") == system / "awb-not-created"
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    with pytest.raises(WorkbenchError, match="symlinks"):
        backup_state(app.store, link / "backup")
    with pytest.raises(WorkbenchError, match="symlinks"):
        _plain_path(link)


def test_migration_preserves_local_edits_ignored_files_and_git_history(tmp_path):
    account = tmp_path / "account"
    account.mkdir()
    legacy = account / "legacy"
    legacy.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(legacy), *args],
            capture_output=True,
            text=True,
            check=True,
        )

    git("init")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Fixture")
    (legacy / "app.py").write_text("original")
    (legacy / ".gitignore").write_text("private.env\n")
    git("add", ".")
    git("commit", "-m", "fixture")
    (legacy / "app.py").write_text("local modification")
    (legacy / "private.env").write_text("fixture-secret")
    (legacy / "untracked.txt").write_text("untracked")
    dest = tmp_path / "backup"
    migration.backup([str(legacy)], dest, account)
    saved = migration.verify(dest)
    assert saved["roots"][0]["git"]["head"] and (dest / "history-0.bundle").exists()
    assert (dest / "root-0/app.py").read_text() == "local modification"
    assert (dest / "root-0/private.env").read_text() == "fixture-secret"
    assert dest.stat().st_mode & 0o777 == 0o700


def test_migration_rejects_overlap_and_account_root(tmp_path):
    account = tmp_path / "account"
    account.mkdir()
    child = account / "legacy"
    child.mkdir()
    with pytest.raises(WorkbenchError):
        migration.backup([str(account)], tmp_path / "b", account)
    nested = child / "nested"
    nested.mkdir()
    with pytest.raises(WorkbenchError):
        migration.backup([str(child), str(nested)], tmp_path / "b", account)


def test_migration_never_retires_without_target_evidence(tmp_path):
    account = tmp_path / "account"
    account.mkdir()
    legacy = account / "old"
    legacy.mkdir()
    (legacy / "change").write_text("preserve")
    dest = tmp_path / "backup"
    migration.backup([legacy], dest, account)
    proof = tmp_path / "proof.json"
    proof.write_text("{}")
    with pytest.raises(WorkbenchError):
        migration.retire(dest, proof)
    assert (legacy / "change").read_text() == "preserve"


def test_verified_retire_is_recoverable_and_does_not_overwrite_new_changes(
    tmp_path, monkeypatch
):
    account = tmp_path / "account"
    account.mkdir()
    legacy = account / "old"
    legacy.mkdir()
    (legacy / "change").write_text("preserve")
    dest = tmp_path / "backup"
    migration.backup([legacy], dest, account)
    saved = migration.verify(dest)
    evidence = tmp_path / "fixture-evidence"
    evidence.write_text("SIMULATED test fixture, not real GUI or model evidence")
    artifact = {"path": str(evidence), "sha256": digest(evidence.read_bytes())}
    proof = {
        "created": now(),
        "host": platform.node(),
        "qualified_for_cutover": True,
        "migration_id": saved["id"],
        "legacy_processes_stopped": True,
        "custom_changes_reconciled": True,
        "gates": {g: "passed" for g in GATES},
        "evidence": {g: {"artifacts": [artifact]} for g in GATES},
        "custom_changes_report": artifact,
    }
    path = tmp_path / "proof.json"
    path.write_text(canonical(proof))
    monkeypatch.setattr(migration, "_legacy_services_stopped", lambda r: None)
    migration.retire(dest, path)
    assert not legacy.exists()
    assert (dest / "root-0/change").read_text() == "preserve"
    migration.restore(dest)
    assert (legacy / "change").read_text() == "preserve"
    (legacy / "change").write_text("new work")
    with pytest.raises(WorkbenchError):
        migration.restore(dest)


def test_backup_inventory_tampering_is_detected(tmp_path):
    account = tmp_path / "account"
    account.mkdir()
    legacy = account / "old"
    legacy.mkdir()
    dest = tmp_path / "backup"
    migration.backup([legacy], dest, account)
    p = dest / "migration.json"
    record_ = json.loads(p.read_text())
    record_["roots"][0]["copy"] = "../elsewhere"
    p.write_text(canonical(record_))
    with pytest.raises(WorkbenchError):
        migration.verify(dest)


def test_symlink_backups_do_not_follow_targets(tmp_path):
    account = tmp_path / "account"
    account.mkdir()
    outside = tmp_path / "unrelated"
    outside.write_text("user data")
    link = account / "legacy"
    link.symlink_to(outside)
    dest = tmp_path / "backup"
    migration.backup([link], dest, account)
    assert (dest / "root-0").is_symlink() and outside.read_text() == "user data"


def test_terminal_include_preserves_theme_and_local_edits(app, tmp_path):
    path = tmp_path / "wezterm.lua"
    path.write_text(
        "local config = {}\nconfig.color_scheme = 'My theme'\nreturn config\n"
    )
    receipt = terminal_install(app.home, path, sys.executable)
    assert "My theme" in path.read_text() and receipt["gui_qualification"] == "not_run"
    assert terminal_install(app.home, path, sys.executable) == receipt
    path.write_text(path.read_text().replace("My theme", "New theme"))
    terminal_uninstall(app.home)
    assert "New theme" in path.read_text() and "WORKBENCH" not in path.read_text()


def test_complex_lua_config_is_not_guessed(app, tmp_path):
    path = tmp_path / "wezterm.lua"
    text = "return { color_scheme = 'Custom' }\n"
    path.write_text(text)
    with pytest.raises(WorkbenchError):
        terminal_install(app.home, path, sys.executable)
    assert path.read_text() == text


def test_service_paths_are_argument_safe(app, tmp_path):
    import plistlib

    value = plistlib.loads(service_definition(app.home, sys.executable).encode())
    assert value["ProgramArguments"][0] == str(Path(sys.executable).resolve())
    assert value["ProgramArguments"][-1] == "serve"
    assert "ExecStart=" in service_definition(app.home, sys.executable, "systemd")


def test_missing_or_expired_qualification_is_not_a_pass(app, tmp_path):
    result = qualification(app, "subscription")
    assert not result["qualified"] and all(
        v == "not_run" for v in result["gates"].values()
    )
    evidence = tmp_path / "observed.txt"
    evidence.write_text("Fixture evidence")
    record(
        app.store,
        "installed_cli",
        "subscription",
        "passed",
        [str(evidence)],
        "Fixture test",
    )
    assert qualification(app, "subscription")["gates"]["installed_cli"] == "passed"
    evidence.write_text("changed")
    assert qualification(app, "subscription")["gates"]["installed_cli"] == "blocked"


def test_pass_requires_evidence(app):
    with pytest.raises(WorkbenchError):
        record(app.store, "terminal_gui", "subscription", "passed", [], "Assumed okay")

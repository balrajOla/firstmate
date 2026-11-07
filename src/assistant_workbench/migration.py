"""Data-preserving replacement of an identified legacy installation.

No process-name killing, recursive deletion, credential removal, or reset of a
user repository. Retirement moves inventoried software into a recovery archive.
"""

from __future__ import annotations

import json
import os
import plistlib
import shutil
import stat
import subprocess
from pathlib import Path
from .util import WorkbenchError, atomic, canonical, digest, now, read_json, uid


def file_manifest(root: Path):
    result = {}

    def visit(path, relative):
        st = path.lstat()
        mode = stat.S_IMODE(st.st_mode)
        if path.is_symlink():
            result[relative] = {
                "type": "symlink",
                "target": os.readlink(path),
                "mode": mode,
            }
            return
        if path.is_dir():
            result[relative] = {"type": "directory", "mode": mode}
            for child in sorted(path.iterdir(), key=lambda p: p.name):
                visit(
                    child,
                    str(Path(relative) / child.name) if relative != "." else child.name,
                )
        elif path.is_file():
            before = (st.st_size, st.st_mtime_ns)
            sha = digest(path.read_bytes())
            after = path.stat()
            if before != (after.st_size, after.st_mtime_ns):
                raise WorkbenchError("File changed during backup: " + str(path))
            result[relative] = {
                "type": "file",
                "sha256": sha,
                "size": st.st_size,
                "mode": mode,
            }
        else:
            raise WorkbenchError(
                "Stop legacy services and remove ephemeral sockets before backup: "
                + str(path)
            )

    visit(root, ".")
    return result


def _safe_root(path, account_home):
    path = Path(os.path.abspath(Path(path).expanduser()))
    if path in {Path("/"), Path(account_home).resolve()} or len(path.parts) < 3:
        raise WorkbenchError("Refusing a system or account root")
    if not path.exists() and not path.is_symlink():
        raise WorkbenchError("Legacy path does not exist: " + str(path))
    return path


def discover(account_home=None, prefix=None):
    home = Path(account_home or Path.home()).resolve()
    prefix = Path(prefix or home / ".local")
    candidates = [
        prefix / "bin/olympus",
        prefix / "bin/olympusd",
        prefix / "lib/olympus",
        home / ".olympus",
        home / ".local/share/olympus",
        home / ".config/olympus",
        home / "Library/LaunchAgents/com.olympus.coordinator.plist",
        home / ".config/systemd/user/olympus.service",
    ]
    paths = [str(p) for p in candidates if p.exists() or p.is_symlink()]
    config_files = [
        home / ".wezterm.lua",
        home / ".config/wezterm/wezterm.lua",
        home / ".zshrc",
        home / ".bashrc",
    ]
    mentions = []
    for file in config_files:
        if file.is_file() and file.stat().st_size < 2 * 1024 * 1024:
            lines = [
                i + 1
                for i, line in enumerate(file.read_text(errors="replace").splitlines())
                if "olympus" in line.lower()
            ]
            if lines:
                mentions.append(
                    {
                        "path": str(file),
                        "lines": lines,
                        "action": "Review exact legacy include/launcher; preserve unrelated settings",
                    }
                )
    return {
        "schema": 1,
        "account_home": str(home),
        "legacy_roots": paths,
        "shared_config_mentions": mentions,
        "notice": "Inventory is bounded to known paths. Explicitly add any custom installation roots. No files or processes changed.",
    }


def backup(roots, destination, account_home=None):
    account = Path(account_home or Path.home())
    dest = Path(destination).expanduser().resolve()
    if dest.exists():
        raise WorkbenchError("Backup destination must be new")
    paths = [_safe_root(p, account) for p in roots]
    if not paths:
        raise WorkbenchError("No legacy paths selected")
    for i, path in enumerate(paths):
        if dest == path or dest.is_relative_to(path):
            raise WorkbenchError("Backup cannot be inside a legacy root")
        if any(
            path == other or path.is_relative_to(other) or other.is_relative_to(path)
            for other in paths[:i]
        ):
            raise WorkbenchError("Legacy roots must be distinct and non-overlapping")
    dest.mkdir(parents=True, mode=0o700)
    os.chmod(dest, 0o700)
    record = {
        "schema": 1,
        "id": uid("migration_"),
        "created": now(),
        "phase": "copying",
        "roots": [],
        "account_home": str(account.resolve()),
    }
    atomic(dest / "migration.json", canonical(record))
    try:
        for i, path in enumerate(paths):
            manifest = file_manifest(path)
            copy = dest / ("root-" + str(i))
            if path.is_symlink():
                os.symlink(os.readlink(path), copy)
            elif path.is_dir():
                shutil.copytree(path, copy, symlinks=True)
            else:
                shutil.copy2(path, copy)
            if file_manifest(copy) != manifest or file_manifest(path) != manifest:
                raise WorkbenchError("Backup verification failed or source changed")
            entry = {
                "original": str(path),
                "copy": copy.name,
                "manifest": manifest,
                "state": "backed_up",
                "git": None,
            }
            if path.is_dir() and (path / ".git").exists():

                def git(*args):
                    result = subprocess.run(
                        ["git", "-C", str(path), *args],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    return result.stdout if result.returncode == 0 else None

                entry["git"] = {
                    "head": git("rev-parse", "HEAD"),
                    "status": git("--no-optional-locks", "status", "--porcelain=v1"),
                    "branch": git("branch", "--show-current"),
                }
                if entry["git"]["head"]:
                    bundle = dest / ("history-" + str(i) + ".bundle")
                    result = subprocess.run(
                        [
                            "git",
                            "-C",
                            str(path),
                            "bundle",
                            "create",
                            str(bundle),
                            "--all",
                            "HEAD",
                        ],
                        capture_output=True,
                        timeout=120,
                    )
                    if result.returncode:
                        raise WorkbenchError(
                            "Could not preserve Git history; legacy installation remains untouched"
                        )
                    entry["bundle"] = {
                        "path": bundle.name,
                        "sha256": digest(bundle.read_bytes()),
                    }
            record["roots"].append(entry)
            atomic(dest / "migration.json", canonical(record))
        record["phase"] = "verified"
        record["manifest_hash"] = snapshot_digest(record)
        atomic(dest / "migration.json", canonical(record))
    except Exception as exc:
        record["phase"] = "incomplete"
        record["error"] = str(exc)
        atomic(dest / "migration.json", canonical(record))
        raise
    return {
        "backup": str(dest),
        "phase": "verified",
        "roots": len(paths),
        "local_changes_preserved": True,
    }


def snapshot_digest(record):
    return digest(
        canonical(
            [
                {k: e.get(k) for k in ("original", "copy", "manifest", "git", "bundle")}
                for e in record["roots"]
            ]
        )
    )


def verify(destination):
    dest = Path(destination).resolve()
    record = read_json(dest / "migration.json")
    if not record or record.get("phase") not in {"verified", "retiring", "retired"}:
        raise WorkbenchError("Backup is incomplete")
    if record.get("manifest_hash") != snapshot_digest(record):
        raise WorkbenchError("Migration inventory changed or is incomplete")
    for index, entry in enumerate(record["roots"]):
        if entry["copy"] != "root-" + str(index):
            raise WorkbenchError("Invalid backup root identity")
        if entry.get("bundle"):
            bundle = entry["bundle"]
            if (
                bundle["path"] != "history-" + str(index) + ".bundle"
                or digest((dest / bundle["path"]).read_bytes()) != bundle["sha256"]
            ):
                raise WorkbenchError("Git history backup integrity failed")
        if file_manifest(dest / entry["copy"]) != entry["manifest"]:
            raise WorkbenchError(
                "Backup content or permissions changed: " + entry["copy"]
            )
    return record


def _legacy_services_stopped(record):
    import sys

    if sys.platform == "darwin":
        result = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/com.olympus.coordinator"],
            capture_output=True,
            timeout=10,
        )
        if result.returncode == 0:
            raise WorkbenchError(
                "Stop the identified Olympus launchd service before retiring files"
            )
    elif shutil.which("systemctl"):
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "olympus.service"],
            capture_output=True,
            timeout=10,
        )
        if result.returncode == 0:
            raise WorkbenchError(
                "Stop the identified Olympus user service before retiring files"
            )


def retire(destination, qualification):
    dest = Path(destination).resolve()
    record = verify(dest)
    proof = read_json(Path(qualification))
    if (
        not proof
        or proof.get("qualified_for_cutover") is not True
        or not 0 <= now() - proof.get("created", 0) < 86400
    ):
        raise WorkbenchError(
            "A current passed local cutover qualification is required; keep the old installation recoverable"
        )
    if proof.get("migration_id") != record["id"]:
        raise WorkbenchError("Qualification belongs to another migration")
    if not proof.get("legacy_processes_stopped") or not proof.get(
        "custom_changes_reconciled"
    ):
        raise WorkbenchError(
            "Reconcile local changes and confirm legacy processes stopped before removal"
        )
    required = {
        "installed_cli",
        "coordinator",
        "real_workers",
        "context_recovery",
        "terminal_gui",
        "migration_restore",
    }
    if not required.issubset(proof.get("gates", {})) or any(
        proof["gates"][g] != "passed" for g in required
    ):
        raise WorkbenchError("Required target-machine gates have not all passed")
    from .qualification import validate_cutover

    validate_cutover(proof)
    _legacy_services_stopped(record)
    quarantine = dest / "retired"
    quarantine.mkdir(exist_ok=True, mode=0o700)
    # Validate every root before moving any: changed files cannot be lost by a partial retirement.
    for i, entry in enumerate(record["roots"]):
        original = Path(entry["original"])
        target = quarantine / str(i)
        if entry["state"] == "retired" or (
            not original.exists()
            and not original.is_symlink()
            and (target.exists() or target.is_symlink())
        ):
            continue
        if file_manifest(original) != entry["manifest"]:
            raise WorkbenchError(
                "Legacy files changed after backup; make a fresh snapshot"
            )
        if original.is_dir() and not original.is_symlink():
            nested = [p for p in original.rglob(".git") if p.parent != original]
            if nested:
                raise WorkbenchError(
                    "Nested project repositories must be moved to their preserved location and retested before retiring this root"
                )
    record["phase"] = "retiring"
    atomic(dest / "migration.json", canonical(record))
    for i, entry in enumerate(record["roots"]):
        if entry["state"] == "retired":
            continue
        original = Path(entry["original"])
        target = quarantine / str(i)
        if original.exists() or original.is_symlink():
            if target.exists() or target.is_symlink():
                raise WorkbenchError("Retirement destination already exists")
            os.replace(original, target)
        elif not target.exists() and not target.is_symlink():
            raise WorkbenchError(
                "Interrupted migration has an unknown file disposition"
            )
        entry["state"] = "retired"
        entry["retired_path"] = str(target)
        atomic(dest / "migration.json", canonical(record))
    record["phase"] = "retired"
    atomic(dest / "migration.json", canonical(record))
    return {
        "phase": "retired",
        "backup": str(dest),
        "notice": "Old software is removed from active paths. Recovery copies remain private.",
    }


def restore(destination):
    dest = Path(destination).resolve()
    record = verify(dest)
    for entry in record["roots"]:
        original = Path(entry["original"])
        if original.exists() or original.is_symlink():
            if file_manifest(original) != entry["manifest"]:
                raise WorkbenchError(
                    "Restore would overwrite newer local data; choose a separate recovery location"
                )
    for entry in record["roots"]:
        original = Path(entry["original"])
        saved = dest / entry["copy"]
        if original.exists() or original.is_symlink():
            continue
        original.parent.mkdir(parents=True, exist_ok=True)
        if saved.is_symlink():
            os.symlink(os.readlink(saved), original)
        elif saved.is_dir():
            shutil.copytree(saved, original, symlinks=True)
        else:
            shutil.copy2(saved, original)
        if file_manifest(original) != entry["manifest"]:
            raise WorkbenchError("Restored content failed verification")
    return {
        "restored": len(record["roots"]),
        "services": "not automatically restarted; reconcile with the replacement before enabling",
    }

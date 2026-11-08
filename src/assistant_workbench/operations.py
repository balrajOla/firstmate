from __future__ import annotations

from contextlib import closing
import json
import hashlib
import os
import plistlib
import re
import shlex
import shutil
import sqlite3
import stat
import sys
from pathlib import Path
from .config import config
from .integrations import run_process
from .util import WorkbenchError, atomic, canonical, digest, now, read_json, uid


_STATE_ROOTS = {"archive", "knowledge", "config.json", "snapshots", "workspaces"}


def _os_owned_link(part):
    """Root-owned links in root-owned, non-shared directories (macOS /tmp, /var)."""
    if part.lstat().st_uid != 0:
        return False
    parent = part.parent.lstat()
    return parent.st_uid == 0 and not parent.st_mode & (stat.S_IWGRP | stat.S_IWOTH)


def _plain_path(value):
    """Do not hide a supplied symlink by resolving it before validation."""
    path = Path(os.path.abspath(Path(value).expanduser()))
    for part in (path, *path.parents):
        if part.is_symlink() and (part == path or not _os_owned_link(part)):
            raise WorkbenchError("Snapshot paths must not contain symlinks")
    return path


def _snapshot_name(name):
    if not isinstance(name, str) or not name or "\\" in name:
        raise WorkbenchError("Snapshot inventory contains an invalid relative path")
    path = Path(name)
    if (
        path.is_absolute()
        or any(part in {".", ".."} for part in name.split("/"))
        or path.as_posix() != name
    ):
        raise WorkbenchError("Snapshot inventory contains an invalid relative path")
    return path


def _snapshot_hash(path):
    _plain_path(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise WorkbenchError("Snapshot contains a non-regular file")
        result = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
        return result.hexdigest()


def _snapshot_inventory(root, selected=None):
    """Inventory every entry, including empty directories; never follow links."""
    root = _plain_path(root)
    files, directories = {}, []

    def visit(path):
        mode = path.lstat().st_mode
        name = path.relative_to(root).as_posix()
        if stat.S_ISLNK(mode):
            raise WorkbenchError("Snapshots reject file and directory symlinks")
        if stat.S_ISDIR(mode):
            directories.append(name)
            for child in sorted(path.iterdir()):
                visit(child)
        elif stat.S_ISREG(mode):
            files[name] = _snapshot_hash(path)
        else:
            raise WorkbenchError("Snapshots reject sockets, devices and special files")

    for child in sorted(root.iterdir()):
        if selected is None or child.name in selected:
            visit(child)
    return {"files": files, "directories": sorted(directories)}


def _copy_snapshot_files(source, destination, inventory):
    for name in inventory["directories"]:
        (destination / _snapshot_name(name)).mkdir(
            parents=True, exist_ok=True, mode=0o700
        )
    for name, expected in inventory["files"].items():
        relative = _snapshot_name(name)
        original, copied = source / relative, destination / relative
        if _snapshot_hash(original) != expected:
            raise WorkbenchError("Snapshot source changed during copying")
        copied.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(original, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as reader, copied.open("xb") as writer:
            if not stat.S_ISREG(os.fstat(reader.fileno()).st_mode):
                raise WorkbenchError("Snapshot source is not a regular file")
            shutil.copyfileobj(reader, writer, 1024 * 1024)
        copied.chmod(0o700 if original.stat().st_mode & 0o111 else 0o600)
        if _snapshot_hash(copied) != expected:
            raise WorkbenchError("Snapshot copy failed its content integrity check")


def _portable_workspaces(db, state_root, rewrite=False):
    exists = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='execution_workspaces'"
    ).fetchone()
    if not exists:
        return set()
    identities = set()
    for identity, location in db.execute(
        "SELECT id,path FROM execution_workspaces"
    ).fetchall():
        if not re.fullmatch(r"ws_[A-Za-z0-9_-]+", identity):
            raise WorkbenchError("Invalid owned workspace identity")
        relative = Path("workspaces") / identity
        if Path(location) not in {relative, state_root / relative}:
            raise WorkbenchError(
                "Workspace location is outside its owned state directory"
            )
        if rewrite:
            db.execute(
                "UPDATE execution_workspaces SET path=? WHERE id=?",
                (relative.as_posix(), identity),
            )
        identities.add(identity)
    return identities


def _check_workspace_directories(home, identities):
    workspace_root = home / "workspaces"
    if workspace_root.is_symlink():
        raise WorkbenchError("Owned workspace root must not be a symlink")
    actual = (
        {p.name for p in workspace_root.iterdir()} if workspace_root.exists() else set()
    )
    if actual != identities:
        raise WorkbenchError(
            "Owned workspace inventory has missing or unregistered directories"
        )
    if any(not (workspace_root / identity).is_dir() for identity in identities):
        raise WorkbenchError("Owned workspace is not a directory")


def backup_state(store, destination):
    home, dest = _plain_path(store.home), _plain_path(destination)
    if dest.exists() or dest.is_relative_to(home) or home.is_relative_to(dest):
        raise WorkbenchError("Backup must be a new directory outside active state")
    db = store.connect()
    try:
        version = db.execute("PRAGMA data_version").fetchone()[0]
        if db.execute(
            "SELECT name FROM sqlite_master WHERE name='execution_runs'"
        ).fetchone():
            if db.execute(
                "SELECT 1 FROM execution_runs WHERE status='running' LIMIT 1"
            ).fetchone():
                raise WorkbenchError(
                    "Stop or reconcile active execution runs before a filesystem backup"
                )
        identities = _portable_workspaces(db, home)
        _check_workspace_directories(home, identities)
        before = _snapshot_inventory(home, _STATE_ROOTS)
        dest.mkdir(parents=True, mode=0o700)
        try:
            # VACUUM INTO writes one rollback-journal file. backup() would copy
            # the live WAL mode, and some SQLite builds then leave -wal/-shm
            # sidecars that the restore inventory rejects.
            db.execute("VACUUM INTO ?", (str(dest / "workbench.sqlite"),))
            with closing(sqlite3.connect(dest / "workbench.sqlite")) as target:
                _portable_workspaces(target, home, rewrite=True)
                target.commit()
            _copy_snapshot_files(home, dest, before)
            if (
                before != _snapshot_inventory(home, _STATE_ROOTS)
                or version != db.execute("PRAGMA data_version").fetchone()[0]
            ):
                raise WorkbenchError(
                    "State changed during backup; quiesce writers and retry"
                )
            manifest = _snapshot_inventory(dest)
            atomic(
                dest / "backup.json",
                canonical({"schema": 2, "created": now(), **manifest}),
            )
        except Exception:
            shutil.rmtree(dest)
            raise
    finally:
        db.close()
    return {
        "path": str(dest),
        "files": len(manifest["files"]),
        "workspace_count": len(identities),
    }


def restore_state(source, destination):
    src, dest = _plain_path(source), _plain_path(destination)
    if (
        not src.is_dir()
        or dest.exists()
        or dest.is_relative_to(src)
        or src.is_relative_to(dest)
    ):
        raise WorkbenchError(
            "Restore requires a verified snapshot and a new separate destination"
        )
    inventory = _snapshot_inventory(src)
    if "backup.json" not in inventory["files"]:
        raise WorkbenchError("Snapshot manifest is missing")
    try:
        manifest = json.loads((src / "backup.json").read_text())
    except (ValueError, UnicodeError) as exc:
        raise WorkbenchError("Snapshot manifest is malformed") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") not in {1, 2}
        or not isinstance(manifest.get("files"), dict)
    ):
        raise WorkbenchError("Snapshot manifest schema is unsupported")
    files = manifest["files"]
    if "workbench.sqlite" not in files:
        raise WorkbenchError("Snapshot database is missing")
    for name, sha in files.items():
        relative = _snapshot_name(name)
        if (
            relative.parts[0] not in _STATE_ROOTS | {"workbench.sqlite"}
            or not isinstance(sha, str)
            or not re.fullmatch(r"[0-9a-f]{64}", sha)
        ):
            raise WorkbenchError("Snapshot inventory contains an unsupported entry")
    if manifest["schema"] == 2:
        directories = manifest.get("directories")
        if (
            not isinstance(directories, list)
            or not all(isinstance(name, str) for name in directories)
            or len(directories) != len(set(directories))
        ):
            raise WorkbenchError("Snapshot directory inventory is malformed")
    else:
        directories = sorted(
            {
                parent.as_posix()
                for name in files
                for parent in Path(name).parents
                if parent != Path(".")
            }
        )
    for name in directories:
        if _snapshot_name(name).parts[0] not in _STATE_ROOTS:
            raise WorkbenchError("Snapshot directory is outside owned state")
    expected = {"files": files, "directories": sorted(directories)}
    actual = {
        "files": {
            name: sha
            for name, sha in inventory["files"].items()
            if name != "backup.json"
        },
        "directories": inventory["directories"],
    }
    if actual != expected:
        raise WorkbenchError(
            "Backup integrity check failed: changed, missing or unlisted entries"
        )
    dest.mkdir(parents=True, mode=0o700)
    try:
        _copy_snapshot_files(src, dest, expected)
        if _snapshot_inventory(dest) != expected:
            raise WorkbenchError("Restored files failed their integrity check")
        with closing(
            sqlite3.connect((dest / "workbench.sqlite").as_uri() + "?mode=ro", uri=True)
        ) as db:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise WorkbenchError("Restored database failed its integrity check")
            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if not {"meta", "projects", "requests", "sources"}.issubset(tables):
                raise WorkbenchError(
                    "Restored database lacks the Workbench state schema"
                )
            _check_workspace_directories(dest, _portable_workspaces(db, dest))
    except Exception as exc:
        shutil.rmtree(dest)
        if isinstance(exc, sqlite3.DatabaseError):
            raise WorkbenchError(
                "Restored database failed its integrity check"
            ) from exc
        raise
    return {
        "path": str(dest),
        "files": len(files),
        "notice": "Workers are not restarted automatically. Reconcile the coordinator and re-plan runtime commands before dispatch.",
    }


def install(source, prefix, python=None):
    source = Path(source).resolve(strict=True)
    prefix = Path(prefix).expanduser().resolve()
    if not (source / "pyproject.toml").is_file():
        raise WorkbenchError("Source is not an Assistant Workbench distribution")
    content = [
        [name, digest((source / name).read_bytes())]
        for name in ("pyproject.toml", "LICENSE", "requirements-runtime.lock")
    ]
    for path in sorted((source / "src").rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            content.append([str(path.relative_to(source)), digest(path.read_bytes())])
    version = digest(canonical(content))[:16]
    release = prefix / "lib/assistant-workbench/releases" / version
    old = read_json(prefix / "lib/assistant-workbench/install.json")
    _check_launcher(prefix, old)
    if not (release / "ready.json").exists():
        if release.exists():
            raise WorkbenchError(
                "Incomplete release exists; inspect it and use a new prefix or remove only that incomplete release"
            )
        release.mkdir(parents=True)
        package = release / "source"
        package.mkdir()
        shutil.copy2(source / "pyproject.toml", package / "pyproject.toml")
        shutil.copy2(source / "LICENSE", package / "LICENSE")
        shutil.copy2(
            source / "requirements-runtime.lock", package / "requirements-runtime.lock"
        )
        shutil.copytree(
            source / "src",
            package / "src",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info"),
        )
        run_process(
            [python or sys.executable, "-m", "venv", str(release / "venv")], timeout=90
        )
        interpreter = release / "venv/bin/python"
        run_process(
            [
                str(interpreter),
                "-m",
                "pip",
                "install",
                "--constraint",
                str(package / "requirements-runtime.lock"),
                str(package) + "[tui]",
            ],
            timeout=600,
        )
        run_process(
            [str(interpreter), "-m", "assistant_workbench", "--version"], timeout=20
        )
        atomic(
            release / "ready.json",
            canonical({"schema": 1, "content_revision": version, "created": now()}),
        )
    return _activate(prefix, release, old)


def rollback(prefix):
    """Point the owned launcher back at the previously active release."""
    prefix = Path(prefix).expanduser().resolve()
    old = read_json(prefix / "lib/assistant-workbench/install.json")
    # Older receipts could name the active release as its own predecessor.
    if not old or not old.get("previous") or old["previous"] == old.get("release"):
        raise WorkbenchError("No previous release is recorded for this prefix")
    _check_launcher(prefix, old)
    previous = Path(old["previous"])
    if previous.parent != prefix / "lib/assistant-workbench/releases" or not (
        previous / "ready.json"
    ).is_file():
        raise WorkbenchError("The previous release is missing or incomplete; reinstall it from its source")
    run_process([str(previous / "venv/bin/python"), "-m", "assistant_workbench", "--version"], timeout=20)
    return {**_activate(prefix, previous, old), "rolled_back_from": old["release"]}


def _check_launcher(prefix, old):
    launcher = prefix / "bin/awb"
    if launcher.is_symlink():
        raise WorkbenchError(
            "Existing launcher is a symlink; preserve it and choose another prefix"
        )
    if launcher.exists() and (
        not old or digest(launcher.read_bytes()) != old.get("launcher_sha256")
    ):
        raise WorkbenchError(
            "Existing awb launcher is not owned by this installer; choose another prefix"
        )


def _activate(prefix, release, old):
    """Point the owned launcher at a ready release and record what it replaced."""
    import shlex

    text = (
        "#!/bin/sh\n# assistant-workbench managed launcher\nexec "
        + shlex.quote(str(release / "venv/bin/python"))
        + ' -m assistant_workbench "$@"\n'
    )
    atomic(prefix / "bin/awb", text, 0o755)
    current = old.get("release") if old else None
    result = {
        "schema": 1,
        "prefix": str(prefix),
        "release": str(release),
        "content_revision": release.name,
        # Reinstalling the active release keeps the real rollback target.
        "previous": current if current != str(release) else (old or {}).get("previous"),
        "launcher_sha256": digest(text),
        "created": now(),
    }
    atomic(prefix / "lib/assistant-workbench/install.json", canonical(result))
    return result


def uninstall(prefix):
    prefix = Path(prefix).expanduser().resolve()
    receipt = read_json(prefix / "lib/assistant-workbench/install.json")
    launcher = prefix / "bin/awb"
    if not receipt:
        raise WorkbenchError("No owned installation found")
    if launcher.is_symlink() or (
        launcher.exists()
        and digest(launcher.read_bytes()) != receipt["launcher_sha256"]
    ):
        raise WorkbenchError("Launcher changed; refusing to delete local modifications")
    if launcher.exists():
        launcher.unlink()
    return {
        "removed": str(launcher),
        "preserved": [
            str(prefix / "lib/assistant-workbench"),
            "all application state and project repositories",
        ],
        "notice": "Stop and remove the owned Workbench service before uninstalling. Release copies are retained for recovery.",
    }


def service_definition(home, executable, kind="launchd"):
    executable = str(Path(executable).expanduser().resolve(strict=True))
    home = Path(home).resolve()
    if kind == "launchd":
        return plistlib.dumps(
            {
                "Label": "com.assistant-workbench.bridge",
                "ProgramArguments": [executable, "--home", str(home), "serve"],
                "RunAtLoad": True,
                "KeepAlive": True,
                "ThrottleInterval": 10,
                "StandardOutPath": str(home / "service.stdout.log"),
                "StandardErrorPath": str(home / "service.stderr.log"),
            }
        ).decode()
    if kind == "systemd":
        import shlex

        # systemd's quoting differs from shell quoting; reject control characters and escape quotes.
        def quote(s):
            if any(c in s for c in "\n\r\x00"):
                raise WorkbenchError("Invalid service path")
            return (
                '"'
                + s.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
                + '"'
            )

        return (
            "[Unit]\nDescription=Assistant Workbench bridge\n[Service]\nExecStart="
            + quote(executable)
            + " --home "
            + quote(str(home))
            + " serve\nRestart=on-failure\nRestartSec=10\n[Install]\nWantedBy=default.target\n"
        )
    raise WorkbenchError("Unsupported service manager")


def _logged_step(log, argv, **kwargs):
    """Run an installer step and keep its full output in a private log."""
    result = run_process(argv, allowed_codes=range(-255, 256), **kwargs)
    descriptor = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "a") as stream:
        stream.write(
            "$ " + shlex.join(argv) + "\n" + result["stdout"] + result["stderr"]
            + "[exit " + str(result["exit_code"]) + "]\n"
        )
    if result["exit_code"]:
        tail = (result["stderr"].strip() or result["stdout"].strip())[-600:]
        raise WorkbenchError(
            "Installer step failed with exit " + str(result["exit_code"]) + ": "
            + shlex.join(argv) + ("\n" + tail if tail else "")
        )
    return result


def install_dependencies(home, names, registry=None):
    registry = registry or json.loads(
        (Path(__file__).parent / "assets/dependencies.json").read_text()
    )
    result = []
    for name in names:
        if name not in registry:
            raise WorkbenchError("Unknown dependency: " + name)
        spec = registry[name]
        dest = home / "dependencies" / name / spec["revision"]
        if (dest / "installed.json").exists():
            result.append(read_json(dest / "installed.json"))
            continue
        if dest.exists():
            raise WorkbenchError("Dependency installation is incomplete: " + str(dest))
        if spec["kind"] not in {"git", "python"}:
            raise WorkbenchError(
                "Dependency requires its documented platform installer: " + name
            )
        dest.mkdir(parents=True)
        try:
            result.append(_install_dependency(name, spec, dest))
        except Exception as exc:
            # Keep the failed attempt for inspection, out of the way of a retry.
            failed = dest.with_name(dest.name + uid(".failed-"))
            dest.rename(failed)
            log = failed / "install.log"
            raise WorkbenchError(
                str(exc) + ("\nBuild log: " + str(log) if log.is_file() else "")
            ) from exc
    return result


def _install_dependency(name, spec, dest):
    log = dest / "install.log"
    if spec["kind"] == "git":
        repo = dest / "source"
        _logged_step(
            log, ["git", "clone", "--no-checkout", spec["url"], str(repo)],
            timeout=300,
        )
        _logged_step(
            log, ["git", "-C", str(repo), "checkout", "--detach", spec["revision"]],
            timeout=90,
        )
        actual = run_process(["git", "-C", str(repo), "rev-parse", "HEAD"])[
            "stdout"
        ].strip()
        if actual != spec["revision"]:
            raise WorkbenchError("Dependency revision mismatch")
        for argv in spec.get("build", []):
            _logged_step(log, argv, cwd=repo, timeout=1800)
        if name == "lcm":
            for origin, target in [
                (repo / "src/prompts", repo / "dist/src/prompts"),
                (
                    repo / "src/eval/compaction-shadow",
                    repo / "dist/src/eval/compaction-shadow",
                ),
            ]:
                target.mkdir(parents=True, exist_ok=True)
                for asset in origin.glob("*.yaml"):
                    shutil.copy2(asset, target / asset.name)
            (repo / "dist/installer").mkdir(parents=True, exist_ok=True)
            shutil.copy2(
                repo / "installer/setup.sh", repo / "dist/installer/setup.sh"
            )
            run_process(["node", "scripts/write-build-id.mjs"], cwd=repo)
    else:
        _logged_step(
            log, [sys.executable, "-m", "venv", str(dest / "venv")], timeout=90
        )
        _logged_step(
            log,
            [
                str(dest / "venv/bin/python"),
                "-m",
                "pip",
                "install",
                spec["package"] + "==" + spec["revision"],
            ],
            timeout=1200,
        )
    record = {
        "name": name,
        "revision": spec["revision"],
        "path": str(dest),
        "state": (
            "installed"
            if spec["kind"] == "python"
            or spec.get("build")
            or name in {"superpowers", "humanizer", "elements-of-style"}
            else "source_fetched"
        ),
        "created": now(),
        "notice": "Installed is distinct from configured, activated and verified.",
    }
    atomic(dest / "installed.json", canonical(record))
    return record

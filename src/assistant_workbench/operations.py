from __future__ import annotations

import json
import os
import plistlib
import shutil
import sqlite3
import sys
from pathlib import Path
from .config import config
from .integrations import run_process
from .util import WorkbenchError, atomic, canonical, digest, now, read_json


def backup_state(store, destination):
    dest = Path(destination).resolve()
    if dest.exists() or dest.is_relative_to(store.home):
        raise WorkbenchError("Backup must be a new directory outside active state")
    dest.mkdir(parents=True, mode=0o700)
    db = store.connect()
    target = sqlite3.connect(dest / "workbench.sqlite")
    try:
        db.backup(target)
    finally:
        db.close()
        target.close()
    from .migration import file_manifest

    for name in ["archive", "knowledge", "config.json", "snapshots"]:
        src = store.home / name
        before = file_manifest(src) if src.exists() else None
        if src.is_dir():
            shutil.copytree(src, dest / name, symlinks=True)
        elif src.is_file():
            shutil.copy2(src, dest / name)
        if before is not None and (
            file_manifest(src) != before or file_manifest(dest / name) != before
        ):
            raise WorkbenchError(
                "State changed during backup; quiesce its writers and retry to a new destination"
            )
    manifest = {
        str(p.relative_to(dest)): digest(p.read_bytes())
        for p in dest.rglob("*")
        if p.is_file() and not p.is_symlink()
    }
    atomic(
        dest / "backup.json",
        canonical({"schema": 1, "created": now(), "files": manifest}),
    )
    return {"path": str(dest), "files": len(manifest)}


def restore_state(source, destination):
    src = Path(source).resolve()
    dest = Path(destination).resolve()
    manifest = read_json(src / "backup.json")
    if not manifest or dest.exists():
        raise WorkbenchError(
            "Restore requires a verified snapshot and a new destination"
        )
    for name, sha in manifest["files"].items():
        file = (src / name).resolve()
        if (
            not file.is_relative_to(src)
            or not file.is_file()
            or digest(file.read_bytes()) != sha
        ):
            raise WorkbenchError("Backup integrity check failed")
    shutil.copytree(src, dest, symlinks=True)
    return {
        "path": str(dest),
        "notice": "Workers are not restarted automatically. Reconcile the coordinator before dispatch.",
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
    launcher = prefix / "bin/awb"
    receipt = prefix / "lib/assistant-workbench/install.json"
    old = read_json(receipt)
    if launcher.exists() and (
        not old or digest(launcher.read_bytes()) != old.get("launcher_sha256")
    ):
        raise WorkbenchError(
            "Existing awb launcher is not owned by this installer; choose another prefix"
        )
    if launcher.is_symlink():
        raise WorkbenchError(
            "Existing launcher is a symlink; preserve it and choose another prefix"
        )
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
    import shlex

    text = (
        "#!/bin/sh\n# assistant-workbench managed launcher\nexec "
        + shlex.quote(str(release / "venv/bin/python"))
        + ' -m assistant_workbench "$@"\n'
    )
    atomic(launcher, text, 0o755)
    result = {
        "schema": 1,
        "prefix": str(prefix),
        "release": str(release),
        "content_revision": version,
        "launcher_sha256": digest(text),
        "previous": old.get("release") if old else None,
        "created": now(),
    }
    atomic(receipt, canonical(result))
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


def install_dependencies(home, names):
    registry = json.loads(
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
        dest.mkdir(parents=True)
        if spec["kind"] == "git":
            repo = dest / "source"
            run_process(
                ["git", "clone", "--no-checkout", spec["url"], str(repo)], timeout=300
            )
            run_process(
                ["git", "-C", str(repo), "checkout", "--detach", spec["revision"]],
                timeout=90,
            )
            actual = run_process(["git", "-C", str(repo), "rev-parse", "HEAD"])[
                "stdout"
            ].strip()
            if actual != spec["revision"]:
                raise WorkbenchError("Dependency revision mismatch")
            for argv in spec.get("build", []):
                run_process(argv, cwd=repo, timeout=1800)
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
        elif spec["kind"] == "python":
            run_process([sys.executable, "-m", "venv", str(dest / "venv")], timeout=90)
            run_process(
                [
                    str(dest / "venv/bin/python"),
                    "-m",
                    "pip",
                    "install",
                    spec["package"] + "==" + spec["revision"],
                ],
                timeout=1200,
            )
        else:
            raise WorkbenchError(
                "Dependency requires its documented platform installer: " + name
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
        result.append(record)
    return result

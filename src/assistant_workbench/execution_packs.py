"""Inspectable language checks and artifact receipts in private workspace copies.

This is a local execution boundary, not an operating-system sandbox. Project
code and reviewed package scripts run with the operator's filesystem identity.
No model is invoked and no semantic outcome is accepted by this module.
"""

from __future__ import annotations

from contextlib import closing
from html.parser import HTMLParser
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import selectors
import shlex
import shutil
import signal
import subprocess
import sys
import time
import zipfile
from urllib.parse import unquote, urlsplit

from .context import Context
from .util import WorkbenchError, canonical, digest, now, safe_text, uid


SCHEMA = """
CREATE TABLE IF NOT EXISTS execution_workspaces(
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),
 path TEXT NOT NULL UNIQUE,source TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS execution_plans(
 id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL REFERENCES execution_workspaces(id),
 payload TEXT NOT NULL,sha256 TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS execution_runs(
 id TEXT PRIMARY KEY,plan_id TEXT NOT NULL REFERENCES execution_plans(id),
 status TEXT NOT NULL,cancel_requested INTEGER NOT NULL DEFAULT 0,
 result TEXT,created REAL NOT NULL,finished REAL);
CREATE TABLE IF NOT EXISTS artifact_manifests(
 id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL REFERENCES execution_workspaces(id),
 payload TEXT NOT NULL,sha256 TEXT NOT NULL,created REAL NOT NULL);
"""

EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "target",
    "dist",
    "build",
    ".next",
    ".expo",
}
LOCKS = {"package-lock.json": "npm", "pnpm-lock.yaml": "pnpm", "yarn.lock": "yarn"}
MAX_FILES = 10000
MAX_SOURCE_BYTES = 100 * 1024 * 1024


def _secret_name(path):
    return (
        path.name == ".env"
        or path.name.startswith(".env.")
        or path.suffix in {".pem", ".key"}
    )


def _file(root, relative):
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise WorkbenchError("Use a relative file path within the registered workspace")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise WorkbenchError("Workspace and artifact symlinks are not followed")
    if not current.is_file() or not current.resolve().is_relative_to(root.resolve()):
        raise WorkbenchError("Required workspace file is missing or outside its root")
    return current


def _inventory(root):
    records = []
    total = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs:
            if (Path(directory) / name).is_symlink():
                raise WorkbenchError("Workspace contains a directory symlink")
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS)
        for name in sorted(files):
            path = Path(directory) / name
            if path.is_symlink():
                raise WorkbenchError("Workspace contains a file symlink")
            if name == ".git" or _secret_name(path) or path.suffix in {".pyc", ".pyo"}:
                continue
            size = path.stat().st_size
            total += size
            if len(records) >= MAX_FILES or total > MAX_SOURCE_BYTES:
                raise WorkbenchError(
                    "Workspace inventory exceeds the 10,000-file / 100-MiB bound"
                )
            records.append(
                {
                    "path": str(path.relative_to(root)),
                    "bytes": size,
                    "sha256": digest(path.read_bytes()),
                }
            )
    return records


def _json_file(root, name):
    try:
        value = json.loads(_file(root, name).read_text())
    except (ValueError, UnicodeError) as exc:
        raise WorkbenchError("Invalid project JSON: " + name) from exc
    if not isinstance(value, dict):
        raise WorkbenchError("Project JSON must contain an object: " + name)
    return value


def _stop_group(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=0.25)
    except subprocess.TimeoutExpired:
        pass
    # A parent may exit while a child still owns the group's output pipes.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def _script_test_framework(script, package, names):
    """Interpret totals only for a directly declared, inspected test runner."""
    if any(character in script for character in "&|;`$<>\n\r"):
        return None
    try:
        tokens = shlex.split(script)
    except ValueError:
        return None
    tests = any(
        re.search(
            r"(?:^|/)(?:test_[^/]+|[^/]+[._](?:test|spec))\.(?:[cm]?js|tsx?)$", name
        )
        or ("__tests__" in Path(name).parts and name.endswith((".js", ".ts", ".tsx")))
        for name in names
    )
    if not tokens or not tests:
        return None
    if (
        tokens[0] == "node"
        and "--test" in tokens
        and not {"-e", "--eval", "-p", "--print"}.intersection(tokens)
    ):
        return "node" if "--test-reporter=tap" in tokens else "javascript"
    dependencies = {}
    for group in ("dependencies", "devDependencies"):
        declared = package.get(group, {})
        if isinstance(declared, dict):
            dependencies.update(declared)
    if tokens[0] in {"jest", "vitest"} and tokens[0] in dependencies:
        return "javascript"
    return None


class ExecutionPacks:
    def __init__(self, store):
        self.store = store
        with closing(store.connect()) as db:
            db.executescript(SCHEMA)

    def _workspace(self, workspace_id):
        record = self.store.one(
            "SELECT * FROM execution_workspaces WHERE id=?", (workspace_id,)
        )
        relative = Path("workspaces") / workspace_id
        recorded = Path(record["path"])
        expected = self.store.home / "workspaces" / workspace_id
        # Older state used an absolute location. New receipts are portable when
        # the verified state tree is restored under a different directory.
        if recorded not in {relative, expected}:
            raise WorkbenchError("Registered workspace path is outside owned state")
        path = expected
        if path != expected or path.is_symlink() or not path.is_dir():
            raise WorkbenchError(
                "Registered private workspace is unavailable or changed"
            )
        if path.resolve() != expected.absolute() or expected.parent.is_symlink():
            raise WorkbenchError("Registered workspace boundary changed")
        return record, path

    def create_workspace(self, project):
        """Copy current project files, excluding dependencies, outputs and secrets."""
        record = self.store.project(project)
        if not record["repository"]:
            raise WorkbenchError("The project needs an explicit repository directory")
        source = Path(record["repository"])
        if source.is_symlink() or not source.is_dir():
            raise WorkbenchError("Project repository must be a real directory")
        source = source.resolve()
        destination_parent = self.store.home / "workspaces"
        if source == destination_parent or destination_parent.is_relative_to(source):
            raise WorkbenchError(
                "State/workspace directory must not be inside the source repository"
            )
        if destination_parent.is_symlink():
            raise WorkbenchError("Workspace parent must not be a symlink")
        files = _inventory(source)
        identity = uid("ws_")
        destination = destination_parent / identity
        destination.mkdir(parents=True, mode=0o700)
        try:
            for item in files:
                src = _file(source, item["path"])
                data = src.read_bytes()
                if digest(data) != item["sha256"]:
                    raise WorkbenchError(
                        "Source changed during workspace creation; retry from a stable revision"
                    )
                target = destination / item["path"]
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                target.write_bytes(data)
                target.chmod(0o700 if src.stat().st_mode & 0o111 else 0o600)
            with self.store.transaction() as db:
                db.execute(
                    "INSERT INTO execution_workspaces VALUES(?,?,?,?,?)",
                    (
                        identity,
                        record["id"],
                        str(Path("workspaces") / identity),
                        str(source),
                        now(),
                    ),
                )
                self.store.event(
                    db,
                    "execution.workspace_created",
                    identity,
                    {
                        "project": record["id"],
                        "files": len(files),
                        "source_sha256": digest(canonical(files)),
                    },
                )
        except Exception:
            shutil.rmtree(destination)
            raise
        return {
            "id": identity,
            "project_id": record["id"],
            "path": str(destination),
            "files": len(files),
            "source_sha256": digest(canonical(files)),
            "notice": "Private copy, not an OS sandbox. Dependencies and secret files were not copied.",
        }

    def inspect(self, workspace_id):
        record, root = self._workspace(workspace_id)
        files = _inventory(root)
        names = {item["path"] for item in files}
        languages = []
        if "pyproject.toml" in names or any(name.endswith(".py") for name in names):
            languages.append("python")
        if "package.json" in names or any(
            name.endswith((".js", ".mjs", ".cjs", ".ts", ".tsx")) for name in names
        ):
            languages.append(
                "typescript"
                if "tsconfig.json" in names
                or any(name.endswith((".ts", ".tsx")) for name in names)
                else "javascript"
            )
        if "Cargo.toml" in names:
            languages.append("rust")
        package = _json_file(root, "package.json") if "package.json" in names else {}
        return {
            "workspace_id": workspace_id,
            "project_id": record["project_id"],
            "languages": languages,
            "files": files,
            "source_sha256": digest(canonical(files)),
            "package_managers": sorted(
                {manager for name, manager in LOCKS.items() if name in names}
            ),
            "declared_scripts": package.get("scripts", {}),
            "runtimes": {
                name: shutil.which(name)
                for name in ["node", "npm", "pnpm", "yarn", "cargo"]
            },
            "python": sys.executable,
            "notice": "Inspection does not execute project code or certify a test suite.",
        }

    def list_workspaces(self, project=None):
        if project:
            return self.store.rows(
                "SELECT * FROM execution_workspaces WHERE project_id=? ORDER BY created DESC",
                (self.store.project(project)["id"],),
            )
        return self.store.rows(
            "SELECT * FROM execution_workspaces ORDER BY created DESC"
        )

    def list_runs(self, workspace_id=None, limit=50):
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
        ):
            raise WorkbenchError("Run-list limit must be 1–100")
        if workspace_id:
            self._workspace(workspace_id)
        return self.store.rows(
            "SELECT r.id,r.plan_id,r.status,r.cancel_requested,r.created,r.finished,p.workspace_id "
            "FROM execution_runs r JOIN execution_plans p ON r.plan_id=p.id "
            "WHERE (? IS NULL OR p.workspace_id=?) ORDER BY r.created DESC LIMIT ?",
            (workspace_id, workspace_id, limit),
        )

    def get_run(self, run_id):
        record = self.store.one("SELECT * FROM execution_runs WHERE id=?", (run_id,))
        return {
            **record,
            "result": json.loads(record["result"]) if record["result"] else None,
        }

    def plan(
        self, workspace_id, language=None, goal="test", allow_package_scripts=False
    ):
        """Resolve a fixed check command from inspected project files; never install."""
        if not isinstance(allow_package_scripts, bool):
            raise WorkbenchError("Package-script review must be an explicit boolean")
        _, root = self._workspace(workspace_id)
        observed = self.inspect(workspace_id)
        if language is None:
            if len(observed["languages"]) != 1:
                raise WorkbenchError(
                    "Choose a language explicitly for an empty or multi-language project"
                )
            language = observed["languages"][0]
        if language not in {
            "python",
            "javascript",
            "typescript",
            "rust",
        } or goal not in {"test", "build", "typecheck"}:
            raise WorkbenchError(
                "Choose a supported language and test/build/typecheck goal"
            )
        names = {item["path"] for item in observed["files"]}
        command = []
        reason = None
        framework = None
        if language == "python":
            tests = sorted(
                name
                for name in names
                if name.endswith(".py")
                and (Path(name).name.startswith("test_") or name.endswith("_test.py"))
            )
            if goal == "test":
                if not tests:
                    reason = "No Python test files were discovered"
                elif importlib.util.find_spec("pytest") is None:
                    reason = (
                        "pytest is not installed in the selected Python environment"
                    )
                else:
                    command, framework = [
                        sys.executable,
                        "-m",
                        "pytest",
                        "-q",
                        *tests,
                    ], "pytest"
            elif goal == "build":
                if "pyproject.toml" not in names:
                    reason = "No pyproject.toml defines a Python package build"
                elif importlib.util.find_spec("build") is None:
                    reason = "Python build is not installed"
                else:
                    command = [sys.executable, "-m", "build", "--no-isolation"]
            else:
                reason = "Python typechecking requires a separately reviewed project tool configuration"
        elif language in {"javascript", "typescript"}:
            node = shutil.which("node")
            package = (
                _json_file(root, "package.json") if "package.json" in names else {}
            )
            scripts = package.get("scripts", {})
            if not isinstance(scripts, dict):
                raise WorkbenchError("package.json scripts must be an object")
            script = "typecheck" if goal == "typecheck" else goal
            if script in scripts:
                if not isinstance(scripts[script], str) or not scripts[script].strip():
                    reason = "Declared package script is empty or malformed"
                elif not allow_package_scripts:
                    reason = "Review the declared script and explicitly enable package-script execution"
                else:
                    managers = observed["package_managers"]
                    declaration = package.get("packageManager", "")
                    if not isinstance(declaration, str):
                        raise WorkbenchError("packageManager must be a string")
                    declared = declaration.split("@", 1)[0]
                    manager = declared or (
                        managers[0]
                        if len(managers) == 1
                        else "npm" if not managers else None
                    )
                    if manager not in {"npm", "pnpm", "yarn"} or (
                        managers and any(m != manager for m in managers)
                    ):
                        reason = (
                            "Conflicting or unsupported package-manager declarations"
                        )
                    elif not node or not shutil.which(manager):
                        reason = (
                            "The selected Node/package-manager runtime is unavailable"
                        )
                    else:
                        command = [shutil.which(manager), "run", script]
                        framework = (
                            _script_test_framework(scripts[script], package, names)
                            if goal == "test"
                            else None
                        )
            elif language == "javascript" and goal == "test":
                tests = sorted(
                    name
                    for name in names
                    if name.endswith((".test.js", ".test.mjs", ".test.cjs"))
                )
                if not tests:
                    reason = (
                        "No Node test files or declared test script were discovered"
                    )
                elif not node:
                    reason = "Node is not installed"
                else:
                    command, framework = [
                        node,
                        "--test",
                        "--test-reporter=tap",
                        *tests,
                    ], "node"
            else:
                reason = "No reviewed package script defines the requested operation"
        else:
            if "Cargo.toml" not in names:
                reason = "No Cargo.toml defines this Rust project"
            elif not shutil.which("cargo"):
                reason = "Cargo is not installed"
            elif "Cargo.lock" not in names:
                reason = (
                    "Generate and review Cargo.lock before offline locked execution"
                )
            else:
                command = [
                    shutil.which("cargo"),
                    {"typecheck": "check", "build": "build", "test": "test"}[goal],
                    "--locked",
                    "--offline",
                ]
                framework = "cargo" if goal == "test" else None
        identity = uid("xp_")
        payload = {
            "id": identity,
            "workspace_id": workspace_id,
            "language": language,
            "goal": goal,
            "framework": framework,
            "source_sha256": observed["source_sha256"],
            "command": command,
            "status": "blocked" if reason else "ready",
            "reason": reason,
            "package_scripts_reviewed": bool(allow_package_scripts),
            "notice": "Commands run project code. This plan does not approve its semantic result or create an OS sandbox.",
        }
        sha = digest(canonical(payload))
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO execution_plans VALUES(?,?,?,?,?)",
                (identity, workspace_id, canonical(payload), sha, now()),
            )
        return {**payload, "sha256": sha}

    def cancel(self, run_id):
        with self.store.transaction() as db:
            run = db.execute(
                "SELECT * FROM execution_runs WHERE id=?", (run_id,)
            ).fetchone()
            if not run:
                raise WorkbenchError("Unknown execution run")
            if run["status"] == "running":
                db.execute(
                    "UPDATE execution_runs SET cancel_requested=1 WHERE id=?", (run_id,)
                )
                self.store.event(db, "execution.cancel_requested", run_id, {})
                return {"run_id": run_id, "status": "stop_requested"}
            return {"run_id": run_id, "status": run["status"]}

    def _cancelled(self, run_id):
        return bool(
            self.store.one(
                "SELECT cancel_requested FROM execution_runs WHERE id=?", (run_id,)
            )["cancel_requested"]
        )

    def run(
        self,
        plan_id,
        expected_sha256,
        timeout=120,
        max_output_bytes=1024 * 1024,
        memory_mb=None,
    ):
        for value, low, high, name in [
            (timeout, 0.1, 600, "timeout"),
            (max_output_bytes, 256, 8 * 1024 * 1024, "max_output_bytes"),
        ]:
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not low <= value <= high
            ):
                raise WorkbenchError(name + " exceeds the supported execution bounds")
        if not isinstance(max_output_bytes, int) or (
            memory_mb is not None
            and (
                isinstance(memory_mb, bool)
                or not isinstance(memory_mb, int)
                or not 64 <= memory_mb <= 16384
            )
        ):
            raise WorkbenchError(
                "Output bytes and optional memory MiB must be bounded integers"
            )
        if memory_mb is not None and sys.platform != "linux":
            raise WorkbenchError(
                "An explicit memory bound is currently supported only on Linux; "
                "choose a Linux worker or omit memory_mb"
            )
        record = self.store.one("SELECT * FROM execution_plans WHERE id=?", (plan_id,))
        if (
            expected_sha256 != record["sha256"]
            or digest(record["payload"]) != record["sha256"]
        ):
            raise WorkbenchError("Execution plan differs from the reviewed receipt")
        plan = json.loads(record["payload"])
        _, root = self._workspace(plan["workspace_id"])
        if self.inspect(plan["workspace_id"])["source_sha256"] != plan["source_sha256"]:
            raise WorkbenchError(
                "Workspace changed after inspection; create and review a fresh plan"
            )
        if plan["status"] != "ready":
            return {
                "status": "blocked",
                "reason": plan["reason"],
                "plan_id": plan_id,
                "executed": False,
            }
        identity = uid("run_")
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO execution_runs(id,plan_id,status,created) VALUES(?,?,'running',?)",
                (identity, plan_id, now()),
            )
            self.store.event(
                db,
                "execution.started",
                identity,
                {"workspace": plan["workspace_id"], "plan": plan_id},
            )
        result = self._execute(
            plan, root, identity, timeout, max_output_bytes, memory_mb
        )
        result.update(
            run_id=identity,
            plan_id=plan_id,
            workspace_id=plan["workspace_id"],
            executed=True,
            semantic_acceptance="requires independent review",
            isolation="private copy; no OS sandbox",
        )
        if result["status"] == "passed" and plan["goal"] == "test":
            count = _test_count(
                result["stdout"] + "\n" + result["stderr"], plan["framework"]
            )
            result["tests_observed"] = count
            if count is None or count == 0:
                result.update(
                    status="not_run",
                    reason="The command exited zero but no executed tests could be established",
                )
        with self.store.transaction() as db:
            db.execute(
                "UPDATE execution_runs SET status=?,result=?,finished=? WHERE id=?",
                (result["status"], canonical(result), now(), identity),
            )
            self.store.event(
                db,
                "execution." + result["status"],
                identity,
                {"exit_code": result["exit_code"], "seconds": result["seconds"]},
            )
        return result

    def _execute(self, plan, root, run_id, timeout, max_output, memory_mb):
        env = {
            k: os.environ[k]
            for k in ["PATH", "LANG", "LC_ALL", "SYSTEMROOT", "TMPDIR"]
            if k in os.environ
        }
        env.update(
            CI="1",
            PYTHONDONTWRITEBYTECODE="1",
            PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
            DO_NOT_TRACK="1",
            CARGO_NET_OFFLINE="true",
            npm_config_offline="true",
            npm_config_ignore_scripts="true",
            NODE_OPTIONS="--max-old-space-size=512",
        )
        limits = {
            "cpu_seconds": math.ceil(timeout) + 1,
            "memory_mb": memory_mb,
            "file_bytes": 512 * 1024 * 1024,
        }
        argv = [
            sys.executable,
            "-I",
            "-m",
            "assistant_workbench.execution_packs",
            "_limited_exec",
            canonical(limits),
            *plan["command"],
        ]
        started = time.monotonic()
        output = {"stdout": bytearray(), "stderr": bytearray()}
        received = 0
        reason = None
        process = None
        try:
            process = subprocess.Popen(
                argv,
                cwd=root,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, "stdout")
                selector.register(process.stderr, selectors.EVENT_READ, "stderr")
                while selector.get_map() or process.poll() is None:
                    if time.monotonic() - started > timeout:
                        reason = "timed_out"
                    if self._cancelled(run_id):
                        reason = "cancelled"
                    if reason:
                        _stop_group(process)
                        break
                    if process.poll() is not None:
                        _stop_group(process)
                    for key, _ in selector.select(timeout=0.025):
                        chunk = os.read(key.fileobj.fileno(), 8192)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        room = max(0, max_output - received)
                        output[key.data].extend(chunk[:room])
                        received += len(chunk)
                        if received > max_output:
                            reason = "output_limit"
                            _stop_group(process)
                            break
                if process.poll() is None:
                    process.wait(
                        timeout=max(0.01, timeout - (time.monotonic() - started))
                    )
            code = process.returncode
            status = reason or ("passed" if code == 0 else "failed")
        except subprocess.TimeoutExpired:
            if process:
                _stop_group(process)
            code, status = process.returncode if process else None, "timed_out"
        except OSError as exc:
            code, status = None, "blocked"
            output["stderr"].extend(str(exc).encode()[:max_output])
        finally:
            if process:
                _stop_group(process)
                process.stdout.close()
                process.stderr.close()
        return {
            "status": status,
            "exit_code": code,
            "seconds": round(time.monotonic() - started, 3),
            "stdout": safe_text(output["stdout"].decode(errors="replace")),
            "stderr": safe_text(output["stderr"].decode(errors="replace")),
            "captured_bytes": sum(len(v) for v in output.values()),
            "limits": {"wall_seconds": timeout, "output_bytes": max_output, **limits},
            "notice": "Only command execution is measured. Test counts are evidence from the selected test runner, not a semantic-quality judgment.",
        }

    def artifacts(self, workspace_id, paths, kind="files", platforms=None, claims=None):
        """Record real files and mechanical checks; no generated result is inferred."""
        workspace, root = self._workspace(workspace_id)
        if (
            kind not in {"files", "web", "mobile", "research"}
            or not isinstance(paths, list)
            or not 1 <= len(paths) <= 100
            or not all(isinstance(path, str) and path for path in paths)
        ):
            raise WorkbenchError(
                "Use a known artifact kind and 1–100 explicit file paths"
            )
        if len(paths) != len(set(paths)):
            raise WorkbenchError("Artifact paths must be distinct")
        files = []
        total = 0
        for name in paths:
            path = _file(root, name)
            total += path.stat().st_size
            if total > MAX_SOURCE_BYTES or _secret_name(path):
                raise WorkbenchError(
                    "Artifact bundle contains a secret filename or exceeds 100 MiB"
                )
            files.append(
                {
                    "path": str(path.relative_to(root)),
                    "bytes": path.stat().st_size,
                    "sha256": digest(path.read_bytes()),
                }
            )
        checks = []
        dependencies = []
        if kind == "web":
            pages = [f for f in files if f["path"].endswith((".html", ".htm"))]
            checks.append({"check": "html_entry", "passed": bool(pages)})
            for item in pages:
                parser = _WebPage()
                parser.feed(_file(root, item["path"]).read_text())
                checks.append(
                    {
                        "check": "document_structure",
                        "file": item["path"],
                        "passed": parser.has_title and parser.has_body,
                    }
                )
                for reference in parser.references:
                    uri = urlsplit(reference)
                    if uri.scheme or uri.netloc or not uri.path:
                        continue
                    try:
                        # Root-relative URLs resolve from the exported project root.
                        target = (
                            Path(unquote(uri.path.lstrip("/")))
                            if uri.path.startswith("/")
                            else Path(item["path"]).parent / unquote(uri.path)
                        )
                        target = Path(os.path.normpath(target))
                        dependency = _file(root, target)
                        total += dependency.stat().st_size
                        if total > MAX_SOURCE_BYTES or len(dependencies) >= 100:
                            raise WorkbenchError(
                                "Referenced assets exceed the artifact inspection bound"
                            )
                        dependencies.append(
                            {
                                "path": str(target),
                                "bytes": dependency.stat().st_size,
                                "sha256": digest(dependency.read_bytes()),
                            }
                        )
                        exists = True
                    except WorkbenchError:
                        exists = False
                    checks.append(
                        {
                            "check": "local_asset_exists",
                            "file": item["path"],
                            "reference": reference,
                            "passed": exists,
                        }
                    )
        elif kind == "mobile":
            if (
                not isinstance(platforms, list)
                or not platforms
                or not set(platforms) <= {"android", "ios"}
            ):
                raise WorkbenchError(
                    "Mobile artifacts require explicit android/ios target declarations"
                )
            names = [f["path"] for f in files]
            for platform in platforms:
                suffixes = (".apk", ".aab") if platform == "android" else (".ipa",)
                archives = [p for p in names if p.endswith(suffixes)]
                checks.append(
                    {
                        "check": "packaged_build_present",
                        "platform": platform,
                        "passed": bool(archives),
                    }
                )
                for name in archives:
                    valid = False
                    try:
                        with zipfile.ZipFile(_file(root, name)) as archive:
                            members = set(archive.namelist())
                            valid = (
                                "AndroidManifest.xml" in members
                                if name.endswith(".apk")
                                else (
                                    "base/manifest/AndroidManifest.xml" in members
                                    if name.endswith(".aab")
                                    else any(
                                        re.fullmatch(
                                            r"Payload/[^/]+\.app/Info\.plist", member
                                        )
                                        for member in members
                                    )
                                )
                            )
                    except zipfile.BadZipFile:
                        pass
                    checks.append(
                        {
                            "check": "mobile_archive_structure",
                            "file": name,
                            "passed": valid,
                            "notice": "Structure only; signature, installability and runtime remain unverified",
                        }
                    )
            checks.append(
                {
                    "check": "device_runtime",
                    "passed": None,
                    "status": "not_run",
                    "reason": "An installed app and device/emulator journey are required",
                }
            )
        elif kind == "research":
            if claims is not None and (
                not isinstance(claims, list)
                or len(claims) > 100
                or any(
                    not isinstance(claim, dict)
                    or not all(
                        isinstance(claim.get(k), str)
                        for k in ["source_id", "statement", "quote"]
                    )
                    for claim in claims
                )
            ):
                raise WorkbenchError(
                    "Research claims require source_id, statement and quote strings"
                )
            text = "\n".join(
                _file(root, f["path"]).read_text()
                for f in files
                if Path(f["path"]).suffix in {".md", ".txt", ".html"}
            )
            checks.append({"check": "readable_report", "passed": bool(text.strip())})
            citations = []
            for claim in claims or []:
                source = Context(self.store).source(
                    workspace["project_id"], claim["source_id"]
                )
                statement, quote = claim.get("statement", ""), claim.get("quote", "")
                passed = bool(
                    statement
                    and quote
                    and statement in text
                    and quote in source["text"]
                )
                citations.append(
                    {
                        "check": "source_quote_and_statement_present",
                        "source_id": source["id"],
                        "source_sha256": source["hash"],
                        "passed": passed,
                        "entailment": "requires independent review",
                    }
                )
            checks.extend(citations)
            checks.append(
                {"check": "grounded_citations_supplied", "passed": bool(citations)}
            )
        else:
            checks.append({"check": "files_present", "passed": True})
        identity = uid("artifact_")
        payload = {
            "id": identity,
            "workspace_id": workspace_id,
            "project_id": workspace["project_id"],
            "kind": kind,
            "files": files,
            "dependencies": dependencies,
            "checks": checks,
            "mechanical_status": (
                "failed"
                if any(c["passed"] is False for c in checks)
                else (
                    "incomplete"
                    if any(c["passed"] is None for c in checks)
                    else "passed"
                )
            ),
            "status": "needs_review",
            "semantic_acceptance": "not granted",
            "created": now(),
        }
        sha = digest(canonical(payload))
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO artifact_manifests VALUES(?,?,?,?,?)",
                (identity, workspace_id, canonical(payload), sha, now()),
            )
            self.store.event(
                db,
                "artifacts.recorded",
                identity,
                {"workspace": workspace_id, "kind": kind, "sha256": sha},
            )
        return {**payload, "sha256": sha}

    def verify_artifacts(self, manifest_id):
        record = self.store.one(
            "SELECT * FROM artifact_manifests WHERE id=?", (manifest_id,)
        )
        if digest(record["payload"]) != record["sha256"]:
            raise WorkbenchError("Artifact receipt changed")
        payload = json.loads(record["payload"])
        _, root = self._workspace(record["workspace_id"])
        for item in [*payload["files"], *payload.get("dependencies", [])]:
            path = _file(root, item["path"])
            if (
                path.stat().st_size != item["bytes"]
                or digest(path.read_bytes()) != item["sha256"]
            ):
                raise WorkbenchError(
                    "Artifact changed since inspection: " + item["path"]
                )
        for check in payload["checks"]:
            if check.get("source_id"):
                source = Context(self.store).source(
                    payload["project_id"], check["source_id"]
                )
                if source["hash"] != check["source_sha256"]:
                    raise WorkbenchError("Cited source changed since inspection")
        return {
            "id": manifest_id,
            "integrity": "passed",
            "mechanical_status": payload["mechanical_status"],
            "status": "needs_review",
            "semantic_acceptance": "not granted",
        }


class _WebPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.has_title = False
        self.has_body = False
        self.references = []

    def handle_starttag(self, tag, attrs):
        self.has_title = self.has_title or tag == "title"
        self.has_body = self.has_body or tag == "body"
        for name, value in attrs:
            if (
                value
                and name in {"src", "href"}
                and tag
                in {"script", "link", "img", "source", "video", "audio", "iframe"}
            ):
                self.references.append(value)


def _test_count(text, framework):
    if framework == "pytest":
        matches = re.findall(r"(\d+) (?:passed|failed|error)s?\b", text)
    elif framework == "node":
        matches = re.findall(r"^# tests (\d+)\s*$", text, re.M)
    elif framework == "cargo":
        matches = re.findall(r"test result: .*? (\d+) passed", text)
    elif framework == "javascript":
        matches = re.findall(
            r"(?:^# tests |^ℹ tests |Tests:\s*|Tests\s+)(\d+)", text, re.M
        )
    else:
        return None
    return sum(map(int, matches)) if matches else None


def _limited_exec():
    """Apply resource limits in a fresh process, avoiding threaded preexec hooks."""
    import resource

    limits = json.loads(sys.argv[2])
    resource.setrlimit(
        resource.RLIMIT_CPU, (limits["cpu_seconds"], limits["cpu_seconds"] + 1)
    )
    resource.setrlimit(
        resource.RLIMIT_FSIZE, (limits["file_bytes"], limits["file_bytes"])
    )
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    if limits["memory_mb"] is not None:
        maximum = limits["memory_mb"] * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (maximum, maximum))
    os.execvpe(sys.argv[3], sys.argv[3:], os.environ)


if __name__ == "__main__":
    if len(sys.argv) > 3 and sys.argv[1] == "_limited_exec":
        _limited_exec()
    else:
        raise SystemExit("Use ExecutionPacks through the Workbench operator interface")

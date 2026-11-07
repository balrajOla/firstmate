from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from .config import config
from .context import Context
from .paperclip import Paperclip
from .util import WorkbenchError, atomic, canonical, digest, now, uid


def run_process(
    argv,
    cwd=None,
    env=None,
    timeout=120,
    input_text=None,
    allowed_codes=(0,),
    max_output=8 * 1024 * 1024,
):
    if not argv or not all(isinstance(x, str) for x in argv):
        raise WorkbenchError("Command must be a nonempty argument array")
    started = time.monotonic()
    with (
        tempfile.TemporaryFile() as out,
        tempfile.TemporaryFile() as err,
        tempfile.TemporaryFile() as inp,
    ):
        if input_text is not None:
            inp.write(input_text.encode())
            inp.seek(0)
        try:
            p = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdin=inp,
                stdout=out,
                stderr=err,
                start_new_session=True,
            )
        except OSError as exc:
            raise WorkbenchError("Executable could not start: " + argv[0]) from exc
        reason = None
        while p.poll() is None:
            if time.monotonic() - started > timeout:
                reason = "Process timed out"
            if out.tell() + err.tell() > max_output:
                reason = "Process output exceeded limit"
            if reason:
                try:
                    os.killpg(p.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    p.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL)
                    p.wait()
                break
            time.sleep(0.025)
        if out.tell() + err.tell() > max_output:
            reason = "Process output exceeded limit"
        out.seek(0)
        err.seek(0)
        stdout = out.read(max_output).decode(errors="replace")
        stderr = err.read(max_output).decode(errors="replace")
    if reason:
        raise WorkbenchError(reason)
    result = {
        "exit_code": p.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "seconds": round(time.monotonic() - started, 3),
    }
    if p.returncode not in allowed_codes:
        raise WorkbenchError(
            "Command failed with exit " + str(p.returncode) + ": " + argv[0]
        )
    return result


class Integrations:
    def __init__(self, store):
        self.store = store
        self.settings = config(store.home)

    def spec(self, name):
        item = self.settings.get("integrations", {}).get(name)
        if not item or not item.get("enabled"):
            raise WorkbenchError(name + " is not configured and enabled")
        return item

    def command(self, name):
        item = self.spec(name)
        cmd = item.get("command")
        if (
            not isinstance(cmd, list)
            or not cmd
            or not all(isinstance(x, str) for x in cmd)
        ):
            raise WorkbenchError(name + " requires an explicit command array")
        return cmd, item

    def _env(self, item):
        env = os.environ.copy()
        if item.get("auth", "local") != "api":
            for key in list(env):
                if key.endswith(("_API_KEY", "_ACCESS_TOKEN", "_AUTH_TOKEN")):
                    env.pop(key, None)
        for key, value in item.get("env", {}).items():
            if not isinstance(value, str):
                raise WorkbenchError("Integration environment values must be strings")
            env[key] = value
        # Set before subprocess startup: ORT's API-only opt-out is too late to
        # prevent its initialization event. See Microsoft's ONNX privacy docs.
        env.update(
            PROMPTFOO_DISABLE_UPDATE="true",
            PROMPTFOO_DISABLE_TELEMETRY="true",
            PROMPTFOO_DISABLE_REMOTE_GENERATION="true",
            DO_NOT_TRACK="1",
            ORT_DISABLE_TELEMETRY="1",
            OTEL_SDK_DISABLED="true",
            HF_HUB_DISABLE_TELEMETRY="1",
            ANONYMIZED_TELEMETRY="false",
        )
        return env

    def basic_memory(self, project, action, **args):
        cmd, item = self.command("basic-memory")
        pid = self.store.project(project)["id"]
        item = {
            **item,
            "env": {
                "BASIC_MEMORY_CONFIG_DIR": str(
                    self.store.home / "integrations/basic-memory/config"
                ),
                "BASIC_MEMORY_HOME": str(
                    self.store.home / "integrations/basic-memory/home"
                ),
                "BASIC_MEMORY_FORCE_LOCAL": "true",
                "BASIC_MEMORY_DEFAULT_SEARCH_TYPE": "text",
                "BASIC_MEMORY_SEMANTIC_EMBEDDING_THREADS": "2",
                **item.get("env", {}),
            },
        }
        if action == "register":
            path = self.store.home / "knowledge" / pid
            path.mkdir(parents=True, exist_ok=True)
            argv = [*cmd, "project", "add", pid, str(path), "--local"]
        elif action == "search":
            argv = [
                *cmd,
                "tool",
                "search",
                args["query"],
                "--mode",
                "text",
                "--project",
                pid,
                "--json",
            ]
        elif action == "read":
            argv = [
                *cmd,
                "tool",
                "read",
                args["identifier"],
                "--project",
                pid,
                "--json",
            ]
        elif action == "write":
            identifier = args["identifier"]
            if Path(identifier).is_absolute() or ".." in Path(identifier).parts:
                raise WorkbenchError("Note path must remain within its project")
            argv = [
                *cmd,
                "tool",
                "write",
                "--identifier",
                identifier,
                "--content",
                args["content"],
                "--project",
                pid,
                "--json",
            ]
            if args.get("checksum"):
                argv += ["--overwrite", "--expected-checksum", args["checksum"]]
        else:
            raise WorkbenchError("Unsupported Basic Memory operation")
        return run_process(argv, env=self._env(item), timeout=120)

    def qmd(self, project, query, mode="exact"):
        cmd, item = self.command("qmd")
        pid = self.store.project(project)["id"]
        collection = item.get("collections", {}).get(pid)
        if not collection:
            raise WorkbenchError("No QMD collection bound to this project")
        if mode not in {"exact", "semantic"}:
            raise WorkbenchError("QMD mode must be exact or semantic")
        return run_process(
            [
                *cmd,
                "search" if mode == "exact" else "query",
                query,
                "-c",
                collection,
                "--json",
            ],
            env=self._env(item),
            timeout=60 if mode == "exact" else 180,
        )

    def http(self, name, path, payload=None):
        item = self.spec(name)
        client = Paperclip(
            {
                "url": item["url"],
                "token_env": item.get(
                    "token_env", "AWB_" + name.upper().replace("-", "_") + "_TOKEN"
                ),
            },
            timeout=min(
                item.get("timeout", 30), self.settings["limits"]["max_seconds"]
            ),
        )
        # Reuse the guarded transport without requiring a Paperclip /api prefix.
        api_prefix = item.get("api_prefix", "")
        original = client.base
        if api_prefix == "/api":
            return client.request(
                "POST" if payload is not None else "GET", path, payload
            )
        # Do not rely on server path normalization: explicit request with same origin checks.
        headers = {"Content-Type": "application/json"}
        if client.token:
            headers["Authorization"] = "Bearer " + client.token
        req = urllib.request.Request(
            original + path,
            data=None if payload is None else canonical(payload).encode(),
            headers=headers,
            method="GET" if payload is None else "POST",
        )

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None

        try:
            with urllib.request.build_opener(NoRedirect()).open(
                req, timeout=client.timeout
            ) as res:
                data = res.read(8 * 1024 * 1024 + 1)
                if len(data) > 8 * 1024 * 1024:
                    raise WorkbenchError("Integration response exceeds limit")
                return json.loads(data)
        except (OSError, ValueError) as exc:
            raise WorkbenchError(
                name + " request failed; check service configuration"
            ) from exc

    def history(self, project, action, **args):
        p = self.store.project(project)
        if not p["repository"]:
            raise WorkbenchError("LCM scope requires a project directory")
        if action == "search":
            return self.http(
                "lcm",
                "/search",
                {
                    "cwd": p["repository"],
                    "query": args["query"],
                    "limit": 8,
                    "layers": ["episodic"],
                },
            )
        if action == "capture":
            return self.http(
                "lcm",
                "/ingest",
                {
                    "cwd": p["repository"],
                    "session_id": args["session_id"],
                    "messages": args["messages"],
                    "client": "claude",
                },
            )
        if action == "expand":
            return self.http("lcm", "/expand", {"cwd": p["repository"], **args})
        raise WorkbenchError("Unsupported LCM operation")

    def docling(self, project, path):
        cmd, item = self.command("docling")
        path = Path(path).resolve(strict=True)
        script = "from docling.document_converter import DocumentConverter;import sys;print(DocumentConverter().convert(sys.argv[1]).document.export_to_markdown())"
        result = run_process(
            [*cmd, "-c", script, str(path)],
            env=self._env(item),
            timeout=min(600, self.settings["limits"]["max_seconds"]),
        )
        return Context(self.store).ingest(project, path, text=result["stdout"])

    def notebook(self, project, action, **args):
        item = self.spec("open-notebook")
        pid = self.store.project(project)["id"]
        notebook_id = item.get("notebooks", {}).get(pid)
        if not notebook_id:
            raise WorkbenchError("Bind a notebook explicitly to this project")
        if action == "sources":
            return self.http(
                "open-notebook",
                "/sources?notebook_id=" + urllib.parse.quote(notebook_id, safe=""),
            )
        if action == "ask":
            if item.get("auth") not in {"api", "local"}:
                raise WorkbenchError(
                    "Open Notebook has no qualified subscription-CLI inference bridge"
                )
            return self.http(
                "open-notebook",
                "/search/ask/simple",
                {
                    "question": args["query"],
                    "notebook_id": notebook_id,
                    **item.get("ask_models", {}),
                },
            )
        raise WorkbenchError("Unsupported notebook operation")

    def researcher(self, project, query):
        cmd, item = self.command("gpt-researcher")
        if item.get("auth") not in {"api", "local"}:
            raise WorkbenchError(
                "GPT Researcher requires explicit API/local inference; subscription bridge is unqualified"
            )
        script = (
            "import asyncio,json,sys\nfrom gpt_researcher import GPTResearcher\n"
            "async def main():\n r=GPTResearcher(query=sys.argv[1],report_type='research_report',config_path=sys.argv[2] if len(sys.argv)>2 else None)\n await r.conduct_research()\n print(json.dumps({'report':await r.write_report(),'status':'needs_review'}))\nasyncio.run(main())"
        )
        argv = [*cmd, "-c", script, query]
        if item.get("config_path"):
            argv.append(item["config_path"])
        result = run_process(
            argv,
            env=self._env(item),
            timeout=min(600, self.settings["limits"]["max_seconds"]),
        )
        return {
            "project": self.store.project(project)["id"],
            "status": "needs_review",
            "result": result,
            "notice": "Research worker completion does not certify its claims.",
        }

    def writing(self, name, path):
        cmd, item = self.command(name)
        if name == "vale":
            return run_process(
                [*cmd, "--output=JSON", str(Path(path).resolve(strict=True))],
                env=self._env(item),
                allowed_codes=(0, 1),
            )
        if name == "promptfoo":
            return run_process(
                [
                    *cmd,
                    "eval",
                    "-c",
                    str(Path(path).resolve(strict=True)),
                    "--no-cache",
                    "--no-progress-bar",
                ],
                env=self._env(item),
                allowed_codes=(0, 100),
                timeout=300,
            )
        raise WorkbenchError("Unknown writing/evaluation tool")

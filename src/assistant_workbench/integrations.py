from __future__ import annotations

import json
import fcntl
import os
import re
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
        allowed = {
            "register": set(),
            "search": {"query"},
            "read": {"identifier"},
            "write": {"identifier", "content", "checksum"},
        }
        if (
            not isinstance(action, str)
            or action not in allowed
            or set(args) - allowed[action]
        ):
            raise WorkbenchError("Unsupported Basic Memory operation or arguments")
        if action in {"register", "write"} and item.get("allow_write") is not True:
            raise WorkbenchError("Basic Memory writes require explicit allow_write")
        root = self.store.home / "knowledge" / pid
        for path in (root.parent, root):
            if path.is_symlink():
                raise WorkbenchError("Basic Memory project roots cannot be symlinks")
        if action in {"read", "write"}:
            identifier = self._note_path(args.get("identifier"))
            target = root / identifier
            for part in [target, *target.parents]:
                if part == root.parent:
                    break
                if part.is_symlink():
                    raise WorkbenchError(
                        "Basic Memory note paths cannot follow symlinks"
                    )
            if action == "read" and not target.is_file():
                raise WorkbenchError(
                    "The requested note is not a local file in this project"
                )
        else:
            identifier = None
        # Required isolation values override optional tuning, never the reverse.
        item = {
            **item,
            "env": {
                **item.get("env", {}),
                "BASIC_MEMORY_CONFIG_DIR": str(
                    self.store.home / "integrations/basic-memory/config"
                ),
                "BASIC_MEMORY_HOME": str(
                    self.store.home / "integrations/basic-memory/home"
                ),
                "BASIC_MEMORY_FORCE_LOCAL": "true",
                "BASIC_MEMORY_WRITE_NOTE_OVERWRITE_DEFAULT": "false",
                "BASIC_MEMORY_DEFAULT_SEARCH_TYPE": "text",
                "BASIC_MEMORY_SEMANTIC_EMBEDDING_THREADS": "2",
            },
        }
        input_text = None
        if action == "register":
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            argv = [*cmd, "project", "add", pid, str(root), "--local"]
        elif action == "search":
            query = self._memory_query(args.get("query"))
            argv = [
                *cmd,
                "tool",
                "search",
                query,
                "--mode",
                "text",
                "--project",
                pid,
                "--local",
                "--json",
            ]
        elif action == "read":
            argv = [
                *cmd,
                "tool",
                "read",
                identifier,
                "--project",
                pid,
                "--local",
                "--json",
            ]
        elif action == "write":
            input_text = args.get("content")
            if (
                not isinstance(input_text, str)
                or len(input_text.encode()) > 1024 * 1024
            ):
                raise WorkbenchError(
                    "Basic Memory content must be text of at most 1 MiB"
                )
            checksum = args.get("checksum")
            if checksum is not None and (
                not isinstance(checksum, str)
                or not re.fullmatch(r"[0-9a-f]{64}", checksum)
            ):
                raise WorkbenchError(
                    "Use the full checksum returned by the previous JSON read"
                )
            argv = [
                *cmd,
                "tool",
                "write",
                "--identifier",
                identifier,
                "--project",
                pid,
                "--local",
                "--json",
            ]
            if checksum:
                argv += ["--overwrite", "--expected-checksum", checksum]
        lock_path = (
            self.store.home
            / "integrations/basic-memory"
            / ("register.lock" if action == "register" else pid + ".lock")
        )
        lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if lock_path.is_symlink():
            raise WorkbenchError("Basic Memory lock cannot be a symlink")
        with lock_path.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if action == "write" and target.exists() and not args.get("checksum"):
                raise WorkbenchError(
                    "Read the existing note and supply its checksum before replacing it"
                )
            result = run_process(
                argv,
                env=self._env(item),
                timeout=120,
                input_text=input_text,
                allowed_codes=(0, 1),
            )
        if result["exit_code"] != 0:
            raise WorkbenchError(
                "Basic Memory refused the operation; re-read after a revision conflict and do not overwrite blindly"
            )
        if action == "register":
            return {**result, "project_id": pid, "path": str(root), "registered": True}
        data = self._memory_json(result, "Basic Memory")
        if (
            not isinstance(data, dict)
            or data.get("error")
            or data.get("isError")
            or data.get("action") in {"conflict", "error"}
        ):
            raise WorkbenchError(
                "Basic Memory returned a refused or malformed operation"
            )
        if action in {"read", "write"}:
            if not isinstance(data.get("checksum"), str) or not re.fullmatch(
                r"[0-9a-f]{64}", data["checksum"]
            ):
                raise WorkbenchError(
                    "Basic Memory returned no usable revision checksum"
                )
            if "file_path" in data and self._note_path(data["file_path"]) != identifier:
                raise WorkbenchError(
                    "Basic Memory returned a note outside the requested path"
                )
            if not target.is_file() or digest(target.read_bytes()) != data["checksum"]:
                raise WorkbenchError(
                    "Basic Memory revision does not match the project's local note"
                )
        elif action == "search":
            if not isinstance(data.get("results"), list):
                raise WorkbenchError("Basic Memory search returned no result list")
            for hit in data["results"]:
                if (
                    not isinstance(hit, dict)
                    or not isinstance(hit.get("permalink"), str)
                    or not hit["permalink"].startswith(pid.replace("_", "-") + "/")
                ):
                    raise WorkbenchError(
                        "Basic Memory search returned another project's note"
                    )
                note = root / self._note_path(hit.get("file_path"))
                if (
                    not note.is_file()
                    or note.is_symlink()
                    or not note.resolve().is_relative_to(root.resolve())
                ):
                    raise WorkbenchError(
                        "Basic Memory search returned an unavailable project note"
                    )
        return {
            **result,
            "data": data,
            "project_id": pid,
            "derived": True,
            "review_required": True,
        }

    @staticmethod
    def _note_path(value):
        if (
            not isinstance(value, str)
            or not value.endswith(".md")
            or value.startswith(("/", "-"))
            or any(c in value for c in "\\:%\x00\r\n")
            or any(p in {"", ".", ".."} for p in value.split("/"))
        ):
            raise WorkbenchError(
                "Use an explicit relative Markdown path within this project"
            )
        return value

    @staticmethod
    def _memory_query(value):
        if (
            not isinstance(value, str)
            or not value.strip()
            or value.startswith("-")
            or len(value) > 8192
            or "\x00" in value
        ):
            raise WorkbenchError(
                "Search requires 1–8192 characters and cannot start with a command option"
            )
        return value

    @staticmethod
    def _memory_json(result, name):
        try:
            value = json.loads(result["stdout"])
            # Some Basic Memory versions serialize the tool's JSON string once more.
            if isinstance(value, str):
                value = json.loads(value)
        except (ValueError, KeyError, TypeError) as exc:
            raise WorkbenchError(
                name + " returned malformed JSON; the operation is not confirmed"
            ) from exc
        return value

    def qmd(self, project, query, mode="exact", limit=8):
        cmd, item = self.command("qmd")
        pid = self.store.project(project)["id"]
        mappings = item.get("collections", {})
        collection = mappings.get(pid) if isinstance(mappings, dict) else None
        if not isinstance(collection, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", collection
        ):
            raise WorkbenchError("No QMD collection bound to this project")
        if any(
            other != pid
            and isinstance(name, str)
            and name.casefold() == collection.casefold()
            for other, name in mappings.items()
        ):
            raise WorkbenchError("QMD project collections must be distinct")
        if not isinstance(mode, str) or mode not in {"exact", "semantic", "hybrid"}:
            raise WorkbenchError("QMD mode must be exact, semantic or hybrid")
        query = self._memory_query(query)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise WorkbenchError("QMD limit must be 1–100")
        if mode != "exact" and item.get("allow_model_inference") is not True:
            raise WorkbenchError(
                "Enable QMD allow_model_inference after configuring local models and embeddings"
            )
        result = run_process(
            [
                *cmd,
                {"exact": "search", "semantic": "vsearch", "hybrid": "query"}[mode],
                query,
                "-c",
                collection,
                "-n",
                str(limit),
                "--json",
            ],
            env=self._env(item),
            timeout=60 if mode == "exact" else 180,
        )
        data = self._memory_json(result, "QMD")
        if not isinstance(data, list):
            raise WorkbenchError("QMD returned no result list")
        hits = []
        for hit in data:
            if not isinstance(hit, dict) or not isinstance(hit.get("file"), str):
                raise WorkbenchError("QMD returned a malformed result")
            uri = urllib.parse.urlsplit(hit["file"])
            path = urllib.parse.unquote(uri.path).lstrip("/")
            if (
                uri.scheme != "qmd"
                or uri.netloc != collection
                or uri.query
                or uri.fragment
                or any(p in {"", ".", ".."} for p in path.split("/"))
                or "\\" in path
            ):
                raise WorkbenchError(
                    "QMD returned a result outside the bound collection"
                )
            hits.append(
                {**hit, "derived": False, "review_required": True, "project_id": pid}
            )
        return {
            **result,
            "results": hits[:limit],
            "project_id": pid,
            "mode": mode,
            "retrieval": {"exact": "lexical", "semantic": "vector", "hybrid": "hybrid"}[
                mode
            ],
            "notice": "A successful query is not a relevance or factual-accuracy evaluation. Open the source before citing.",
        }

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
        item = self.spec("lcm")
        bindings = item.get("projects", {})
        binding = bindings.get(p["id"]) if isinstance(bindings, dict) else None
        if not isinstance(binding, dict) or not isinstance(binding.get("cwd"), str):
            raise WorkbenchError("LCM requires an explicit project-to-cwd binding")
        cwd = Path(binding["cwd"])
        if (
            not cwd.is_absolute()
            or not cwd.is_dir()
            or not p["repository"]
            or cwd.resolve() != Path(p["repository"]).resolve()
        ):
            raise WorkbenchError(
                "LCM cwd must match this project's existing repository directory"
            )
        cwd = cwd.resolve()
        for other, mapped in bindings.items():
            if other == p["id"]:
                continue
            if not isinstance(mapped, dict) or not isinstance(mapped.get("cwd"), str):
                raise WorkbenchError(
                    "LCM project bindings must contain explicit directories"
                )
            target = Path(mapped["cwd"]).resolve()
            if cwd == target or cwd in target.parents or target in cwd.parents:
                raise WorkbenchError("LCM project scopes must be disjoint")
        allowed = {
            "search": {"query", "limit"},
            "capture": {"session_id", "messages", "client"},
            "expand": {"nodeId", "depth"},
            "summarize": {"session_id", "client"},
            "install_hooks": {"harness", "expected_sha256"},
        }
        if (
            not isinstance(action, str)
            or action not in allowed
            or set(args) - allowed[action]
        ):
            raise WorkbenchError("Unsupported LCM operation or arguments")
        if (
            action in {"capture", "summarize", "install_hooks"}
            and item.get("allow_write") is not True
        ):
            raise WorkbenchError("LCM writes require explicit allow_write")
        if action == "install_hooks":
            return self._install_lcm_hooks(p["id"], cwd, item, **args)
        from .context_backends import BackendHTTP

        http = BackendHTTP("lcm", item)
        payload = {"cwd": str(cwd)}
        if action == "search":
            limit = args.get("limit", 8)
            if type(limit) is not int or not 1 <= limit <= 100:
                raise WorkbenchError("LCM result limit must be 1–100")
            result = http.request(
                "POST",
                "/search",
                {
                    **payload,
                    "query": self._memory_query(args.get("query")),
                    "limit": limit,
                    "layers": ["episodic"],
                    "backend": "native",
                },
            )
            self._lcm_response(result)
            hits = result.get("episodic")
            if not isinstance(hits, list):
                raise WorkbenchError("LCM returned no episodic result list")
            retained = []
            for hit in hits:
                if not isinstance(hit, dict) or not isinstance(
                    hit.get("project"), dict
                ):
                    raise WorkbenchError("LCM result has no project provenance")
                if hit["project"].get("id") == digest(str(cwd)) and hit["project"].get(
                    "cwd"
                ) == str(cwd):
                    retained.append(
                        {**hit, "derived": "summaryId" in hit, "review_required": True}
                    )
            return {
                "project_id": p["id"],
                "episodic": retained[:limit],
                "promoted": [],
                "omitted": len(hits) - len(retained[:limit]),
                "notice": "Recall is scoped to this checkout; inspect source messages before treating a summary as fact.",
            }
        if action == "expand":
            node, depth = args.get("nodeId"), args.get("depth", 1)
            if (
                not isinstance(node, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", node)
                or type(depth) is not int
                or not 1 <= depth <= 8
            ):
                raise WorkbenchError("LCM expansion needs a safe node ID and depth 1–8")
            result = http.request(
                "POST",
                "/expand",
                {
                    **payload,
                    "nodeId": node,
                    "depth": depth,
                    "projectId": digest(str(cwd)),
                },
            )
            self._lcm_response(result)
            if (
                not isinstance(result.get("expansions"), list)
                or type(result.get("truncated")) is not bool
            ):
                raise WorkbenchError("LCM expansion response is malformed")
            return {
                **result,
                "project_id": p["id"],
                "derived": True,
                "review_required": True,
            }
        session_id, client = args.get("session_id"), args.get("client")
        if (
            not isinstance(session_id, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", session_id)
            or not isinstance(client, str)
            or client not in {"claude", "codex", "omp"}
        ):
            raise WorkbenchError(
                "LCM requires a safe session ID and an explicit claude, codex or omp client"
            )
        payload.update(session_id=session_id, client=client)
        if action == "capture":
            messages = args.get("messages")
            if not isinstance(messages, list) or not 1 <= len(messages) <= 1000:
                raise WorkbenchError("LCM capture requires 1–1000 explicit messages")
            for message in messages:
                if (
                    not isinstance(message, dict)
                    or set(message) - {"role", "content", "tokenCount", "timestamp"}
                    or not isinstance(message.get("role"), str)
                    or message.get("role")
                    not in {"user", "assistant", "system", "tool"}
                    or not isinstance(message.get("content"), str)
                    or type(message.get("tokenCount")) is not int
                    or message["tokenCount"] < 0
                    or (
                        "timestamp" in message
                        and not isinstance(message["timestamp"], str)
                    )
                ):
                    raise WorkbenchError(
                        "LCM messages require role, text content and a nonnegative tokenCount"
                    )
            payload["messages"] = messages
            encoded = canonical(payload)
            if len(encoded.encode()) > 1024 * 1024:
                raise WorkbenchError("LCM capture exceeds 1 MiB; use bounded batches")
            path = (
                self.store.home
                / "integrations/lcm/captures"
                / p["id"]
                / (digest(encoded) + ".json")
            )
            atomic(path, encoded)
            original = Context(self.store).ingest(p["id"], str(path))
            result = http.request("POST", "/ingest", payload)
            self._lcm_response(result)
            if (
                type(result.get("ingested")) is not int
                or result["ingested"] < 0
                or type(result.get("totalTokens")) is not int
            ):
                raise WorkbenchError("LCM did not confirm a valid capture result")
            return {
                **result,
                "project_id": p["id"],
                "source_id": original["id"],
                "original_sha256": original["hash"],
                "status": "excluded" if result.get("excluded") else "captured",
                "notice": "Capture acknowledgement is separate from summarization and hook activation.",
            }
        provider = item.get("summary_provider")
        if not isinstance(provider, str) or provider not in {
            "disabled",
            "claude-process",
            "codex-process",
            "copilot-process",
            "omp-process",
            "anthropic",
            "openai",
            "session",
            "session-pool",
        }:
            raise WorkbenchError(
                "Declare an explicit LCM summary_provider; auto fallback is not an integration contract"
            )
        subscription_providers = {
            "claude-process",
            "codex-process",
            "copilot-process",
            "omp-process",
            "session",
            "session-pool",
        }
        if provider in subscription_providers and item.get("auth") != "subscription":
            raise WorkbenchError(
                "LCM process/session summarization requires an explicit subscription profile"
            )
        if provider in {"anthropic", "openai"} and item.get("auth") not in {
            "api",
            "local",
        }:
            raise WorkbenchError(
                "LCM API-compatible summarization requires an API or local profile"
            )
        result = http.request("POST", "/compact", {**payload, "skip_ingest": True})
        self._lcm_response(result)
        outcome = result.get("replayOutcome")
        if outcome not in {
            "compacted",
            "no_work",
            "disabled",
            "skipped",
        } or not isinstance(result.get("summary"), str):
            raise WorkbenchError("LCM returned a malformed compaction result")
        if result.get("providerId") not in {None, provider}:
            raise WorkbenchError(
                "LCM used a different summary provider; review daemon configuration before retrying"
            )
        status = "needs_review" if outcome == "compacted" else "not_generated"
        usage = result.get("llmUsage") or {}
        if not isinstance(usage, dict):
            raise WorkbenchError("LCM returned malformed model usage")
        calls = usage.get("calls", 0)
        return {
            **result,
            "project_id": p["id"],
            "status": status,
            "derived": True,
            "review_required": True,
            "model_call_reported": type(calls) is int and calls > 0,
            "notice": "Only the daemon's reported result is recorded; provider availability and summary quality need separate qualification.",
        }

    @staticmethod
    def _lcm_response(result):
        if (
            not isinstance(result, dict)
            or result.get("error")
            or result.get("errors")
            or result.get("blocked")
            or result.get("incomplete")
        ):
            raise WorkbenchError(
                "LCM refused or incompletely processed the request; inspect its scoped diagnostics"
            )

    def _install_lcm_hooks(self, pid, cwd, item, harness=None, expected_sha256=None):
        if harness != "codex" or item.get("allow_hook_install") is not True:
            raise WorkbenchError(
                "Only explicit project-local Codex hook installation is supported here"
            )
        cmd, _ = self.command("lcm")
        directory, target = cwd / ".codex", cwd / ".codex/hooks.json"
        if directory.is_symlink() or target.is_symlink():
            raise WorkbenchError("Hook configuration cannot be a symlink")
        lock_path = self.store.home / "integrations/lcm" / (pid + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with lock_path.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            previous = target.read_bytes() if target.exists() else None
            previous_hash = digest(previous) if previous is not None else None
            if previous_hash != expected_sha256:
                raise WorkbenchError(
                    "Hook configuration changed; provide its current SHA-256 before merging"
                )
            with tempfile.TemporaryDirectory(prefix="awb-lcm-hooks-") as temp:
                stage = Path(temp)
                (stage / ".codex").mkdir()
                if previous is not None:
                    (stage / ".codex/hooks.json").write_bytes(previous)
                result = run_process(
                    [*cmd, "connectors", "install", "codex", "--type", "hooks"],
                    cwd=stage,
                    env=self._env(item),
                    timeout=60,
                )
                generated = stage / ".codex/hooks.json"
                if not generated.is_file() or generated.is_symlink():
                    raise WorkbenchError(
                        "LCM did not generate the expected hook configuration"
                    )
                data = generated.read_bytes()
                try:
                    hooks = json.loads(data)["hooks"]
                except (ValueError, KeyError, TypeError) as exc:
                    raise WorkbenchError("LCM generated malformed hooks") from exc
                if (
                    not isinstance(hooks, dict)
                    or not {"SessionStart", "Stop", "PreCompact"} <= hooks.keys()
                ):
                    raise WorkbenchError("LCM generated incomplete Codex capture hooks")
                current = target.read_bytes() if target.exists() else None
                if directory.is_symlink() or target.is_symlink() or current != previous:
                    raise WorkbenchError(
                        "Hook configuration changed during preparation; nothing was installed"
                    )
                if previous is not None:
                    atomic(
                        lock_path.parent / (pid + "-" + previous_hash + ".json"),
                        previous,
                    )
                atomic(target, data)
        return {
            "project_id": pid,
            "harness": harness,
            "path": str(target),
            "sha256": digest(data),
            "previous_sha256": previous_hash,
            "installed": True,
            "activation": "unknown",
            "notice": "Restart Codex and review/trust these hooks in /hooks. Run the harness with the same LCM_HOME as the daemon. A file installation is not a live capture or summary test.",
        }

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

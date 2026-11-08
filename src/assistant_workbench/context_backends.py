"""Explicit, scoped OpenViking and Hindsight clients over the original archive.

These adapters never qualify a model, read harness credentials, or replace the
original source archive. See docs/CONTEXT_BACKENDS.md for contracts and limits.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from .config import config
from .context import Context
from .util import WorkbenchError, canonical, digest, now


class BackendError(WorkbenchError):
    def __init__(self, message, uncertain=False, status_code=None):
        super().__init__(message)
        self.uncertain = uncertain
        self.status_code = status_code


class BackendHTTP:
    """One configured origin; no redirects or environment-derived destinations."""

    def __init__(self, backend, settings):
        self.backend = backend
        self.base = str(settings.get("url", "")).rstrip("/")
        try:
            parsed = urllib.parse.urlsplit(self.base)
            port = parsed.port
        except ValueError as exc:
            raise WorkbenchError("Invalid backend URL or port") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
            or any(c.isspace() for c in self.base)
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise WorkbenchError(
                "Backend URL must be an HTTP(S) origin without credentials or a path"
            )
        self.local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme == "http" and not self.local:
            raise WorkbenchError("Remote context backends require HTTPS")
        if settings.get("auth") not in {"local", "subscription", "api"}:
            raise WorkbenchError(
                "Declare the backend's local, subscription or API inference profile"
            )
        env_name = settings.get("token_env")
        if env_name is not None and (
            not isinstance(env_name, str)
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env_name)
        ):
            raise WorkbenchError("Backend token_env must name an environment variable")
        self.token = os.environ.get(env_name) if env_name else None
        if (env_name or not self.local) and not self.token:
            raise WorkbenchError("The configured backend service token is unavailable")
        if self.token and any(c in self.token for c in "\r\n"):
            raise WorkbenchError(
                "Backend service token contains an invalid header character"
            )
        self.timeout = settings.get("timeout_seconds", 30)
        if type(self.timeout) not in {int, float} or not 1 <= self.timeout <= 120:
            raise WorkbenchError("Backend timeout must be between 1 and 120 seconds")

    def request(self, method, path, payload=None, query=None):
        if (
            not path.startswith("/")
            or path.startswith("//")
            or "?" in path
            or "#" in path
        ):
            raise WorkbenchError("Invalid backend request path")
        url = self.base + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {"Accept": "application/json"}
        if self.token:
            headers[
                "X-API-Key" if self.backend == "openviking" else "Authorization"
            ] = (self.token if self.backend == "openviking" else "Bearer " + self.token)
        data = None if payload is None else canonical(payload).encode()
        if data is not None:
            headers["Content-Type"] = "application/json"
            if len(data) > 2 * 1024 * 1024:
                raise WorkbenchError("Backend request exceeds the 2 MiB limit")
        request = urllib.request.Request(url, data=data, headers=headers, method=method)

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        try:
            with urllib.request.build_opener(NoRedirect()).open(
                request, timeout=self.timeout
            ) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise BackendError(
                    "Context backend response exceeded 4 MiB", method != "GET"
                )
            result = json.loads(raw) if raw else {}
            if not isinstance(result, dict):
                raise ValueError("Expected a JSON object")
            return result
        except urllib.error.HTTPError as exc:
            raise BackendError(
                f"{self.backend} returned HTTP {exc.code}",
                method != "GET" and exc.code >= 500,
                exc.code,
            ) from exc
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise BackendError(
                f"{self.backend} did not return a confirmed response", method != "GET"
            ) from exc


class ContextBackends:
    NAMES = {"hindsight", "openviking"}

    def __init__(self, store):
        self.store = store
        self.context = Context(store)
        with store.transaction() as db:
            # No source FK: a retained deletion receipt must survive local purge.
            db.execute(
                """CREATE TABLE IF NOT EXISTS external_context(
                backend TEXT NOT NULL, project_id TEXT NOT NULL,
                source_id TEXT NOT NULL, remote_id TEXT NOT NULL,
                binding_hash TEXT NOT NULL, source_hash TEXT NOT NULL,
                text_hash TEXT NOT NULL, state TEXT NOT NULL,
                result_hash TEXT, error TEXT, updated REAL NOT NULL,
                PRIMARY KEY(backend,project_id,source_id))"""
            )

    @staticmethod
    def _scope(backend, binding):
        key = "bank_id" if backend == "hindsight" else "root_uri"
        value = binding.get(key) if isinstance(binding, dict) else None
        if backend == "hindsight":
            if not isinstance(value, str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value
            ):
                raise WorkbenchError(
                    "Hindsight bank_id must be a single safe identifier"
                )
            return value
        if not isinstance(value, str) or not value.startswith("viking://resources/"):
            raise WorkbenchError(
                "OpenViking root_uri must be a project directory below viking://resources/"
            )
        value = value.rstrip("/")
        segments = value[len("viking://resources/") :].split("/")
        if not segments or any(
            not re.fullmatch(r"[A-Za-z0-9_-]+", segment) for segment in segments
        ):
            raise WorkbenchError(
                "OpenViking project roots cannot contain traversal, escapes or query strings"
            )
        return value

    def _bound(self, backend, project, writing=False):
        if backend not in self.NAMES:
            raise WorkbenchError("Unknown context backend")
        pid = self.store.project(project)["id"]
        integrations = config(self.store.home).get("integrations", {})
        if not isinstance(integrations, dict):
            raise WorkbenchError("Context integrations must be an object")
        settings = integrations.get(backend, {})
        if not isinstance(settings, dict):
            raise WorkbenchError("Context backend configuration must be an object")
        if settings.get("enabled") is not True:
            raise WorkbenchError(backend + " is not enabled")
        if writing and settings.get("allow_write") is not True:
            raise WorkbenchError(
                "Enable allow_write explicitly before modifying the configured backend"
            )
        bindings = settings.get("projects", {})
        if not isinstance(bindings, dict) or pid not in bindings:
            raise WorkbenchError("No context backend scope is bound to this project")
        scope = self._scope(backend, bindings[pid])
        for other, binding in bindings.items():
            if other == pid:
                continue
            other_scope = self._scope(backend, binding)
            a, b = scope.casefold(), other_scope.casefold()
            if a == b or (
                backend == "openviking"
                and (a.startswith(b + "/") or b.startswith(a + "/"))
            ):
                raise WorkbenchError(
                    "Projects must have disjoint context backend scopes"
                )
        http = BackendHTTP(backend, settings)
        fingerprint = digest(canonical([backend, http.base, scope]))
        return pid, scope, fingerprint, http

    def _record(self, backend, pid, source_id, fingerprint):
        item = self.store.one(
            "SELECT * FROM external_context WHERE backend=? AND project_id=? AND source_id=?",
            (backend, pid, source_id),
        )
        if item["binding_hash"] != fingerprint:
            raise WorkbenchError(
                "Backend binding changed; reconcile the previous destination before migrating it"
            )
        return item

    def _finish(self, backend, pid, source_id, state, response=None, error=None):
        with self.store.transaction() as db:
            db.execute(
                "UPDATE external_context SET state=?,result_hash=?,error=?,updated=? WHERE backend=? AND project_id=? AND source_id=?",
                (
                    state,
                    digest(canonical(response)) if response is not None else None,
                    error,
                    now(),
                    backend,
                    pid,
                    source_id,
                ),
            )
            self.store.event(
                db,
                "context.backend." + state,
                source_id,
                {"backend": backend, "project": pid},
            )

    @staticmethod
    def _result(backend, response, writing=False):
        if backend == "openviking":
            if response.get("status") != "ok" or "result" not in response:
                raise BackendError(
                    "OpenViking did not confirm a successful response", writing
                )
            return response["result"]
        if writing and response.get("success") is not True:
            raise BackendError("Hindsight did not confirm the mutation", True)
        return response

    @staticmethod
    def _bank(scope):
        return "/v1/default/banks/" + urllib.parse.quote(scope, safe="")

    def ingest(self, backend, project, source_id):
        pid, scope, fingerprint, http = self._bound(backend, project, writing=True)
        source = self.context.source(pid, source_id)
        if source["project_id"] != pid:
            raise WorkbenchError(
                "A read grant does not authorize copying another project's source into a backend"
            )
        if len(source["text"].encode()) > 1024 * 1024:
            raise WorkbenchError(
                "Split and inventory sources larger than 1 MiB before backend ingestion"
            )
        remote = (
            source_id if backend == "hindsight" else scope + "/" + source_id + ".md"
        )
        with self.store.transaction() as db:
            previous = db.execute(
                "SELECT * FROM external_context WHERE backend=? AND project_id=? AND source_id=?",
                (backend, pid, source_id),
            ).fetchone()
            if previous:
                if previous["binding_hash"] != fingerprint:
                    raise WorkbenchError(
                        "Backend binding changed; reconcile the previous destination"
                    )
                if previous["source_hash"] != source["hash"] or previous[
                    "text_hash"
                ] != digest(source["text"]):
                    raise WorkbenchError(
                        "The retained source changed; create and review a new source version"
                    )
                if previous["state"] in {"retained", "index_pending"}:
                    return {
                        "source_id": source_id,
                        "remote_id": remote,
                        "state": previous["state"],
                        "duplicate": True,
                    }
                if previous["state"] not in {"failed", "deleted"}:
                    raise WorkbenchError(
                        "Previous backend mutation is unconfirmed; reconcile it before retrying"
                    )
            db.execute(
                "INSERT INTO external_context VALUES(?,?,?,?,?,?,?,'uploading',NULL,NULL,?) ON CONFLICT(backend,project_id,source_id) DO UPDATE SET state='uploading',error=NULL,updated=excluded.updated",
                (
                    backend,
                    pid,
                    source_id,
                    remote,
                    fingerprint,
                    source["hash"],
                    digest(source["text"]),
                    now(),
                ),
            )
        try:
            if backend == "hindsight":
                response = http.request(
                    "POST",
                    self._bank(scope) + "/memories",
                    {
                        "async": False,
                        "items": [
                            {
                                "content": source["text"],
                                "document_id": remote,
                                "metadata": {
                                    "awb_source_id": source_id,
                                    "awb_source_hash": source["hash"],
                                    "awb_authority": source["authority"],
                                },
                                "tags": ["awb_project=" + pid],
                            }
                        ],
                    },
                )
                result = self._result(backend, response, writing=True)
                if (
                    result.get("bank_id") != scope
                    or result.get("async") is not False
                    or result.get("items_count") != 1
                ):
                    raise BackendError(
                        "Hindsight returned an unexpected bank or asynchronous operation",
                        True,
                    )
                state = "retained"
            else:
                response = http.request(
                    "POST",
                    "/api/v1/content/write",
                    {
                        "uri": remote,
                        "content": source["text"],
                        "mode": "replace",
                        "wait": True,
                        "tags": ["awb_project=" + pid, "awb_source=" + source_id],
                        "tag_mode": "replace",
                    },
                )
                result = self._result(backend, response, writing=True)
                if (
                    not isinstance(result, dict)
                    or result.get("uri") != remote
                    or result.get("content_updated") is not True
                ):
                    raise BackendError(
                        "OpenViking did not confirm the expected file write", True
                    )
                state = (
                    "retained"
                    if result.get("semantic_status")
                    == result.get("vector_status")
                    == "complete"
                    else "index_pending"
                )
            self._finish(backend, pid, source_id, state, response)
        except BackendError as exc:
            self._finish(
                backend,
                pid,
                source_id,
                "uncertain" if exc.uncertain else "failed",
                error=str(exc),
            )
            raise
        return {
            "backend": backend,
            "project_id": pid,
            "source_id": source_id,
            "remote_id": remote,
            "state": state,
            "original_sha256": source["hash"],
            "duplicate": False,
            "notice": "Stored by the backend; generated memories and summaries still require source review.",
        }

    def _read_remote(self, backend, scope, remote, http):
        response = (
            http.request(
                "GET",
                self._bank(scope) + "/documents/" + urllib.parse.quote(remote, safe=""),
            )
            if backend == "hindsight"
            else http.request("GET", "/api/v1/content/read", query={"uri": remote})
        )
        result = self._result(backend, response)
        if backend == "hindsight":
            if result.get("bank_id") != scope or result.get("id") != remote:
                raise BackendError(
                    "Hindsight returned a document from an unexpected scope"
                )
            text = result.get("original_text")
        else:
            text = result
        if not isinstance(text, str):
            raise BackendError("The backend did not return original document text")
        return text

    def read(self, backend, project, source_id):
        pid, scope, fingerprint, http = self._bound(backend, project)
        item = self._record(backend, pid, source_id, fingerprint)
        if item["state"] not in {"retained", "index_pending"}:
            raise WorkbenchError("Backend source is deleted or unconfirmed")
        original = self.context.source(pid, source_id)
        text = self._read_remote(backend, scope, item["remote_id"], http)
        if digest(text) != item["text_hash"] or original["hash"] != item["source_hash"]:
            raise WorkbenchError(
                "Backend text changed or was sanitized; reopen and review the local original"
            )
        return {
            "text": text,
            "source_id": source_id,
            "project_id": pid,
            "backend": backend,
            "remote_id": item["remote_id"],
            "original_sha256": item["source_hash"],
            "text_sha256": item["text_hash"],
            "authority": original["authority"],
            "derived": original["authority"] in {"derived", "inference"},
            "review_required": True,
        }

    def recall(self, backend, project, query, limit=8):
        if not isinstance(query, str) or not query.strip() or len(query) > 8192:
            raise WorkbenchError("Recall requires a query of 1–8192 characters")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise WorkbenchError("Recall limit must be between 1 and 100")
        pid, scope, fingerprint, http = self._bound(backend, project)
        if backend == "hindsight":
            response = http.request(
                "POST",
                self._bank(scope) + "/memories/recall",
                {
                    "query": query,
                    "max_tokens": min(8192, limit * 512),
                    "budget": "mid",
                    "tags": ["awb_project=" + pid],
                    "tags_match": "all_strict",
                },
            )
            items = response.get("results")
        else:
            response = http.request(
                "POST",
                "/api/v1/search/find",
                {
                    "query": query,
                    "target_uri": scope + "/",
                    "limit": limit,
                    "context_type": "resource",
                    "tags": ["awb_project=" + pid],
                },
            )
            result = self._result(backend, response)
            items = result.get("resources") if isinstance(result, dict) else None
        if not isinstance(items, list):
            raise BackendError("Backend recall response has no result list")
        managed = {
            r["remote_id"]: r
            for r in self.store.rows(
                "SELECT * FROM external_context WHERE backend=? AND project_id=? AND binding_hash=? AND state IN ('retained','index_pending')",
                (backend, pid, fingerprint),
            )
        }
        results, omitted = [], []
        for item in items:
            if not isinstance(item, dict):
                raise BackendError("Backend recall returned a malformed item")
            identity = item.get("document_id" if backend == "hindsight" else "uri")
            if identity is not None and not isinstance(identity, str):
                raise BackendError(
                    "Backend recalled item has an invalid source identifier"
                )
            record = managed.get(identity)
            if record is None:
                omitted.append({"reason": "No managed original in this project"})
                continue
            if self.store.rows(
                "SELECT id FROM sources WHERE supersedes=? AND revoked=0",
                (record["source_id"],),
            ):
                omitted.append(
                    {
                        "source_id": record["source_id"],
                        "reason": "A newer source version supersedes this result",
                    }
                )
                continue
            try:
                original = self.context.source(pid, record["source_id"])
                if (
                    original["hash"] != record["source_hash"]
                    or digest(original["text"]) != record["text_hash"]
                ):
                    raise WorkbenchError(
                        "Original no longer matches the ingested version"
                    )
            except WorkbenchError:
                omitted.append(
                    {
                        "source_id": record["source_id"],
                        "reason": "Original revoked, purged or unavailable",
                    }
                )
                continue
            text = item.get("text") if backend == "hindsight" else item.get("abstract")
            if not isinstance(text, str):
                raise BackendError("Backend recalled item has no text")
            if len(results) >= limit:
                omitted.append({"reason": "Result limit"})
                continue
            results.append(
                {
                    "text": text,
                    "backend": backend,
                    "remote_id": identity,
                    "source_id": original["id"],
                    "original_sha256": original["hash"],
                    "source_authority": original["authority"],
                    "derived": True,
                    "review_required": True,
                    "kind": item.get("type", "summary"),
                    "score": item.get("score", item.get("scores")),
                }
            )
        return {
            "backend": backend,
            "project_id": pid,
            "results": results,
            "omitted": omitted,
            "notice": "Recall returns derived evidence, not verified facts. Open each original before citing.",
        }

    def delete(self, backend, project, source_id):
        pid, scope, fingerprint, http = self._bound(backend, project, writing=True)
        item = self._record(backend, pid, source_id, fingerprint)
        if item["state"] == "deleted":
            return {"source_id": source_id, "state": "deleted", "duplicate": True}
        with self.store.transaction() as db:
            current = db.execute(
                "SELECT state FROM external_context WHERE backend=? AND project_id=? AND source_id=?",
                (backend, pid, source_id),
            ).fetchone()[0]
            if current in {"uploading", "deleting", "delete_uncertain"}:
                raise WorkbenchError(
                    "A backend mutation is in progress or uncertain; reconcile it first"
                )
            db.execute(
                "UPDATE external_context SET state='deleting',updated=? WHERE backend=? AND project_id=? AND source_id=?",
                (now(), backend, pid, source_id),
            )
        try:
            response = (
                http.request(
                    "DELETE",
                    self._bank(scope)
                    + "/documents/"
                    + urllib.parse.quote(item["remote_id"], safe=""),
                )
                if backend == "hindsight"
                else http.request(
                    "DELETE",
                    "/api/v1/fs",
                    query={
                        "uri": item["remote_id"],
                        "recursive": "false",
                        "wait": "true",
                    },
                )
            )
            result = self._result(backend, response, writing=True)
            if (
                not isinstance(result, dict)
                or (
                    result.get("document_id")
                    if backend == "hindsight"
                    else result.get("uri")
                )
                != item["remote_id"]
            ):
                raise BackendError(
                    "Backend acknowledged deletion of an unexpected object", True
                )
            self._finish(backend, pid, source_id, "deleted", response)
        except BackendError as exc:
            if exc.status_code == 404:
                self._finish(
                    backend, pid, source_id, "deleted", {"already_absent": True}
                )
            else:
                self._finish(
                    backend,
                    pid,
                    source_id,
                    "delete_uncertain" if exc.uncertain else "delete_failed",
                    error=str(exc),
                )
                raise
        return {
            "backend": backend,
            "source_id": source_id,
            "state": "deleted",
            "duplicate": False,
            "notice": "Remote deletion acknowledged; this receipt does not erase independent provider backups.",
        }

    def reconcile(self, backend, project, source_id):
        pid, scope, fingerprint, http = self._bound(backend, project)
        item = self._record(backend, pid, source_id, fingerprint)
        if (
            item["state"] in {"uploading", "deleting"}
            and now() - item["updated"] < http.timeout + 5
        ):
            raise WorkbenchError(
                "A backend mutation is still within its request lease; reconcile after it finishes"
            )
        try:
            text = self._read_remote(backend, scope, item["remote_id"], http)
        except BackendError as exc:
            if exc.status_code != 404:
                raise
            state = "deleted" if item["state"].startswith("delet") else "failed"
            self._finish(backend, pid, source_id, state, {"remote_absent": True})
            return {"source_id": source_id, "state": state}
        if digest(text) != item["text_hash"]:
            raise WorkbenchError(
                "Remote content does not match the retained source; manual reconciliation required"
            )
        # Readback proves stored bytes, not extraction/index completion.
        state = (
            "delete_failed" if item["state"].startswith("delet") else "index_pending"
        )
        self._finish(backend, pid, source_id, state, {"readback_sha256": digest(text)})
        return {
            "source_id": source_id,
            "state": state,
            "notice": "Readback confirms bytes only, not generated-memory or index readiness.",
        }

    def status(self, project=None):
        if project:
            return self.store.rows(
                "SELECT * FROM external_context WHERE project_id=? ORDER BY updated DESC",
                (self.store.project(project)["id"],),
            )
        return self.store.rows("SELECT * FROM external_context ORDER BY updated DESC")

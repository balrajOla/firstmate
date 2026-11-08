"""Loopback HTTP contract tests, not live backend/model qualification."""

import json
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from assistant_workbench.context import Context
from assistant_workbench.context_backends import (
    BackendError,
    BackendHTTP,
    ContextBackends,
)
from assistant_workbench.util import WorkbenchError


@pytest.fixture
def backend_server():
    state = {
        "requests": [],
        "documents": {},
        "fault": None,
        "recall_extra": [],
        "index_complete": True,
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def handle_request(self):
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else None
            parsed = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            state["requests"].append(
                {
                    "method": self.command,
                    "path": parsed.path,
                    "query": query,
                    "body": body,
                    "headers": dict(self.headers),
                }
            )
            if state.get("pause_mutation") and self.command in {"POST", "DELETE"}:
                state["entered"].set()
                assert state["release"].wait(
                    3
                ), "Contract test failed to release the request"
            if state["fault"]:
                if state["fault"] == "redirect":
                    self.send_response(302)
                    self.send_header("Location", state["redirect_to"])
                    self.end_headers()
                    return
                self.send_response(int(state["fault"]))
                self.end_headers()
                return
            status = 200
            parts = parsed.path.strip("/").split("/")
            if parsed.path.startswith("/v1/default/banks/"):
                bank = parts[3]
                if parsed.path.endswith("/memories/recall"):
                    result = {
                        "results": [
                            {
                                "id": "fact-" + key,
                                "text": "Derived: " + value,
                                "document_id": key,
                                "type": "world",
                            }
                            for (scope, key), value in state["documents"].items()
                            if scope == bank
                        ]
                        + state["recall_extra"]
                    }
                elif self.command == "POST":
                    for item in body["items"]:
                        state["documents"][(bank, item["document_id"])] = item[
                            "content"
                        ]
                    result = {
                        "success": True,
                        "bank_id": bank,
                        "items_count": len(body["items"]),
                        "async": body["async"],
                    }
                else:
                    ident = urllib.parse.unquote(parts[-1])
                    key = (bank, ident)
                    if key not in state["documents"]:
                        status, result = 404, {"error": "absent"}
                    elif self.command == "DELETE":
                        del state["documents"][key]
                        result = {
                            "success": True,
                            "document_id": ident,
                            "memory_units_deleted": 1,
                        }
                    else:
                        result = {
                            "id": ident,
                            "bank_id": bank,
                            "original_text": state["documents"][key],
                        }
            elif parsed.path == "/api/v1/content/write":
                state["documents"][("ov", body["uri"])] = body["content"]
                result = {
                    "status": "ok",
                    "result": {
                        "uri": body["uri"],
                        "content_updated": True,
                        "semantic_status": (
                            "complete" if state["index_complete"] else "skipped"
                        ),
                        "vector_status": "complete",
                    },
                }
            elif parsed.path == "/api/v1/search/find":
                result = {
                    "status": "ok",
                    "result": {
                        "resources": [
                            {
                                "uri": ident,
                                "abstract": "Derived: " + text,
                                "level": 2,
                                "score": 12.0,
                            }
                            for (scope, ident), text in state["documents"].items()
                            if scope == "ov" and ident.startswith(body["target_uri"])
                        ]
                        + state["recall_extra"]
                    },
                }
            else:
                ident = query["uri"][0]
                key = ("ov", ident)
                if key not in state["documents"]:
                    status, result = 404, {"status": "error"}
                elif self.command == "DELETE":
                    del state["documents"][key]
                    result = {"status": "ok", "result": {"uri": ident}}
                else:
                    result = {"status": "ok", "result": state["documents"][key]}
            raw = json.dumps(state.get("response_override", result)).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        do_GET = handle_request
        do_POST = handle_request
        do_DELETE = handle_request

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
    )
    thread.start()
    state["url"] = f"http://127.0.0.1:{server.server_port}"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def configure(app, project, server, backend, **overrides):
    settings = json.loads((app.home / "config.json").read_text())
    scope = (
        {"bank_id": "awb-" + project["id"]}
        if backend == "hindsight"
        else {"root_uri": "viking://resources/workbench/" + project["id"]}
    )
    settings["integrations"][backend] = {
        "enabled": True,
        "url": server["url"],
        "auth": "local",
        "allow_write": True,
        "projects": {project["id"]: scope},
        **overrides,
    }
    (app.home / "config.json").write_text(json.dumps(settings))
    return settings["integrations"][backend]


def source(app, project, tmp_path, name="source.md"):
    path = tmp_path / name
    path.write_text(
        "Revenue is 17.50 GBP, excluding refunds. This is not a profit figure."
    )
    return Context(app.store).ingest(project["id"], str(path))


@pytest.mark.parametrize("backend", ["hindsight", "openviking"])
def test_backend_round_trip_preserves_original_and_marks_derived(
    app, project, tmp_path, backend_server, backend
):
    configure(app, project, backend_server, backend)
    original = source(app, project, tmp_path)
    api = ContextBackends(app.store)
    retained = api.ingest(backend, project["id"], original["id"])
    assert retained["state"] == "retained"
    count = len(backend_server["requests"])
    assert api.ingest(backend, project["id"], original["id"])["duplicate"]
    assert len(backend_server["requests"]) == count
    read = api.read(backend, project["id"], original["id"])
    assert "excluding refunds" in read["text"] and not read["derived"]
    assert read["original_sha256"] == original["hash"]
    recalled = api.recall(backend, project["id"], "profit figure")
    assert len(recalled["results"]) == 1 and recalled["results"][0]["derived"]
    assert recalled["results"][0]["review_required"] and not recalled["omitted"]
    assert api.delete(backend, project["id"], original["id"])["state"] == "deleted"
    assert api.delete(backend, project["id"], original["id"])["duplicate"]
    assert not api.recall(backend, project["id"], "profit figure")["results"]


@pytest.mark.parametrize("backend", ["hindsight", "openviking"])
def test_backend_does_not_leak_unmanaged_or_revoked_results(
    app, project, tmp_path, backend_server, backend
):
    configure(app, project, backend_server, backend)
    original = source(app, project, tmp_path)
    api = ContextBackends(app.store)
    api.ingest(backend, project["id"], original["id"])
    backend_server["recall_extra"] = [
        {
            "document_id": "another-project",
            "uri": "viking://resources/other/secret.md",
            "text": "SECRET",
            "abstract": "SECRET",
        }
    ]
    result = api.recall(backend, project["id"], "refunds")
    assert len(result["results"]) == 1 and len(result["omitted"]) == 1
    assert "SECRET" not in json.dumps(result)
    Context(app.store).revoke(project["id"], original["id"])
    assert not api.recall(backend, project["id"], "refunds")["results"]
    with pytest.raises(WorkbenchError):
        api.read(backend, project["id"], original["id"])


@pytest.mark.parametrize("backend", ["hindsight", "openviking"])
def test_remote_deletion_remains_possible_after_local_purge(
    app, project, tmp_path, backend_server, backend
):
    configure(app, project, backend_server, backend)
    original = source(app, project, tmp_path)
    api = ContextBackends(app.store)
    api.ingest(backend, project["id"], original["id"])
    Context(app.store).purge(project["id"], original["id"])
    assert api.delete(backend, project["id"], original["id"])["state"] == "deleted"


@pytest.mark.parametrize("backend", ["hindsight", "openviking"])
def test_backend_requires_explicit_project_and_write_configuration(
    app, project, tmp_path, backend_server, backend
):
    api = ContextBackends(app.store)
    original = source(app, project, tmp_path)
    with pytest.raises(WorkbenchError, match="not enabled"):
        api.ingest(backend, project["id"], original["id"])
    configure(app, project, backend_server, backend, projects={})
    with pytest.raises(WorkbenchError, match="No context backend scope"):
        api.recall(backend, project["id"], "query")
    configure(app, project, backend_server, backend, allow_write=False)
    with pytest.raises(WorkbenchError, match="allow_write"):
        api.ingest(backend, project["id"], original["id"])
    assert not backend_server["requests"]


@pytest.mark.parametrize("backend", ["hindsight", "openviking"])
def test_backend_binding_change_cannot_redirect_existing_deletions(
    app, project, tmp_path, backend_server, backend
):
    settings = configure(app, project, backend_server, backend)
    original = source(app, project, tmp_path)
    api = ContextBackends(app.store)
    api.ingest(backend, project["id"], original["id"])
    key = "bank_id" if backend == "hindsight" else "root_uri"
    settings["projects"][project["id"]][key] += "-changed"
    configure(app, project, backend_server, backend, projects=settings["projects"])
    count = len(backend_server["requests"])
    with pytest.raises(WorkbenchError, match="binding changed"):
        api.delete(backend, project["id"], original["id"])
    assert len(backend_server["requests"]) == count


@pytest.mark.parametrize("backend", ["hindsight", "openviking"])
def test_backend_refuses_shared_scopes_and_foreign_source_ingestion(
    app, project, tmp_path, backend_server, backend
):
    other = app.store.add_project("Other")
    settings = configure(app, project, backend_server, backend)
    mapping = settings["projects"]
    mapping[other["id"]] = dict(mapping[project["id"]])
    configure(app, project, backend_server, backend, projects=mapping)
    with pytest.raises(WorkbenchError, match="disjoint"):
        ContextBackends(app.store).recall(backend, project["id"], "query")
    configure(app, project, backend_server, backend)
    original = source(app, other, tmp_path)
    app.store.grant(project["id"], other["id"])
    with pytest.raises(WorkbenchError, match="read grant"):
        ContextBackends(app.store).ingest(backend, project["id"], original["id"])
    assert not backend_server["requests"]


@pytest.mark.parametrize("backend", ["hindsight", "openviking"])
def test_backend_uncertain_write_is_not_blindly_retried(
    app, project, tmp_path, backend_server, backend
):
    configure(app, project, backend_server, backend)
    original = source(app, project, tmp_path)
    api = ContextBackends(app.store)
    backend_server["fault"] = 503
    with pytest.raises(BackendError) as failure:
        api.ingest(backend, project["id"], original["id"])
    assert failure.value.uncertain and api.status()[0]["state"] == "uncertain"
    count = len(backend_server["requests"])
    with pytest.raises(WorkbenchError, match="reconcile"):
        api.ingest(backend, project["id"], original["id"])
    assert len(backend_server["requests"]) == count
    backend_server["fault"] = None
    assert api.reconcile(backend, project["id"], original["id"])["state"] == "failed"
    assert api.ingest(backend, project["id"], original["id"])["state"] == "retained"


@pytest.mark.parametrize("backend", ["hindsight", "openviking"])
def test_backend_readback_detects_remote_content_changes(
    app, project, tmp_path, backend_server, backend
):
    configure(app, project, backend_server, backend)
    original = source(app, project, tmp_path)
    api = ContextBackends(app.store)
    api.ingest(backend, project["id"], original["id"])
    for key in backend_server["documents"]:
        backend_server["documents"][key] = "Revenue is profit."
    with pytest.raises(WorkbenchError, match="changed or was sanitized"):
        api.read(backend, project["id"], original["id"])


def test_openviking_reports_incomplete_indexing(app, project, tmp_path, backend_server):
    configure(app, project, backend_server, "openviking")
    original = source(app, project, tmp_path)
    backend_server["index_complete"] = False
    result = ContextBackends(app.store).ingest(
        "openviking", project["id"], original["id"]
    )
    assert result["state"] == "index_pending"


def test_backend_rejects_project_root_prefix_collision(app, project, backend_server):
    other = app.store.add_project("Child")
    configure(
        app,
        project,
        backend_server,
        "openviking",
        projects={
            project["id"]: {"root_uri": "viking://resources/company"},
            other["id"]: {"root_uri": "viking://resources/COMPANY/child"},
        },
    )
    with pytest.raises(WorkbenchError, match="disjoint"):
        ContextBackends(app.store).recall("openviking", project["id"], "query")


@pytest.mark.parametrize(
    "url",
    [
        "http://external.example",
        "https://user:pass@example.com",
        "https://example.com/base",
        "https://example.com?secret=x",
        "https://example.com/#frag",
        "file:///tmp/db",
        "https://example.com:bad",
        "http://",
    ],
)
def test_backend_transport_rejects_unsafe_origins(url):
    with pytest.raises(WorkbenchError):
        BackendHTTP("hindsight", {"url": url, "auth": "local"})


@pytest.mark.parametrize(
    "root",
    [
        "viking://resources/",
        "viking://resources/project/../other",
        "viking://resources/project/%2e%2e",
        "viking://resources/project?other=x",
        "viking://resources/project\\other",
        "https://other.example/resource",
    ],
)
def test_backend_rejects_unsafe_viking_roots(root):
    with pytest.raises(WorkbenchError):
        ContextBackends._scope("openviking", {"root_uri": root})


def test_backend_transport_refuses_redirect_and_missing_credentials(
    backend_server, monkeypatch
):
    monkeypatch.delenv("AWB_TEST_BACKEND_TOKEN", raising=False)
    with pytest.raises(WorkbenchError, match="service token"):
        BackendHTTP(
            "hindsight",
            {
                "url": backend_server["url"],
                "auth": "api",
                "token_env": "AWB_TEST_BACKEND_TOKEN",
            },
        )
    monkeypatch.setenv("AWB_TEST_BACKEND_TOKEN", "synthetic-token")
    http = BackendHTTP(
        "hindsight",
        {
            "url": backend_server["url"],
            "auth": "subscription",
            "token_env": "AWB_TEST_BACKEND_TOKEN",
        },
    )
    backend_server.update(
        fault="redirect", redirect_to=backend_server["url"] + "/credential-leak"
    )
    with pytest.raises(BackendError) as failure:
        http.request("GET", "/probe")
    assert failure.value.status_code == 302
    assert len(backend_server["requests"]) == 1
    assert (
        backend_server["requests"][0]["headers"]["Authorization"]
        == "Bearer synthetic-token"
    )


@pytest.mark.parametrize("backend", ["hindsight", "openviking"])
def test_backend_delete_timeout_needs_reconciliation(
    app, project, tmp_path, backend_server, backend
):
    configure(app, project, backend_server, backend)
    original = source(app, project, tmp_path)
    api = ContextBackends(app.store)
    api.ingest(backend, project["id"], original["id"])
    backend_server["fault"] = 503
    with pytest.raises(BackendError):
        api.delete(backend, project["id"], original["id"])
    assert api.status()[0]["state"] == "delete_uncertain"
    with pytest.raises(WorkbenchError, match="reconcile"):
        api.delete(backend, project["id"], original["id"])
    backend_server["fault"] = None
    assert (
        api.reconcile(backend, project["id"], original["id"])["state"]
        == "delete_failed"
    )
    assert api.delete(backend, project["id"], original["id"])["state"] == "deleted"


@pytest.mark.parametrize("limit", [0, 101, True, "8"])
def test_backend_recall_rejects_invalid_limits(app, limit):
    with pytest.raises(WorkbenchError, match="limit"):
        ContextBackends(app.store).recall("hindsight", "unused", "query", limit)


@pytest.mark.parametrize("backend", ["hindsight", "openviking"])
def test_backend_concurrent_ingest_does_not_duplicate_writes(
    app, project, tmp_path, backend_server, backend
):
    configure(app, project, backend_server, backend)
    original = source(app, project, tmp_path)
    api = ContextBackends(app.store)
    backend_server.update(
        pause_mutation=True, entered=threading.Event(), release=threading.Event()
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(api.ingest, backend, project["id"], original["id"])
        try:
            assert backend_server["entered"].wait(2)
            with pytest.raises(WorkbenchError, match="reconcile"):
                api.ingest(backend, project["id"], original["id"])
            with pytest.raises(WorkbenchError, match="request lease"):
                api.reconcile(backend, project["id"], original["id"])
            assert len(backend_server["requests"]) == 1
        finally:
            backend_server["release"].set()
        assert pending.result(timeout=3)["state"] == "retained"


@pytest.mark.parametrize("backend", ["hindsight", "openviking"])
def test_backend_unexpected_write_schema_records_uncertain_mutation(
    app, project, tmp_path, backend_server, backend
):
    configure(app, project, backend_server, backend)
    original = source(app, project, tmp_path)
    api = ContextBackends(app.store)
    backend_server["response_override"] = {
        "status": "ok",
        "result": [],
        "success": True,
    }
    with pytest.raises(BackendError):
        api.ingest(backend, project["id"], original["id"])
    assert api.status()[0]["state"] == "uncertain"
    del backend_server["response_override"]
    assert (
        api.reconcile(backend, project["id"], original["id"])["state"]
        == "index_pending"
    )
    assert api.read(backend, project["id"], original["id"])["text"].startswith(
        "Revenue"
    )


def test_backend_unexpected_deletion_schema_does_not_claim_success(
    app, project, tmp_path, backend_server
):
    configure(app, project, backend_server, "openviking")
    original = source(app, project, tmp_path)
    api = ContextBackends(app.store)
    api.ingest("openviking", project["id"], original["id"])
    backend_server["response_override"] = {"status": "ok", "result": []}
    with pytest.raises(BackendError):
        api.delete("openviking", project["id"], original["id"])
    assert api.status()[0]["state"] == "delete_uncertain"
    del backend_server["response_override"]
    assert (
        api.reconcile("openviking", project["id"], original["id"])["state"] == "deleted"
    )


@pytest.mark.parametrize("backend", ["hindsight", "openviking"])
def test_backend_recall_rejects_malformed_identity(
    app, project, backend_server, backend
):
    configure(app, project, backend_server, backend)
    backend_server["recall_extra"] = [
        {"document_id": [], "uri": [], "text": "untrusted", "abstract": "untrusted"}
    ]
    with pytest.raises(BackendError, match="source identifier"):
        ContextBackends(app.store).recall(backend, project["id"], "query")


@pytest.mark.parametrize("backend", ["hindsight", "openviking"])
def test_backend_absent_delete_is_idempotent(
    app, project, tmp_path, backend_server, backend
):
    configure(app, project, backend_server, backend)
    original = source(app, project, tmp_path)
    api = ContextBackends(app.store)
    api.ingest(backend, project["id"], original["id"])
    backend_server["documents"].clear()
    assert api.delete(backend, project["id"], original["id"])["state"] == "deleted"


@pytest.mark.parametrize("backend", ["hindsight", "openviking"])
def test_backend_read_rejects_oversize_response(
    app, project, tmp_path, backend_server, backend
):
    configure(app, project, backend_server, backend)
    original = source(app, project, tmp_path)
    api = ContextBackends(app.store)
    api.ingest(backend, project["id"], original["id"])
    backend_server["response_override"] = {"large": "x" * (4 * 1024 * 1024)}
    with pytest.raises(BackendError, match="exceeded 4 MiB"):
        api.read(backend, project["id"], original["id"])


def test_backend_transport_rejects_invalid_ipv6_and_boolean_timeout():
    with pytest.raises(WorkbenchError):
        BackendHTTP("openviking", {"url": "http://[broken", "auth": "local"})
    with pytest.raises(WorkbenchError):
        BackendHTTP(
            "openviking",
            {"url": "http://localhost", "auth": "local", "timeout_seconds": True},
        )


@pytest.mark.parametrize("backend", ["hindsight", "openviking"])
def test_backend_recall_omits_superseded_source_versions(
    app, project, tmp_path, backend_server, backend
):
    configure(app, project, backend_server, backend)
    original = source(app, project, tmp_path)
    api = ContextBackends(app.store)
    api.ingest(backend, project["id"], original["id"])
    (tmp_path / "source.md").write_text(
        "Correction: revenue is 19.20 GBP, excluding refunds."
    )
    current = Context(app.store).ingest(project["id"], str(tmp_path / "source.md"))
    api.ingest(backend, project["id"], current["id"])
    result = api.recall(backend, project["id"], "revenue")
    assert [item["source_id"] for item in result["results"]] == [current["id"]]
    assert any("supersedes" in item["reason"] for item in result["omitted"])


def test_openviking_uses_service_token_header(backend_server, monkeypatch):
    monkeypatch.setenv("AWB_OV_TEST_TOKEN", "synthetic-openviking-token")
    http = BackendHTTP(
        "openviking",
        {"url": backend_server["url"], "auth": "api", "token_env": "AWB_OV_TEST_TOKEN"},
    )
    backend_server["fault"] = 401
    with pytest.raises(BackendError):
        http.request("GET", "/probe")
    headers = {
        key.lower(): value
        for key, value in backend_server["requests"][0]["headers"].items()
    }
    assert headers["x-api-key"] == "synthetic-openviking-token"
    assert "authorization" not in headers

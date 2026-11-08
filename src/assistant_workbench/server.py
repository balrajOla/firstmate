from __future__ import annotations

import hmac
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from .app import Application
from .util import WorkbenchError, atomic, canonical, now


def make_server(home, port=0, background=False):
    app = Application(home)
    token_file = home / "service.token"
    if not token_file.exists():
        atomic(token_file, secrets.token_urlsafe(32))
    if token_file.is_symlink():
        raise WorkbenchError("Service token cannot be a symlink")
    token = token_file.read_text().strip()
    if len(token) < 32:
        raise WorkbenchError(
            "Operator token is invalid; restore or rotate it before starting the bridge"
        )

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self):
            super().setup()
            self.connection.settimeout(20)

        def log_message(self, *args):
            pass

        def reply(self, status, data):
            encoded = canonical(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def authorized(self):
            # This is an operator API. Per-project worker isolation additionally requires
            # a separate OS/container boundary; the same local UID can access its files.
            if self.headers.get("Origin") is not None:
                return False
            return hmac.compare_digest(
                self.headers.get("Authorization", ""), "Bearer " + token
            )

        def do_GET(self):
            if self.path == "/health":
                return self.reply(200, {"status": "ok", "version": "0.1.0"})
            if not self.authorized():
                return self.reply(401, {"error": "Operator token required"})
            if self.path == "/snapshot":
                return self.reply(200, app.store.snapshot())
            return self.reply(404, {"error": "Unknown route"})

        def do_POST(self):
            worker = self.path == "/worker"
            if self.headers.get("Origin") is not None or (
                not worker and not self.authorized()
            ):
                return self.reply(
                    401,
                    {"error": "Operator token required; browser origins are rejected"},
                )
            if self.path not in {"/request", "/worker"}:
                return self.reply(404, {"error": "Unknown route"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > 2 * 1024 * 1024:
                    return self.reply(
                        413, {"error": "Request body too large or missing"}
                    )
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise WorkbenchError("Request body must be an object")
                allowed = (
                    {"action", "args", "host_id"} if worker else {"action", "args"}
                )
                if set(body) - allowed:
                    raise WorkbenchError("Unknown request fields")
                if worker:
                    from .runtime_backends import Fleet

                    authorization = self.headers.get("Authorization", "")
                    if not authorization.startswith("Bearer ") or not authorization[7:]:
                        return self.reply(401, {"error": "Host token required"})
                    result = Fleet(app.store).worker_request(
                        body["host_id"],
                        authorization[7:],
                        body["action"],
                        body.get("args", {}),
                    )
                else:
                    result = app.dispatch(body["action"], body.get("args", {}))
                return self.reply(200, {"result": result})
            except (ValueError, KeyError, TypeError, WorkbenchError) as exc:
                return self.reply(400, {"error": str(exc)})
            except Exception:
                return self.reply(
                    500, {"error": "Internal failure; no successful outcome recorded"}
                )

    class BoundedServer(ThreadingHTTPServer):
        # Bound blocking operator/integration calls without launching agent workers here.
        slots = threading.BoundedSemaphore(8)

        def process_request(self, request, address):
            if not self.slots.acquire(blocking=False):
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                )
                request.close()
                return
            try:
                super().process_request(request, address)
            except BaseException:
                self.slots.release()
                raise

        def process_request_thread(self, request, address):
            try:
                super().process_request_thread(request, address)
            finally:
                self.slots.release()

    server = BoundedServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    stopped = threading.Event()
    server.workbench_stop = stopped
    if background:

        def loop():
            while not stopped.is_set():
                try:
                    app.scheduled()
                    try:
                        # One unresolved request per pass, when semantic
                        # routing is configured; a no-op otherwise. A routing
                        # failure must not stop dispatch of routed work.
                        app.dispatch("routing.pending")
                    except Exception as exc:
                        atomic(
                            home / "routing-health.json",
                            canonical({"status": "blocked", "reason": str(exc), "at": now()}),
                        )
                    app.dispatch("coordinator.tick")
                    atomic(
                        home / "bridge-health.json",
                        canonical({"status": "running", "at": now()}),
                    )
                except Exception as exc:
                    atomic(
                        home / "bridge-health.json",
                        canonical(
                            {"status": "blocked", "reason": str(exc), "at": now()}
                        ),
                    )
                stopped.wait(2)

        def snapshots():
            from .terminal import status

            while not stopped.is_set():
                try:
                    atomic(home / "snapshot.json", canonical(app.store.snapshot()))
                    atomic(home / "terminal-status.json", canonical(status(app.store)))
                except Exception:
                    pass  # Old snapshot becomes visibly stale.
                stopped.wait(2)

        def observations():
            while not stopped.is_set():
                try:
                    result = app.dispatch("coordinator.observe")
                    atomic(
                        home / "observation-health.json",
                        canonical(
                            {
                                "status": "observed",
                                "at": now(),
                                "records": len(result or {}),
                            }
                        ),
                    )
                except Exception as exc:
                    atomic(
                        home / "observation-health.json",
                        canonical(
                            {"status": "unavailable", "reason": str(exc), "at": now()}
                        ),
                    )
                stopped.wait(10)

        for target in (loop, snapshots, observations):
            threading.Thread(target=target, daemon=True).start()
    atomic(
        home / "service.json",
        canonical(
            {
                "url": "http://127.0.0.1:" + str(server.server_port),
                "token_file": str(token_file),
                "started": now(),
            }
        ),
    )
    return server

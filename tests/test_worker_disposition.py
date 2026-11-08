"""The worker disposition command against a real loopback HTTP endpoint."""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from assistant_workbench.worker_disposition import main


@pytest.fixture
def paperclip():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        status = 200

        def do_PATCH(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append({"path": self.path, "headers": dict(self.headers), "body": body})
            # Pinned Paperclip refuses blocked without an unblock path (HTTP 422).
            refused = body.get("status") == "blocked" and not body.get("unblockDescriptor")
            status = 422 if refused else server.status
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(
                {"error": "Entering blocked requires an unblockDescriptor"} if status == 422
                else {"error": "Agent cannot update this issue"} if status >= 400 else {}
            ).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.status = 200
    server.seen = seen
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def run_env(server, **overrides):
    env = {
        "PAPERCLIP_API_URL": "http://127.0.0.1:" + str(server.server_address[1]) + "/api",
        "PAPERCLIP_API_KEY": "tok-secret",
        "PAPERCLIP_RUN_ID": "run-1",
        "PAPERCLIP_TASK_ID": "task-1",
    }
    env.update(overrides)
    return env


def test_done_records_only_its_own_task_with_the_run_identity(paperclip, capsys):
    assert main(["done"], run_env(paperclip)) == 0
    [call] = paperclip.seen
    assert call["path"] == "/api/issues/task-1"
    assert call["body"] == {"status": "done"}
    assert call["headers"]["Authorization"] == "Bearer tok-secret"
    assert call["headers"]["X-Paperclip-Run-Id"] == "run-1"
    assert "tok-secret" not in capsys.readouterr().out


def test_blocked_names_what_would_unblock_it_for_the_board(paperclip):
    assert main(["blocked", "  Supply vendor/legacy_lib.py  "], run_env(paperclip)) == 0
    assert main(["blocked"], run_env(paperclip)) == 0
    first, second = (call["body"] for call in paperclip.seen)
    assert first == {"status": "blocked", "unblockDescriptor": {
        "owner": "board", "action": "Supply vendor/legacy_lib.py"}}
    # Without a reason the board is pointed at the worker's report.
    assert second["unblockDescriptor"]["action"].endswith("see its final report.")


def test_refused_update_fails_without_printing_the_token(paperclip, capsys):
    paperclip.status = 403
    assert main(["done"], run_env(paperclip)) == 1
    out = capsys.readouterr()
    # Paperclip's own reason is shown; the token never is.
    assert "HTTP 403" in out.err and "Agent cannot update this issue" in out.err
    assert "tok-secret" not in out.err + out.out


def test_rejects_other_statuses_and_missing_settings(paperclip, capsys):
    assert main(["in_review"], run_env(paperclip)) == 2
    assert main(["done", "extra"], run_env(paperclip)) == 2
    assert main(["blocked", "reason", "extra"], run_env(paperclip)) == 2
    assert main(["done"], run_env(paperclip, PAPERCLIP_TASK_ID="")) == 1
    assert "PAPERCLIP_TASK_ID" in capsys.readouterr().err
    assert not paperclip.seen


def test_remote_plain_http_is_refused(paperclip):
    env = run_env(paperclip, PAPERCLIP_API_URL="http://paperclip.example/api")
    assert main(["done"], env) == 1
    assert not paperclip.seen


def test_runs_as_the_module_command_workers_are_allowed(paperclip):
    env = {**os.environ, **run_env(paperclip)}
    done = subprocess.run(
        [sys.executable, "-m", "assistant_workbench.worker_disposition", "done"],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert paperclip.seen[0]["body"] == {"status": "done"}

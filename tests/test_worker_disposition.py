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
            body = self.rfile.read(int(self.headers["Content-Length"]))
            seen.append({"path": self.path, "headers": dict(self.headers), "body": json.loads(body)})
            self.send_response(server.status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

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


@pytest.mark.parametrize("disposition", ["done", "blocked"])
def test_records_only_its_own_task_with_the_run_identity(paperclip, disposition, capsys):
    assert main([disposition], run_env(paperclip)) == 0
    [call] = paperclip.seen
    assert call["path"] == "/api/issues/task-1"
    assert call["body"] == {"status": disposition}
    assert call["headers"]["Authorization"] == "Bearer tok-secret"
    assert call["headers"]["X-Paperclip-Run-Id"] == "run-1"
    assert "tok-secret" not in capsys.readouterr().out


def test_refused_update_fails_without_printing_the_token(paperclip, capsys):
    paperclip.status = 403
    assert main(["done"], run_env(paperclip)) == 1
    out = capsys.readouterr()
    assert "HTTP 403" in out.err and "tok-secret" not in out.err + out.out


def test_rejects_other_statuses_and_missing_settings(paperclip, capsys):
    assert main(["in_review"], run_env(paperclip)) == 2
    assert main(["done", "extra"], run_env(paperclip)) == 2
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

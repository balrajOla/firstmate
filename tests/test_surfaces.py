import io
import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
import pytest
from textual.widgets import Input, DataTable, TabbedContent, Static
from assistant_workbench.tui import WorkbenchApp
from assistant_workbench.server import make_server
from assistant_workbench.mcp import serve_stdio
from assistant_workbench.reports import export_report
from assistant_workbench.integrations import run_process, Integrations
from assistant_workbench.quality import Outcomes
from assistant_workbench.util import WorkbenchError, safe_text


def test_http_token_origin_and_durable_prompt(app, project):
    server = make_server(app.home)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:" + str(server.server_port)
    try:
        assert json.load(urllib.request.urlopen(base + "/health"))["status"] == "ok"
        with pytest.raises(urllib.error.HTTPError) as missing:
            urllib.request.urlopen(base + "/snapshot")
        assert missing.value.code == 401
        body = json.dumps(
            {
                "action": "prompt.submit",
                "args": {
                    "text": "Hello",
                    "client_key": "http",
                    "project": project["id"],
                },
            }
        ).encode()
        headers = {
            "Authorization": "Bearer " + (app.home / "service.token").read_text(),
            "Content-Type": "application/json",
        }
        result = json.load(
            urllib.request.urlopen(
                urllib.request.Request(base + "/request", data=body, headers=headers)
            )
        )
        assert result["result"]["request_id"]
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(
                urllib.request.Request(
                    base + "/request",
                    data=body,
                    headers={**headers, "Origin": "https://attacker.invalid"},
                )
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_mcp_stdio_round_trip_and_argument_validation(app, project):
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
        },
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "awb_submit",
                "arguments": {
                    "text": "Saved via MCP",
                    "client_key": "mcp",
                    "project": project["id"],
                },
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "awb_submit", "arguments": {"text": "No key"}},
        },
    ]
    sink = io.StringIO()
    serve_stdio(app.home, io.StringIO("\n".join(json.dumps(m) for m in messages)), sink)
    replies = [json.loads(l) for l in sink.getvalue().splitlines()]
    assert replies[0]["result"]["serverInfo"]["name"] == "assistant-workbench"
    assert len(replies[1]["result"]["tools"]) >= 5
    assert not replies[2]["result"]["isError"] and "error" in replies[3]


def test_cli_exit_codes_and_restart(tmp_path):
    prefix = [
        sys.executable,
        "-m",
        "assistant_workbench",
        "--home",
        str(tmp_path / "state"),
    ]
    assert subprocess.run([*prefix, "init"], capture_output=True).returncode == 0
    assert (
        subprocess.run(
            [*prefix, "request", '{"action":"does.not.exist"}'], capture_output=True
        ).returncode
        == 2
    )
    result = subprocess.run(
        [*prefix, "submit", "Durable CLI prompt", "--key", "stable"],
        capture_output=True,
        text=True,
        check=True,
    )
    result2 = subprocess.run(
        [*prefix, "submit", "Durable CLI prompt", "--key", "stable"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert (
        json.loads(result.stdout)["request_id"]
        == json.loads(result2.stdout)["request_id"]
    )


def test_process_timeout_output_and_no_shell_interpolation(tmp_path):
    with pytest.raises(WorkbenchError, match="timed out"):
        run_process([sys.executable, "-c", "import time;time.sleep(20)"], timeout=0.1)
    result = run_process(
        [sys.executable, "-c", "import sys;print(sys.argv[1])", "$(touch NEVER)"]
    )
    assert "$(touch NEVER)" in result["stdout"]
    with pytest.raises(WorkbenchError):
        run_process([sys.executable, "-c", "raise SystemExit(7)"])


def test_named_integrations_never_silently_use_subscription_as_api(app, project):
    p = app.home / "config.json"
    config = json.loads(p.read_text())
    config["integrations"] = {
        "gpt-researcher": {
            "enabled": True,
            "command": [sys.executable],
            "auth": "subscription",
        },
        "open-notebook": {
            "enabled": True,
            "auth": "subscription",
            "notebooks": {project["id"]: "notebook:one"},
        },
    }
    p.write_text(json.dumps(config))
    with pytest.raises(WorkbenchError, match="subscription"):
        Integrations(app.store).researcher(project["id"], "Question")
    with pytest.raises(WorkbenchError, match="subscription"):
        Integrations(app.store).notebook(project["id"], "ask", query="Question")


def test_export_escapes_untrusted_content(app, project, tmp_path):
    r = app.store.submit("Report <script>bad</script>", "a", project["id"])[
        "request_id"
    ]
    Outcomes(app.store).propose(
        r, "<img src=x onerror=evil()>\nClear answer.", [], "writer", 1
    )
    result = export_report(app.store, r, tmp_path / "report")
    page = Path(result["html"]).read_text()
    assert "<script>bad" not in page and "<img src=x" not in page and "&lt;img" in page
    assert "Content-Security-Policy" in page and result["status"] == "needs_review"
    with pytest.raises(FileExistsError):
        export_report(app.store, r, tmp_path / "report")


@pytest.mark.parametrize(
    "sequence",
    [
        "\x1b]52;c;clipboard\x07danger",
        "\x1b[31mred",
        "\x1b]8;;https://bad\x1b\\link\x1b]8;;\x1b\\",
        "a\x00b",
    ],
)
def test_terminal_controls_are_removed(sequence):
    text = safe_text(sequence)
    assert "\x1b" not in text and "\x00" not in text


@pytest.mark.parametrize("size", [(80, 24), (120, 36), (160, 48)])
async def test_tui_submit_outcomes_and_log_flood(app, project, size):
    r = app.store.submit("Persistent report", "a", project["id"])["request_id"]
    out = Outcomes(app.store).propose(
        r,
        "Answer remains visible.\n\nEvidence is still awaiting review.",
        [],
        "writer",
        1,
    )
    ui = WorkbenchApp(app.home)
    async with ui.run_test(size=size) as pilot:
        await pilot.pause()
        ui.query_one(TabbedContent).active = "outcomes"
        await pilot.pause()
        table = ui.query_one("#outcomes-table", DataTable)
        table.focus()
        await pilot.press("enter")
        await pilot.pause()
        selected = ui.selected
        assert selected == ("outcomes", out["id"])
        before = str(ui.query_one("#details", Static).content)
        with app.store.transaction() as db:
            for i in range(250):
                app.store.event(db, "tool.log", str(i), {"detail": "Routine trace"})
        ui.action_refresh()
        await pilot.pause()
        assert (
            ui.selected == selected
            and str(ui.query_one("#details", Static).content) == before
        )
        ui.action_read()
        ui.action_pin()
        assert app.store.one("SELECT * FROM outcomes")["is_read"] == 1
        ui.query_one(TabbedContent).active = "inbox"
        ui.action_compose()
        ui.query_one("#prompt", Input).value = "Another Cedar prompt"
        await pilot.press("enter")
        await pilot.pause()
        assert len(app.store.rows("SELECT * FROM prompts")) == 2


def test_mcp_malformed_request_recovers_for_next_request(app):
    sink = io.StringIO()
    serve_stdio(
        app.home, io.StringIO('[]\n{"jsonrpc":"2.0","id":2,"method":"ping"}\n'), sink
    )
    rows = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert "error" in rows[0] and rows[1]["result"] == {}


def test_fast_process_output_overflow_is_not_silently_truncated():
    with pytest.raises(WorkbenchError, match="output exceeded"):
        run_process([sys.executable, "-c", 'print("x"*200000)'], max_output=1000)

#!/usr/bin/env python3
"""Opt-in authenticated busy-intake test; owned temporary coordinator only.
OLYMPUS_PARALLEL_LIVE=1 OLYMPUS_PI_EXECUTABLE=/account-aware/pi
OLYMPUS_TEST_BIN=/olympus OLYMPUS_INTAKE_TEST_MODEL=provider/model python3 tests/olympus-parallel-live.py
"""
import json
import os
from pathlib import Path
import queue
import shutil
import signal
import subprocess
import tempfile
import threading
import time

if os.environ.get("OLYMPUS_PARALLEL_LIVE") != "1":
    print("SKIP: set OLYMPUS_PARALLEL_LIVE=1 with authenticated Pi")
    raise SystemExit(0)
exe = os.environ.get("OLYMPUS_TEST_BIN") or shutil.which("olympus")
pi = os.environ.get("OLYMPUS_PI_EXECUTABLE") or shutil.which("pi")
model = os.environ["OLYMPUS_INTAKE_TEST_MODEL"]
extension = Path(__file__).resolve().parents[1] / "packages/olympus-pi/index.ts"
with tempfile.TemporaryDirectory(prefix="olympus-parallel-", dir="/tmp") as area:
    home = Path(area) / "home"

    def call(*args):
        result = subprocess.run([exe, "--home", str(home), *args], capture_output=True, text=True, check=True, timeout=30)
        return json.loads(result.stdout) if result.stdout.strip() else None

    call("init")
    call("start")
    process = None
    try:
        chief = call("request", json.dumps({"action": "agent.create", "role": "chief", "harness": "pi"}))
        env = dict(os.environ, OLYMPUS_HOME=str(home), OLYMPUS_BIN=exe,
                   OLYMPUS_CHIEF_MODE="1", OLYMPUS_AGENT_TOKEN_FILE=chief["credential_file"],
                   OLYMPUS_PARALLEL_INTAKE="1")
        process = subprocess.Popen([pi, "--mode", "rpc", "--no-session", "--model", model,
                                    "--thinking", "low", "-e", str(extension)], cwd=home, env=env,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
        events = queue.Queue()

        def reader():
            for line in iter(process.stdout.readline, b""):
                try:
                    events.put(json.loads(line))
                except ValueError:
                    pass

        threading.Thread(target=reader, daemon=True).start()
        threading.Thread(target=lambda: process.stderr.read(), daemon=True).start()

        def send(request):
            process.stdin.write((json.dumps(request) + "\n").encode())
            process.stdin.flush()

        send({"id": "hold", "type": "prompt", "message": "Authorized isolated intake test: run bash sleep 20, then reply TEST_DONE. Do not create, execute or delegate tasks."})
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                event = events.get(timeout=2)
            except queue.Empty:
                continue
            if event.get("type") == "tool_execution_start" and event.get("toolName") == "bash":
                break
        else:
            raise AssertionError("Chief never entered the held tool")
        started = time.monotonic()
        send({"id": "first", "type": "prompt", "message": "/request Intake fixture alpha; record only, do not execute"})
        send({"id": "second", "type": "prompt", "message": "Intake fixture beta; record only, do not execute"})
        receipts = set()
        settled = False
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and len(receipts) < 2:
            try:
                event = events.get(timeout=1)
            except queue.Empty:
                continue
            if event.get("type") == "agent_settled":
                settled = True
            if event.get("type") == "response" and event.get("id") in ("first", "second"):
                assert event.get("success"), "Intake prompt failed"
                receipts.add(event["id"])
        assert len(receipts) == 2 and not settled, "Requests waited for the current run"
        assert len(call("status")["tasks"]) >= 2, "Intake tasks not persisted"
        print("PASS: slash and ordinary-text intake persisted while chief tool active (%.2fs)" % (time.monotonic() - started))
        send({"id": "abort", "type": "abort"})
        time.sleep(0.5)
    finally:
        if process and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        call("stop")

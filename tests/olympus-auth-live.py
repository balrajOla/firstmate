"""Opt-in real account startup guard; no model prompts and no credential output."""
import json
import os
from pathlib import Path
import queue
import shlex
import signal
import subprocess
import threading
import time

shell = os.environ.get("WORKSTATION_SHELL", os.environ.get("SHELL", "/bin/bash"))
# Reproduce a fresh desktop login, not the coding agent's authenticated process.
environment = {key: value for key, value in os.environ.items()
               if key in ("HOME", "USER", "LOGNAME", "TMPDIR", "TERM", "SHELL", "LANG")}
environment["PATH"] = os.defpath + ":/usr/local/bin"
name = os.environ.get("BALRAJ_WORK_COMMAND", "balraj-work")
resolved = subprocess.run([shell, "-lic", "command -v " + shlex.quote(name)],
                          env=environment, capture_output=True, text=True, check=True).stdout.strip().splitlines()[-1]
assert Path(resolved).is_file(), "Login shell did not resolve an executable workstation command"
cases = [("login-shell PATH", name), ("resolved executable", resolved)]
wrapper = Path.home() / ".local/bin/balraj-work"
if wrapper.is_file() and str(wrapper) != resolved:
    cases.append(("user wrapper", str(wrapper)))

for label, command in cases:
    process = subprocess.Popen([shell, "-lic", "exec " + shlex.quote(command) + " -- --mode rpc --no-session"],
                               env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True)
    responses = queue.Queue()
    def read_records(stream=process.stdout, target=responses):
        for line in iter(stream.readline, b""):
            try:
                target.put(json.loads(line))
            except ValueError:
                pass  # service startup messages precede the reserved RPC stream
    # Drain diagnostics but never print them: a provider may include private paths.
    reader = threading.Thread(target=read_records, daemon=True)
    diagnostics = threading.Thread(target=lambda stream: stream.read(), args=(process.stderr,), daemon=True)
    reader.start(); diagnostics.start()
    try:
        for kind in ("get_state", "get_available_models", "get_commands"):
            process.stdin.write((json.dumps({"id": kind, "type": kind}) + "\n").encode())
        process.stdin.flush()
        result = {}; deadline = time.monotonic() + 60
        while len(result) < 3 and time.monotonic() < deadline:
            try:
                record = responses.get(timeout=1)
            except queue.Empty:
                if process.poll() is not None:
                    break
                continue
            if record.get("type") == "response" and record.get("id") in ("get_state", "get_available_models", "get_commands"):
                result[record["id"]] = record
        assert len(result) == 3, label + ": workstation startup did not return RPC inspection results"
        assert all(record.get("success") for record in result.values()), label + ": startup inspection failed"
        selected = result["get_state"]["data"].get("model") or {}
        models = result["get_available_models"]["data"]["models"]
        assert models, label + ": no authenticated models are available"
        assert any(model.get("provider") == selected.get("provider") and model.get("id") == selected.get("id")
                   for model in models), label + ": the selected model has no usable authentication"
        commands = {command["name"] for command in result["get_commands"]["data"]["commands"]}
        assert {"olympus", "olympus-panel", "olympus-focus"}.issubset(commands), label + ": workstation UI did not load"
        print("PASS " + label + ": " + selected["provider"] + "/" + selected["id"] + "; " + str(len(models)) + " available models; Olympus UI registered")
        process.stdin.close(); process.wait(timeout=15)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL); process.wait()
        reader.join(timeout=2); diagnostics.join(timeout=2)
        for stream in (process.stdout, process.stderr):
            stream.close()

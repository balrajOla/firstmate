"""One command that brings the Workbench up and opens the terminal UI.

Work runs in services, not in the terminal: closing a window loses nothing.
`awb open` starts whatever is down, waits for the bridge, then opens the view
where it was left, optionally inside a persistent herdr session.
"""

from __future__ import annotations

import json
import os
import plistlib
import shlex
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from .operations import SERVICE_LABEL
from .util import WorkbenchError, now, read_json

WORKSPACE = "Assistant Workbench"
SYSTEMD_UNIT = "assistant-workbench.service"


def manager():
    if sys.platform == "darwin":
        return "launchd"
    return "systemd" if shutil.which("systemctl") else None


class Services:
    """Starts installed user services. It never stops or restarts a running one."""

    def __init__(self, kind="auto", runner=subprocess.run, agents=None):
        # None means no service manager: the bridge then runs detached.
        self.kind = manager() if kind == "auto" else kind
        self.runner = runner
        self.agents = Path(agents or Path.home() / "Library/LaunchAgents")

    def run(self, argv):
        return self.runner(argv, capture_output=True, text=True, timeout=30)

    def ensure(self, name, home=None):
        """With a home, only a service whose definition names that home counts:
        the default bridge label may belong to another Workbench home."""
        other = {"service": name, "action": "serves another home"}
        if self.kind == "launchd":
            target = "gui/" + str(os.getuid()) + "/" + name
            shown = self.run(["launchctl", "print", target])
            if shown.returncode == 0:
                if home is not None and str(home) not in (line.strip() for line in shown.stdout.splitlines()):
                    return other
                if "state = running" in shown.stdout:
                    return {"service": name, "action": "already running"}
                # Without -k, kickstart starts a stopped job and leaves a running one alone.
                started = self.run(["launchctl", "kickstart", target])
                action = "started"
            else:
                plist = self.agents / (name + ".plist")
                if not plist.is_file():
                    return {"service": name, "action": "not installed"}
                if home is not None and str(home) not in _plist_arguments(plist):
                    return other
                started = self.run(["launchctl", "bootstrap", "gui/" + str(os.getuid()), str(plist)])
                action = "loaded"
        elif self.kind == "systemd":
            active = self.run(["systemctl", "--user", "is-active", name]).stdout.strip() == "active"
            unit = self.run(["systemctl", "--user", "cat", name])
            if unit.returncode != 0:
                return {"service": name, "action": "not installed"}
            if home is not None and '"' + str(home) + '"' not in unit.stdout:
                return other
            if active:
                return {"service": name, "action": "already running"}
            started = self.run(["systemctl", "--user", "start", name])
            action = "started"
        else:
            return {"service": name, "action": "not installed"}
        if started.returncode != 0:
            raise WorkbenchError(name + " could not be started: " + (started.stderr or started.stdout).strip()[-300:])
        return {"service": name, "action": action}


def _plist_arguments(path):
    try:
        with open(path, "rb") as file:
            arguments = plistlib.load(file).get("ProgramArguments", [])
    except (OSError, ValueError, plistlib.InvalidFileException, AttributeError):
        return []
    return arguments if isinstance(arguments, list) else []


def _free(port):
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _answers(url):
    try:
        with urllib.request.urlopen(url + "/health", timeout=2) as response:
            return response.status == 200
    except (OSError, ValueError):
        return False


def bridge_alive(home, answers=None):
    """A bridge is up when its loop wrote a heartbeat in the last 30 seconds
    and the port it recorded answers."""
    try:
        health = read_json(home / "bridge-health.json", {})
        service = read_json(home / "service.json", {})
    except WorkbenchError:
        return False
    answers = answers or _answers
    at = health.get("at") if isinstance(health, dict) else None
    url = service.get("url") if isinstance(service, dict) else None
    return isinstance(at, (int, float)) and 0 <= now() - at < 30 and isinstance(url, str) and answers(url)


def _cli(home, *args):
    # The interpreter running this command already imports this release, so
    # the bridge and the terminal UI never depend on PATH.
    return [sys.executable, "-m", "assistant_workbench.cli", "--home", str(home), *args]


def _spawn(argv, log, popen):
    with open(log, "ab") as out:
        popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True, close_fds=True)


def _wait(check, seconds, sleep):
    deadline = time.monotonic() + seconds
    while not check():
        if time.monotonic() >= deadline:
            return False
        sleep(0.5)
    return True


def open_workbench(home, settings, wait=30, services=None, popen=None,
                   sleep=time.sleep, answers=None, coordinator=None, free=None):
    """Start what is down and report what is up."""
    home = Path(home)
    popen, answers, free = popen or subprocess.Popen, answers or _answers, free or _free
    opened = settings.get("open", {})
    services = services or Services()
    steps = [services.ensure(name) for name in opened.get("services", [])]
    alive = lambda: bridge_alive(home, answers)
    if alive():
        steps.append({"service": "bridge", "action": "already running"})
    else:
        default = SYSTEMD_UNIT if services.kind == "systemd" else SERVICE_LABEL
        step = services.ensure(opened.get("bridge_service", default), home.resolve())
        if step["action"] in {"not installed", "serves another home"}:
            # The documented port when it is free; otherwise any free one,
            # which the bridge records in service.json.
            port = opened.get("port") or (8765 if free(8765) else 0)
            _spawn(_cli(home, "serve", "--port", str(port)), home / "service.stderr.log", popen)
            step = {"service": "bridge", "action": "started in the background",
                    "note": "Install the bridge as a service so it also starts at login"}
        steps.append(step)
        if not _wait(alive, wait, sleep):
            raise WorkbenchError("The bridge did not report healthy within " + str(wait)
                                 + " seconds; see " + str(home / "service.stderr.log"))
    result = {"home": str(home), "steps": steps, "bridge": read_json(home / "bridge-health.json", {})}
    if coordinator is not None:
        reached = _wait(coordinator, wait, sleep)
        result["coordinator"] = "reachable" if reached else (
            "not reachable yet; saved requests are dispatched once it is")
    return result


def _herdr_sessions(herdr, runner):
    listed = runner([herdr, "session", "list"], capture_output=True, text=True, timeout=20)
    rows = [line.split() for line in listed.stdout.splitlines()[1:]]
    return {row[0]: row[1] for row in rows if len(row) >= 2}


def herdr_session(home, name, runner=subprocess.run, popen=subprocess.Popen,
                  sleep=time.sleep, execv=os.execv, which=shutil.which):
    """Attach to a persistent herdr session whose workspace runs the terminal UI.

    The session outlives the terminal window. Quitting the UI closes its
    workspace, and the next open creates it again.
    """
    herdr = which("herdr")
    if not herdr:
        raise WorkbenchError("herdr is not on PATH; install it from herdr.dev or open without --session")
    home = Path(home)

    def call(*args):
        done = runner([herdr, "--session", name, *args], capture_output=True, text=True, timeout=20)
        if done.returncode != 0:
            raise WorkbenchError("herdr " + " ".join(args[:2]) + " failed: " + (done.stderr or done.stdout).strip()[-300:])
        return json.loads(done.stdout).get("result", {}) if done.stdout.strip() else {}

    if _herdr_sessions(herdr, runner).get(name) != "running":
        _spawn([herdr, "--session", name, "server"], home / "herdr-session.log", popen)
        if not _wait(lambda: _herdr_sessions(herdr, runner).get(name) == "running", 20, sleep):
            raise WorkbenchError("herdr session " + name + " did not start; see " + str(home / "herdr-session.log"))
    current = next((w for w in call("workspace", "list")["workspaces"] if w.get("label") == WORKSPACE), None)
    if current:
        call("workspace", "focus", current["workspace_id"])
    else:
        created = call("workspace", "create", "--label", WORKSPACE, "--cwd", str(home), "--focus")
        call("pane", "run", created["root_pane"]["pane_id"], "exec " + shlex.join(_cli(home, "tui")))
    execv(herdr, [herdr, "--session", name])

"""`awb open`: starting what is down without touching what runs, and the
persistent herdr session. Service managers and herdr are scripted here."""

import json
import os
import plistlib
import subprocess
import sys

import pytest
from assistant_workbench import cli, launcher
from assistant_workbench.config import config
from assistant_workbench.util import WorkbenchError, canonical, now


class Done:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class Script:
    """Answers commands by prefix and records every call."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        for prefix, answer in self.answers.items():
            if tuple(argv[: len(prefix)]) == prefix:
                return answer(argv) if callable(answer) else answer
        return Done(1, stderr="unexpected " + " ".join(argv))


def heartbeat(home, at=None):
    (home / "bridge-health.json").write_text(canonical({"status": "running", "at": at or now()}))
    (home / "service.json").write_text(canonical({"url": "http://127.0.0.1:8765"}))


class Spawned:
    def __init__(self, on_spawn=None):
        self.argv = []
        self.kwargs = []
        self.on_spawn = on_spawn

    def __call__(self, argv, **kwargs):
        self.argv.append(argv)
        self.kwargs.append(kwargs)
        if self.on_spawn:
            self.on_spawn()


def uid_target(name):
    return "gui/" + str(os.getuid()) + "/" + name


def test_a_running_bridge_is_left_alone(app):
    heartbeat(app.home)
    runner, popen = Script({}), Spawned()
    result = launcher.open_workbench(app.home, config(app.home), services=launcher.Services("launchd", runner),
                                     popen=popen, answers=lambda url: True, sleep=lambda s: None)
    assert result["steps"] == [{"service": "bridge", "action": "already running"}]
    assert runner.calls == [] and popen.argv == []


def test_launchd_services_are_loaded_or_started_but_never_restarted(app, tmp_path):
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    (agents / "org.example.coordinator.plist").write_text("<plist/>")
    (agents / "com.assistant-workbench.bridge.plist").write_bytes(plistlib.dumps(
        {"ProgramArguments": ["/opt/awb", "--home", str(app.home.resolve()), "serve"]}))
    loaded = set()
    runner = Script({
        ("launchctl", "print", uid_target("org.example.running")): Done(0, "state = running\n"),
        ("launchctl", "print", uid_target("org.example.stopped")): Done(0, "state = not running\n"),
        ("launchctl", "print"): lambda argv: Done(0 if argv[2] in loaded else 113, "state = running\n"),
        ("launchctl", "kickstart"): Done(),
        ("launchctl", "bootstrap"): lambda argv: loaded.add(uid_target(os.path.basename(argv[3])[:-6])) or (
            heartbeat(app.home) if "bridge" in argv[3] else None) or Done(),
    })
    settings = {**config(app.home), "open": {"services": ["org.example.running", "org.example.stopped",
                                                          "org.example.coordinator", "org.example.absent"]}}
    result = launcher.open_workbench(app.home, settings, services=launcher.Services("launchd", runner, agents),
                                     popen=Spawned(), answers=lambda url: True, sleep=lambda s: None)
    assert [s["action"] for s in result["steps"]] == ["already running", "started", "loaded", "not installed", "loaded"]
    kicks = [c for c in runner.calls if c[1] == "kickstart"]
    assert kicks == [["launchctl", "kickstart", uid_target("org.example.stopped")]]
    # Nothing running is killed, restarted or unloaded.
    assert not any(c[1] in {"bootout", "stop", "remove"} or "-k" in c for c in runner.calls)


def test_without_a_service_the_bridge_starts_detached_from_the_terminal(app):
    popen = Spawned(on_spawn=lambda: heartbeat(app.home))
    settings = {**config(app.home), "open": {"port": 8799}}
    result = launcher.open_workbench(app.home, settings, services=launcher.Services(None),
                                     popen=popen, answers=lambda url: True, sleep=lambda s: None)
    assert result["steps"][-1]["action"] == "started in the background"
    assert popen.argv == [[sys.executable, "-m", "assistant_workbench.cli", "--home", str(app.home),
                           "serve", "--port", "8799"]]
    # Its own session, so closing the window does not end it.
    assert popen.kwargs[0]["start_new_session"] and popen.kwargs[0]["stdin"] is subprocess.DEVNULL


def test_a_bridge_service_for_another_home_is_not_used(app, tmp_path):
    """The default label belongs to the first home on this machine."""
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    (agents / "com.assistant-workbench.bridge.plist").write_bytes(plistlib.dumps(
        {"ProgramArguments": ["/opt/awb", "--home", "/Users/someone/first home", "serve"]}))
    other_home = "\targuments = {\n\t\t/opt/awb\n\t\t--home\n\t\t/Users/someone/first home\n\t}\nstate = running\n"
    for loaded in (True, False):
        runner = Script({("launchctl", "print"): Done(0 if loaded else 113, other_home)})
        popen = Spawned(on_spawn=lambda: heartbeat(app.home))
        # The documented port is taken by the other home's bridge.
        result = launcher.open_workbench(app.home, config(app.home), services=launcher.Services("launchd", runner, agents),
                                         popen=popen, answers=lambda url: True, sleep=lambda s: None,
                                         free=lambda port: False)
        assert result["steps"][-1]["action"] == "started in the background"
        assert popen.argv[0][-2:] == ["--port", "0"]
        assert not any(c[1] in {"kickstart", "bootstrap"} for c in runner.calls)
        (app.home / "bridge-health.json").unlink()


def test_a_stale_heartbeat_or_a_silent_port_is_not_a_running_bridge(app):
    heartbeat(app.home, at=now() - 120)
    assert not launcher.bridge_alive(app.home, lambda url: True)
    heartbeat(app.home)
    assert not launcher.bridge_alive(app.home, lambda url: False)
    (app.home / "bridge-health.json").write_text("{not json")
    assert not launcher.bridge_alive(app.home, lambda url: True)


@pytest.fixture
def clock(monkeypatch):
    """Each reading is a second later, so waits end without sleeping."""
    ticks = iter(range(10_000))
    monkeypatch.setattr(launcher.time, "monotonic", lambda: next(ticks))


def test_a_bridge_that_never_reports_healthy_is_an_error_not_a_second_bridge(app, clock):
    popen = Spawned()
    with pytest.raises(WorkbenchError, match="did not report healthy within 3 seconds"):
        launcher.open_workbench(app.home, config(app.home), wait=3, services=launcher.Services(None),
                                popen=popen, answers=lambda url: True, sleep=lambda s: None,
                                free=lambda port: True)
    assert popen.argv[0][-2:] == ["--port", "8765"]
    assert len(popen.argv) == 1


def test_systemd_units_start_but_another_homes_bridge_unit_is_left_alone(app):
    runner = Script({
        ("systemctl", "--user", "is-active", "coordinator.service"): Done(3, "inactive\n"),
        ("systemctl", "--user", "cat", "coordinator.service"): Done(0, "[Service]\n"),
        ("systemctl", "--user", "start", "coordinator.service"): Done(),
        ("systemctl", "--user", "is-active"): Done(4, "inactive\n"),
        ("systemctl", "--user", "cat", launcher.SYSTEMD_UNIT): Done(0, 'ExecStart="/opt/awb" --home "/srv/other"\n'),
    })
    settings = {**config(app.home), "open": {"services": ["coordinator.service"]}}
    popen = Spawned(on_spawn=lambda: heartbeat(app.home))
    result = launcher.open_workbench(app.home, settings, services=launcher.Services("systemd", runner),
                                     popen=popen, answers=lambda url: True, sleep=lambda s: None)
    assert [s["action"] for s in result["steps"]] == ["started", "started in the background"]
    # The default unit exists but runs another home, so it is not started.
    assert ["systemctl", "--user", "start", launcher.SYSTEMD_UNIT] not in runner.calls
    assert len(popen.argv) == 1


def test_a_service_that_refuses_to_start_is_reported(app):
    runner = Script({("launchctl", "print"): Done(0, "state = not running\n"),
                     ("launchctl", "kickstart"): Done(5, stderr="Operation not permitted")})
    settings = {**config(app.home), "open": {"services": ["org.example.denied"]}}
    with pytest.raises(WorkbenchError, match="org.example.denied could not be started: Operation not permitted"):
        launcher.open_workbench(app.home, settings, services=launcher.Services("launchd", runner),
                                popen=Spawned(), answers=lambda url: True, sleep=lambda s: None)


def test_an_unreachable_coordinator_is_reported_without_blocking_the_ui(app, clock):
    heartbeat(app.home)
    result = launcher.open_workbench(app.home, config(app.home), wait=2, services=launcher.Services(None),
                                     answers=lambda url: True, sleep=lambda s: None, coordinator=lambda: False)
    assert result["coordinator"].startswith("not reachable yet")


def herdr_runner(sessions, workspaces, created_pane="w1:p1"):
    def listing(argv):
        rows = "".join(f"{name} {state} /d /s\n" for name, state in sessions.items())
        return Done(0, "name status directory socket\n" + rows)

    def server(argv):
        sessions["workbench"] = "running"
        return None

    return Script({
        ("/opt/bin/herdr", "session", "list"): listing,
        ("/opt/bin/herdr", "--session", "workbench", "workspace", "list"): Done(0, json.dumps(
            {"result": {"workspaces": workspaces}})),
        ("/opt/bin/herdr", "--session", "workbench", "workspace", "create"): Done(0, json.dumps(
            {"result": {"root_pane": {"pane_id": created_pane}}})),
        ("/opt/bin/herdr", "--session", "workbench", "workspace", "focus"): Done(0, "{}"),
        ("/opt/bin/herdr", "--session", "workbench", "pane", "run"): Done(0, ""),
    }), server


def test_herdr_session_is_started_once_and_runs_the_ui_in_its_own_workspace(tmp_path):
    home = tmp_path / "state with spaces"
    home.mkdir()
    sessions = {"default": "running"}
    runner, start = herdr_runner(sessions, [])
    popen = Spawned(on_spawn=lambda: start(None))
    attached = []
    launcher.herdr_session(home, "workbench", runner=runner, popen=popen, sleep=lambda s: None,
                           execv=lambda path, argv: attached.append(argv), which=lambda name: "/opt/bin/herdr")
    assert popen.argv == [["/opt/bin/herdr", "--session", "workbench", "server"]]
    assert popen.kwargs[0]["start_new_session"]
    run = next(c for c in runner.calls if c[3:5] == ["pane", "run"])
    assert run[5] == "w1:p1" and run[6].startswith("exec ") and run[6].endswith(" tui")
    assert "'" + str(home) + "'" in run[6]
    assert attached == [["/opt/bin/herdr", "--session", "workbench"]]
    # The operator's other herdr sessions are never addressed.
    assert not any("default" in c for c in runner.calls)


def test_reopening_a_live_session_focuses_the_ui_instead_of_starting_another(tmp_path):
    sessions = {"workbench": "running"}
    runner, _ = herdr_runner(sessions, [{"workspace_id": "w3", "label": launcher.WORKSPACE}])
    popen, attached = Spawned(), []
    launcher.herdr_session(tmp_path, "workbench", runner=runner, popen=popen, sleep=lambda s: None,
                           execv=lambda path, argv: attached.append(argv), which=lambda name: "/opt/bin/herdr")
    assert popen.argv == []
    assert ["/opt/bin/herdr", "--session", "workbench", "workspace", "focus", "w3"] in runner.calls
    assert not any(c[3:5] in (["workspace", "create"], ["pane", "run"]) for c in runner.calls)
    assert attached


def test_missing_herdr_is_a_clear_error(tmp_path):
    with pytest.raises(WorkbenchError, match="herdr is not on PATH"):
        launcher.herdr_session(tmp_path, "workbench", which=lambda name: None)


@pytest.mark.parametrize("open_settings, error", [
    ({"services": ["bad name; rm"]}, "open.services"),
    ({"services": "org.example"}, "open.services"),
    ({"bridge_service": ""}, "open.bridge_service"),
    ({"port": 70000}, "open.port"),
    ({"port": True}, "open.port"),
    ({"session": "screen"}, "open.session must be"),
    ({"session_name": "two words"}, "open.session_name"),
    ({"autostart": True}, "open accepts"),
], ids=["unsafe-service", "services-not-a-list", "empty-bridge", "port-range", "port-bool",
        "unknown-session", "session-name-space", "unknown-key"])
def test_open_settings_are_validated(app, open_settings, error):
    settings = json.loads((app.home / "config.json").read_text())
    settings["open"] = open_settings
    (app.home / "config.json").write_text(json.dumps(settings))
    with pytest.raises(WorkbenchError, match=error):
        config(app.home)


def test_open_without_the_ui_reports_the_state_as_json(app, monkeypatch, capsys):
    heartbeat(app.home)
    monkeypatch.setattr(launcher, "_answers", lambda url: True)
    assert cli.main(["--home", str(app.home), "open", "--no-tui", "--wait", "1"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["steps"] == [{"service": "bridge", "action": "already running"}]
    assert "coordinator" in result


def test_inside_a_herdr_pane_open_runs_the_ui_directly(app, monkeypatch):
    heartbeat(app.home)
    monkeypatch.setattr(launcher, "_answers", lambda url: True)
    monkeypatch.setenv("HERDR_ENV", "1")
    monkeypatch.setattr(launcher, "herdr_session", lambda *a, **k: pytest.fail("nested herdr session"))
    import assistant_workbench.tui as tui

    ran = []
    monkeypatch.setattr(tui.WorkbenchApp, "run", lambda self: ran.append(self.work.home))
    assert cli.main(["--home", str(app.home), "open", "--session", "herdr", "--wait", "1"]) == 0
    assert ran == [app.home]

"""`awb setup` with scripted downloads, service managers, Paperclip and CLIs.
Real installs are checked by AWB_TEST_INSTALL_SH and the live notes."""

import hashlib
import io
import json
import os
import plistlib
import subprocess
import tarfile
from pathlib import Path

import pytest
from assistant_workbench import cli, onboarding, profiles
from assistant_workbench.config import config
from assistant_workbench.util import WorkbenchError

ROOT = Path(__file__).resolve().parents[1]


def executable(directory, name, output=""):
    path = directory / name
    path.write_text("#!/bin/sh\necho " + json.dumps(output) + "\n")
    path.chmod(0o755)
    return path


class Run:
    """Answers commands by their first words and records each call."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []

    def __call__(self, argv, env=None, **kwargs):
        self.calls.append({"argv": argv, "env": env, **kwargs})
        name = Path(argv[0]).name
        for key, answer in self.answers.items():
            if (name,) + tuple(argv[1:len(key)]) == key:
                result = answer(argv, env) if callable(answer) else answer
                return {"stdout": "", "stderr": "", "exit_code": 0, **(result or {})}
        return {"stdout": "", "stderr": "", "exit_code": 0}

    def named(self, name):
        return [c for c in self.calls if Path(c["argv"][0]).name == name]


def node_archive(platform_name, version):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        data = b"#!/bin/sh\necho v" + version.encode() + b"\n"
        info = tarfile.TarInfo("node-v" + version + "-" + platform_name + "/bin/node")
        info.size, info.mode = len(data), 0o755
        bundle.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


@pytest.fixture
def machine(app, tmp_path, monkeypatch):
    """A PATH with only the tools a test puts there, and pinned test archives."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    platform_name = onboarding._platform()
    archive = node_archive(platform_name, "24.22.2")
    rustup = b"#!/bin/sh\nexit 0\n"
    toolchain = json.loads(json.dumps(onboarding.TOOLCHAIN))
    toolchain["node"]["sha256"][platform_name] = hashlib.sha256(archive).hexdigest()
    toolchain["rustup"]["sha256"][platform_name] = hashlib.sha256(rustup).hexdigest()
    monkeypatch.setattr(onboarding, "TOOLCHAIN", toolchain)
    downloads = []

    def fetch(url):
        downloads.append(url)
        return archive if "nodejs.org" in url else rustup

    return {"bin": bin_dir, "fetch": fetch, "downloads": downloads, "platform": platform_name}


def make(app, machine, run=None, **kwargs):
    return onboarding.Setup(app.home, "/opt/awb/bin/awb", run=run or Run(), fetch=machine["fetch"],
                            progress=lambda text: None, sleep=lambda s: None, **kwargs)


def test_missing_build_tools_are_installed_under_the_home_after_their_checksums(app, machine):
    def npm_install(argv, env):
        target = Path(argv[argv.index("--prefix") + 1]) / "bin"
        target.mkdir(parents=True, exist_ok=True)
        executable(target, "pnpm", "9.15.4")

    def rustup(argv, env):
        assert env["RUSTUP_HOME"].startswith(str(app.home)) and env["CARGO_HOME"].startswith(str(app.home))
        cargo_bin = Path(env["CARGO_HOME"]) / "bin"
        cargo_bin.mkdir(parents=True)
        executable(cargo_bin, "cargo")

    run = Run({("npm", "install"): npm_install, ("rustup-init",): rustup,
               ("node", "--version"): {"stdout": "v24.22.2\n"}})
    work = make(app, machine, run)
    work.node()
    work.pnpm()
    work.rust()
    assert (app.home / "tools/node/bin/node").is_file()
    assert [u.split("/")[2] for u in machine["downloads"]] == ["nodejs.org", "static.rust-lang.org"]
    assert "/rustup/archive/1.29.3/" in machine["downloads"][1]
    rustup_call = run.named("rustup-init")[0]["argv"]
    # Paperclip's rust-toolchain.toml chooses the Rust version.
    assert rustup_call[1:] == ["-y", "--no-modify-path", "--profile", "minimal", "--default-toolchain", "none"]
    path = work.env["PATH"].split(os.pathsep)
    assert path[:3] == [str(app.home / "tools/cargo/bin"), str(app.home / "tools/bin"), str(app.home / "tools/node/bin")]
    assert [s["status"] for s in work.steps] == ["installed", "installed", "installed"]


def test_a_download_with_the_wrong_checksum_installs_nothing(app, machine, monkeypatch):
    work = make(app, machine)
    monkeypatch.setitem(onboarding.TOOLCHAIN["node"]["sha256"], machine["platform"], "0" * 64)
    with pytest.raises(WorkbenchError, match="Checksum mismatch"):
        work.node()
    assert not (app.home / "tools/node").exists()


def test_suitable_tools_on_the_path_are_used_as_they_are(app, machine):
    for name, output in (("node", "v24.19.0"), ("pnpm", "9.12.0"), ("cargo", "cargo 1.97.1")):
        executable(machine["bin"], name, output)
    run = Run({("node", "--version"): {"stdout": "v24.19.0\n"}, ("pnpm", "--version"): {"stdout": "9.12.0\n"}})
    work = make(app, machine, run)
    work.node()
    work.pnpm()
    work.rust()
    assert machine["downloads"] == [] and not run.named("npm")
    assert [s["status"] for s in work.steps] == ["found", "found", "found"]


def test_node_older_than_paperclip_needs_is_not_used(app, machine):
    executable(machine["bin"], "node", "v20.11.0")
    work = make(app, machine, Run({("node", "--version"): {"stdout": "v20.11.0\n"}}))
    work.node()
    assert work.steps[-1]["status"] == "installed" and machine["downloads"]


class Paperclip:
    """The parts of the Paperclip API setup uses."""

    def __init__(self, companies=(), healthy=True):
        self.companies = list(companies)
        self.healthy = healthy
        self.calls = []

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == "/health":
            if not self.healthy:
                raise WorkbenchError("connection refused")
            return {"status": "ok"}
        if (method, path) == ("GET", "/companies"):
            return self.companies
        if (method, path) == ("POST", "/companies"):
            self.companies.append({"id": "company-1", **body})
            return self.companies[-1]
        return {}


@pytest.fixture
def paperclip_ready(app, machine, monkeypatch):
    executable(machine["bin"], "pnpm", "9.15.4")
    source = app.home / "dependencies/paperclip/rev/source"
    source.mkdir(parents=True)
    monkeypatch.setattr("assistant_workbench.operations.install_dependencies",
                        lambda home, names: [{"path": str(source.parent)}])
    return source


def test_paperclip_is_onboarded_once_served_and_given_a_company(app, machine, paperclip_ready, tmp_path):
    api = Paperclip(healthy=False)
    started = []

    def popen(argv, **kwargs):
        # Onboarding writes its config and starts serving.
        started.append((argv, kwargs))
        config_file = app.home / "paperclip/instances/awb/config.json"
        config_file.parent.mkdir(parents=True)
        config_file.write_text("{}")
        api.healthy = True
        return subprocess.Popen(["/bin/sleep", "30"], start_new_session=True)

    run = Run()
    agents = tmp_path / "LaunchAgents"
    work = make(app, machine, run, api=api, popen=popen, manager="launchd", agents_dir=agents)
    work.paperclip("org.example.paperclip", 3199)
    argv, kwargs = started[0]
    assert argv[1:] == ["paperclipai", "onboard", "--yes", "--no-install-service"]
    assert kwargs["start_new_session"] and kwargs["env"]["PORT"] == "3199"
    assert kwargs["env"]["PAPERCLIP_HOME"] == str(app.home / "paperclip")
    definition = plistlib.loads((agents / "org.example.paperclip.plist").read_bytes())
    assert definition["ProgramArguments"][1:] == ["paperclipai", "run"]
    env = definition["EnvironmentVariables"]
    assert env["PORT"] == "3199" and env["PAPERCLIP_TELEMETRY_DISABLED"] == "1" and env["DO_NOT_TRACK"] == "1"
    assert not any(k.endswith(("_KEY", "_TOKEN")) for k in env)
    assert ["launchctl", "bootstrap", "gui/" + str(os.getuid()), str(agents / "org.example.paperclip.plist")] in [
        c["argv"] for c in run.calls]
    assert ("POST", "/companies", {"name": "Assistant Workbench"}) in api.calls
    assert ("PATCH", "/instance/settings/experimental", {"enableIsolatedWorkspaces": True}) in api.calls
    coordinator = config(app.home)["coordinator"]
    assert coordinator["url"] == "http://127.0.0.1:3199" and coordinator["company_id"] == "company-1"

    # A rerun keeps the onboarding, the company and the unchanged service.
    api.calls.clear()
    before = len(run.calls)
    work.paperclip("org.example.paperclip", 3199)
    assert len(started) == 1 and not any(c[0] == "POST" for c in api.calls)
    assert [c["argv"][1] for c in run.calls[before:]] == ["bootstrap"]


def test_another_server_on_the_paperclip_port_stops_setup(app, machine, paperclip_ready):
    work = make(app, machine, api=Paperclip(healthy=True), popen=lambda *a, **k: pytest.fail("onboarded"))
    with pytest.raises(WorkbenchError, match="already answers on the Paperclip port"):
        work.paperclip("org.example.paperclip", 3100)


def test_a_service_file_of_another_installation_is_never_replaced(app, machine, tmp_path):
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    theirs = agents / "org.example.paperclip.plist"
    theirs.write_text("<plist>someone else's home</plist>")
    work = make(app, machine, manager="launchd", agents_dir=agents)
    with pytest.raises(WorkbenchError, match="belongs to another installation"):
        work.service("org.example.paperclip", ["/bin/true"], {}, tmp_path, tmp_path / "log")
    assert theirs.read_text() == "<plist>someone else's home</plist>"


def test_systemd_units_are_written_and_enabled(app, machine, tmp_path):
    run = Run()
    units = tmp_path / "units"
    work = make(app, machine, run, manager="systemd", units_dir=units)
    work.service("org.example.paperclip", ["/opt/pnpm", "paperclipai", "run"], {"PORT": "3100"}, tmp_path,
                 tmp_path / "log")
    unit = (units / "org.example.paperclip.service").read_text()
    assert 'Environment="PORT=3100"' in unit and "ExecStart=/opt/pnpm paperclipai run" in unit
    assert [c["argv"] for c in run.calls] == [["systemctl", "--user", "daemon-reload"],
                                             ["systemctl", "--user", "enable", "--now", "org.example.paperclip.service"]]


def test_signed_in_clis_become_checked_profiles(app, machine, monkeypatch, tmp_path):
    executable(machine["bin"], "claude")
    executable(machine["bin"], "codex")
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text('model = "model-from-codex-config"\n')
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    probed = []

    def probe(home, identity):
        probed.append(identity)
        if identity == "codex":
            raise WorkbenchError("CLI did not return the required canary marker")
        return {"result": "passed"}

    monkeypatch.setattr(profiles, "probe", probe)
    work = make(app, machine)
    ready = work.agents("sonnet", None, "subscription", "subscription", probe=True, semantic=True)
    models = {m["id"]: m for m in config(app.home)["models"]}
    assert models["claude"]["model"] == "sonnet" and models["claude"]["auth"] == "subscription"
    assert models["codex"]["model"] == "model-from-codex-config"
    assert probed == ["claude", "codex"] and ready == ["claude"]
    statuses = {s["step"]: s["status"] for s in work.steps}
    # Paperclip needs Codex's sign-in as a file; the profile says what to run.
    assert statuses["codex sign-in"] == "not usable by Paperclip"
    assert statuses["codex profile"] == "not ready" and statuses["Semantic routing"] == "on"
    assert config(app.home)["routing"]["semantic"] == {"profile": "claude", "min_confidence": 0.7,
                                                       "create_projects": True}


def test_missing_clis_and_models_are_reported_not_invented(app, machine, monkeypatch, tmp_path):
    executable(machine["bin"], "codex")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "empty"))
    monkeypatch.setattr(profiles, "probe", lambda home, identity: pytest.fail("probed"))
    work = make(app, machine)
    assert work.agents("sonnet", None, "subscription", "subscription", probe=True, semantic=True) == []
    statuses = {s["step"]: s["status"] for s in work.steps}
    assert statuses == {"claude CLI": "not found", "codex CLI": "skipped"}
    assert config(app.home)["models"] == [] and "routing" not in config(app.home)


def test_setup_installs_the_bridge_and_records_what_awb_open_starts(app, machine, monkeypatch, tmp_path):
    awb = executable(machine["bin"], "awb")
    agents = tmp_path / "LaunchAgents"
    run = Run()
    result = onboarding.setup(app.home, str(awb), "org.example", bridge_port=8799, paperclip=False, agents=False,
                              wezterm_config=False, run=run, manager="launchd", agents_dir=agents,
                              progress=lambda text: None)
    definition = plistlib.loads((agents / "org.example.bridge.plist").read_bytes())
    assert definition["ProgramArguments"] == [str(awb.resolve()), "--home", str(app.home.resolve()), "serve",
                                              "--port", "8799"]
    opened = config(app.home)["open"]
    assert opened == {"bridge_service": "org.example.bridge", "port": 8799}
    assert result["next"] == "awb open" and result["steps"][-1]["step"] == "Bridge service"


def test_wezterm_is_optional(app, machine, monkeypatch, tmp_path):
    monkeypatch.setattr(onboarding.Path, "exists", lambda self: False if str(self) == "/Applications/WezTerm.app"
                        else os.path.exists(self))
    work = make(app, machine)
    work.terminal(tmp_path / "wezterm.lua")
    assert work.steps[-1]["status"] == "not installed" and not (tmp_path / "wezterm.lua").exists()


def test_the_services_run_the_prefix_launcher_not_a_release(tmp_path):
    prefix = tmp_path / "my prefix"
    python = prefix / "lib/assistant-workbench/releases/abc123/venv/bin/python"
    python.parent.mkdir(parents=True)
    (prefix / "bin").mkdir()
    (prefix / "bin/awb").write_text("#!/bin/sh\n# assistant-workbench managed launcher\n")
    assert onboarding.managed_launcher(str(python)) == str(prefix / "bin/awb")
    assert onboarding.managed_launcher("/usr/bin/python3") is None


def test_setup_cli_reports_each_step(app, machine, monkeypatch, capsys):
    awb = executable(machine["bin"], "awb")
    assert cli.main(["--home", str(app.home), "setup", "--executable", str(awb), "--no-paperclip", "--no-agents",
                     "--no-services", "--no-terminal"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["home"] == str(app.home) and result["steps"] == [] and result["next"] == "awb open"


def test_install_script_is_valid_posix_shell_and_never_uses_sudo():
    script = (ROOT / "install.sh").read_text()
    subprocess.run(["sh", "-n", str(ROOT / "install.sh")], check=True)
    assert "sudo" not in script.replace("No sudo", "")
    # uv, when needed, comes from a pinned release.
    assert 'UV_VERSION="0.13.7"' in script and 'astral.sh/uv/$UV_VERSION/install.sh' in script


@pytest.mark.skipif(not os.environ.get("AWB_TEST_INSTALL_SH"), reason="Set AWB_TEST_INSTALL_SH=1 to build a real release")
def test_install_script_builds_a_release_and_runs_setup(tmp_path):
    prefix, bin_dir, home = tmp_path / "prefix dir", tmp_path / "bin", tmp_path / "state"
    done = subprocess.run(["sh", str(ROOT / "install.sh"), "--prefix", str(prefix), "--bin-dir", str(bin_dir),
                           "--home", str(home), "--no-paperclip", "--no-agents", "--no-services", "--no-terminal"],
                          capture_output=True, text=True, timeout=900)
    assert done.returncode == 0, done.stderr[-2000:]
    assert json.loads(done.stdout)["next"] == "awb open"
    assert os.readlink(bin_dir / "awb") == str(prefix / "bin/awb")
    assert (home / "config.json").is_file()

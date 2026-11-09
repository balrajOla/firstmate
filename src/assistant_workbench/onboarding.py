"""`awb setup`: everything after the package is installed, as one command.

It installs only open-source software (Node.js, pnpm, rustup and Paperclip)
under the Workbench home when the machine lacks a suitable copy, uses the
agent CLIs you are already signed in to, and never reads, copies or stores
their credentials. Each step is skipped when it is already done, so setup can
be rerun after a failure or an upgrade.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import plistlib
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import tomllib
import urllib.request
from pathlib import Path
from .config import config
from .integrations import run_process
from .util import WorkbenchError, atomic, canonical

TOOLCHAIN = json.loads((Path(__file__).parent / "assets/toolchain.json").read_text())
CLASSES = ["simple", "code", "design", "report", "research"]
COMPANY = "Assistant Workbench"


def _platform():
    system = {"Darwin": "darwin", "Linux": "linux"}.get(platform.system())
    machine = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "amd64": "x64"}.get(platform.machine().lower())
    if not system or not machine:
        raise WorkbenchError("Setup supports macOS and Linux on arm64 or x86_64")
    return system + "-" + machine


def _fetch(url):
    with urllib.request.urlopen(url, timeout=600) as response:
        return response.read()


def _download(url, sha256, fetch):
    data = fetch(url)
    if hashlib.sha256(data).hexdigest() != sha256:
        raise WorkbenchError("Checksum mismatch for " + url + "; nothing was installed")
    return data


def _version(text):
    digits = text.strip().lstrip("v").split(".")
    try:
        return tuple(int(d) for d in digits[:2])
    except ValueError:
        return (0, 0)


class Setup:
    def __init__(self, home, executable, run=run_process, fetch=_fetch, which=shutil.which,
                 api=None, manager=None, agents_dir=None, units_dir=None, sleep=time.sleep,
                 progress=None, popen=subprocess.Popen):
        self.home = Path(home)
        self.executable = executable
        self.run, self.fetch, self.which, self.sleep, self.popen = run, fetch, which, sleep, popen
        self.api = api
        self.manager = manager or ("launchd" if sys.platform == "darwin" else "systemd")
        self.agents_dir = Path(agents_dir or Path.home() / "Library/LaunchAgents")
        self.units_dir = Path(units_dir or Path.home() / ".config/systemd/user")
        self.tools = self.home / "tools"
        self.env = dict(os.environ)
        self.steps = []
        self.progress = progress or (lambda text: print("==> " + text, file=sys.stderr, flush=True))

    # Results -------------------------------------------------------------

    def step(self, name, status, detail=""):
        self.steps.append({"step": name, "status": status, "detail": detail})
        self.progress(name + ": " + status + (" (" + detail + ")" if detail else ""))

    def check(self, argv, **kwargs):
        result = self.run(argv, env=self.env, allowed_codes=range(-255, 256), **kwargs)
        if result["exit_code"]:
            raise WorkbenchError(" ".join(argv[:2]) + " failed: "
                                 + (result["stderr"] or result["stdout"]).strip()[-400:])
        return result

    def path(self, directory):
        self.env["PATH"] = str(directory) + os.pathsep + self.env.get("PATH", "")

    def found(self, name):
        return self.which(name, path=self.env.get("PATH"))

    # Build tools ---------------------------------------------------------

    def node(self):
        spec = TOOLCHAIN["node"]
        node = self.found("node")
        if node and _version(self.check([node, "--version"])["stdout"]) >= tuple(spec["minimum"]):
            return self.step("Node.js", "found", node)
        bundled = self.tools / "node/bin"
        if not (bundled / "node").exists():
            target = _platform()
            name = "node-v" + spec["version"] + "-" + target
            self.progress("Downloading Node.js " + spec["version"])
            data = _download(spec["url"].format(version=spec["version"], platform=target),
                             spec["sha256"][target], self.fetch)
            self.tools.mkdir(parents=True, exist_ok=True)
            archive = self.tools / (name + ".tar.gz")
            archive.write_bytes(data)
            with tarfile.open(archive) as bundle:
                if hasattr(tarfile, "data_filter"):
                    bundle.extractall(self.tools, filter="data")
                else:  # Python before 3.11.4; the archive's checksum was verified
                    bundle.extractall(self.tools)
            archive.unlink()
            (self.tools / name).rename(self.tools / "node")
        self.path(bundled)
        self.step("Node.js", "installed", str(bundled / "node"))

    def pnpm(self):
        wanted = TOOLCHAIN["pnpm"]["version"]
        pnpm = self.found("pnpm")
        if pnpm and _version(self.check([pnpm, "--version"])["stdout"])[0] == _version(wanted)[0]:
            return self.step("pnpm", "found", pnpm)
        if not (self.tools / "bin/pnpm").exists():
            self.check([self.found("npm") or "npm", "install", "--global", "--prefix", str(self.tools),
                        "pnpm@" + wanted], timeout=600)
        self.path(self.tools / "bin")
        self.step("pnpm", "installed", wanted)

    def rust(self):
        if self.found("cargo"):
            return self.step("Rust", "found", self.found("cargo"))
        spec = TOOLCHAIN["rustup"]
        self.env["RUSTUP_HOME"] = str(self.tools / "rustup")
        self.env["CARGO_HOME"] = str(self.tools / "cargo")
        if not (self.tools / "cargo/bin/cargo").exists():
            target = _platform()
            self.progress("Downloading rustup " + spec["version"])
            data = _download(spec["url"].format(version=spec["version"], target=spec["targets"][target]),
                             spec["sha256"][target], self.fetch)
            installer = self.tools / "rustup-init"
            self.tools.mkdir(parents=True, exist_ok=True)
            installer.write_bytes(data)
            installer.chmod(0o700)
            self.check([str(installer), "-y", "--no-modify-path", "--profile", "minimal",
                        "--default-toolchain", "none"], timeout=600)
            installer.unlink()
        self.path(self.tools / "cargo/bin")
        self.step("Rust", "installed", "rustup " + spec["version"])

    # Paperclip -----------------------------------------------------------

    def paperclip(self, label, port):
        from .operations import install_dependencies

        os.environ.update(self.env)  # dependency builds inherit the tool paths
        self.progress("Building Paperclip (several minutes on the first run)")
        installed = install_dependencies(self.home, ["paperclip"])[0]
        source = Path(installed["path"]) / "source"
        self.step("Paperclip build", "ready", str(source))
        env = {
            "PATH": self.service_path(),
            "PAPERCLIP_HOME": str(self.home / "paperclip"),
            "PAPERCLIP_INSTANCE_ID": "awb",
            "PORT": str(port),
            # Paperclip's own telemetry opt-outs.
            "DO_NOT_TRACK": "1",
            "PAPERCLIP_TELEMETRY_DISABLED": "1",
        }
        for key in ("RUSTUP_HOME", "CARGO_HOME", "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE"):
            if self.env.get(key):
                env[key] = self.env[key]
        url = "http://127.0.0.1:" + str(port)
        api = self.api or self._paperclip(url)
        self.onboard(source, env, api)
        self.service(label, [self.found("pnpm"), "paperclipai", "run"], env, source,
                     self.home / "paperclip-service.log")
        if not self._healthy(api, 600):
            raise WorkbenchError("Paperclip did not answer at " + url + " within 10 minutes; see "
                                 + str(self.home / "paperclip-service.log"))
        settings = config(self.home)
        company = settings["coordinator"].get("company_id")
        companies = api.request("GET", "/companies")
        if not any(c.get("id") == company for c in companies):
            mine = next((c for c in companies if c.get("name") == COMPANY), None)
            company = (mine or api.request("POST", "/companies", {"name": COMPANY}))["id"]
        # Repository tasks need their own worktrees; see docs/SETUP.md.
        api.request("PATCH", "/instance/settings/experimental", {"enableIsolatedWorkspaces": True})
        settings["coordinator"].update(kind="paperclip", url=url, company_id=company)
        atomic(self.home / "config.json", canonical(settings) + "\n")
        self.step("Paperclip", "running", url + ", company " + company)

    def onboard(self, source, env, api):
        """Paperclip's quickstart onboarding, once. `paperclipai run` refuses to
        onboard without a terminal, and `onboard --yes` goes on to serve in
        the foreground, so it runs in its own process group until it answers."""
        if (self.home / "paperclip/instances/awb/config.json").exists():
            return self.step("Paperclip onboarding", "already done")
        try:
            taken = api.request("GET", "/health") is not None
        except WorkbenchError:
            taken = False
        if taken:
            raise WorkbenchError("Another server already answers on the Paperclip port; pass --paperclip-port")
        log = self.home / "paperclip-onboarding.log"
        with open(log, "ab") as out:
            process = self.popen([self.found("pnpm"), "paperclipai", "onboard", "--yes", "--no-install-service"],
                                 cwd=str(source), env={**self.env, **env, "PAPERCLIP_NO_BROWSER": "1"},
                                 stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True)
        try:
            answered = self._healthy(api, 600, process)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
        if not answered or not (self.home / "paperclip/instances/awb/config.json").exists():
            raise WorkbenchError("Paperclip onboarding did not finish; see " + str(log))
        self.step("Paperclip onboarding", "done", "trusted local loopback, telemetry off")

    @staticmethod
    def _paperclip(url):
        from .paperclip import Paperclip

        return Paperclip({"url": url})

    def _healthy(self, api, seconds, process=None):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and (process is None or process.poll() is None):
            try:
                if api.request("GET", "/health").get("status") == "ok":
                    return True
            except WorkbenchError:
                pass
            self.sleep(3)
        return False

    def service_path(self):
        from .operations import service_path

        saved = os.environ.get("PATH")
        os.environ["PATH"] = self.env["PATH"]
        try:
            return service_path()
        finally:
            os.environ["PATH"] = saved or ""

    # Services ------------------------------------------------------------

    def service(self, label, argv, env, workdir, log):
        """Write and start a user service that only this home may own."""
        if self.manager == "launchd":
            path = self.agents_dir / (label + ".plist")
            content = plistlib.dumps({
                "Label": label, "ProgramArguments": argv, "EnvironmentVariables": env,
                "WorkingDirectory": str(workdir), "RunAtLoad": True, "KeepAlive": True,
                "ThrottleInterval": 10, "StandardOutPath": str(log), "StandardErrorPath": str(log),
            }).decode()
            domain = "gui/" + str(os.getuid())
            self._install(path, content, [["launchctl", "bootout", domain + "/" + label]],
                          [["launchctl", "bootstrap", domain, str(path)]])
        else:
            import shlex

            unit = label + ".service"
            content = ("[Unit]\nDescription=Assistant Workbench " + label + "\n[Service]\n"
                       + "".join("Environment=" + json.dumps(k + "=" + v) + "\n" for k, v in env.items())
                       + "WorkingDirectory=" + str(workdir) + "\nExecStart=" + shlex.join(argv)
                       + "\nRestart=on-failure\nRestartSec=10\n[Install]\nWantedBy=default.target\n")
            self._install(self.units_dir / unit, content, [],
                          [["systemctl", "--user", "daemon-reload"],
                           ["systemctl", "--user", "enable", "--now", unit]])

    def _install(self, path, content, stop, start):
        if path.exists():
            current = path.read_text()
            if current == content:
                for argv in start[-1:]:
                    self.run(argv, env=self.env, allowed_codes=range(-255, 256))  # loads if unloaded
                return
            if str(self.home) not in current:
                raise WorkbenchError(str(path) + " belongs to another installation; choose another --label-prefix")
            for argv in stop:
                self.run(argv, env=self.env, allowed_codes=range(-255, 256))
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic(path, content, 0o644)
        for argv in start:
            self.check(argv)

    def bridge(self, label, port):
        from .operations import service_definition

        kind = self.manager
        name = label if kind == "launchd" else label + ".service"
        content = service_definition(self.home, self.executable, kind, label, port if port != 8765 else None)
        if kind == "launchd":
            path = self.agents_dir / (label + ".plist")
            domain = "gui/" + str(os.getuid())
            self._install(path, content, [["launchctl", "bootout", domain + "/" + label]],
                          [["launchctl", "bootstrap", domain, str(path)]])
        else:
            self._install(self.units_dir / name, content, [],
                          [["systemctl", "--user", "daemon-reload"],
                           ["systemctl", "--user", "enable", "--now", name]])
        return name

    # Agents --------------------------------------------------------------

    def agents(self, claude_model, codex_model, claude_auth, codex_auth, probe, semantic):
        from . import profiles

        settings = config(self.home)
        existing = {m["id"]: m for m in settings["models"]}
        ready = []
        codex_home = Path(self.env.get("CODEX_HOME") or Path.home() / ".codex")
        if codex_model is None and (codex_home / "config.toml").is_file():
            try:
                codex_model = tomllib.loads((codex_home / "config.toml").read_text()).get("model")
            except (OSError, tomllib.TOMLDecodeError):
                codex_model = None
        for harness, model, auth in (("claude", claude_model, claude_auth), ("codex", codex_model, codex_auth)):
            if not self.found(harness):
                self.step(harness + " CLI", "not found", "install and sign in to use it as a worker")
                continue
            if not model:
                self.step(harness + " CLI", "skipped", "pass --" + harness + "-model or set model in "
                          + str(codex_home / "config.toml"))
                continue
            if harness not in existing:
                profiles.add(self.home, harness, harness, model, "medium", auth, CLASSES, 10 if harness == "claude" else 20)
            if harness == "codex" and not (codex_home / "auth.json").is_file():
                self.step("codex sign-in", "not usable by Paperclip",
                          "run `codex login` with cli_auth_credentials_store = \"file\" so " + str(codex_home / "auth.json") + " exists")
            saved = next(m for m in config(self.home)["models"] if m["id"] == harness)
            ok, reason = profiles.readiness(saved)
            if not ok and probe:
                self.progress("Checking the " + harness + " sign-in with one short prompt")
                try:
                    profiles.probe(self.home, harness)
                    ok, reason = True, "probe passed"
                except WorkbenchError as exc:
                    reason = str(exc)
            self.step(harness + " profile", "ready" if ok else "not ready", reason)
            if ok:
                ready.append(harness)
        if semantic and "claude" in ready:
            settings = config(self.home)
            settings.setdefault("routing", {}).setdefault(
                "semantic", {"profile": "claude", "min_confidence": 0.7, "create_projects": True})
            atomic(self.home / "config.json", canonical(settings) + "\n")
            self.step("Semantic routing", "on", "the claude profile picks or starts the project and the team")
        return ready

    # Terminal ------------------------------------------------------------

    def terminal(self, config_file):
        from . import terminal

        app = Path("/Applications/WezTerm.app")
        if not self.found("wezterm") and not app.exists():
            return self.step("WezTerm", "not installed", "optional; `awb open` works in any terminal")
        try:
            terminal.install(self.home, config_file, self.executable)
            self.step("WezTerm", "Ctrl+Shift+A runs awb open", str(config_file))
        except WorkbenchError as exc:
            self.step("WezTerm", "manual step", str(exc))


def managed_launcher(interpreter=None):
    """The stable launcher of the installed prefix this release runs from, if any:
    <prefix>/lib/assistant-workbench/releases/<release>/venv/bin/python."""
    parts = Path(interpreter or sys.executable).parents
    if len(parts) < 7 or parts[3].name != "releases" or parts[4].name != "assistant-workbench":
        return None
    launcher = parts[6] / "bin/awb"
    try:
        return str(launcher) if "assistant-workbench managed launcher" in launcher.read_text() else None
    except OSError:
        return None


def setup(home, executable, label_prefix="com.assistant-workbench", paperclip_port=3100, bridge_port=8765,
          paperclip=True, agents=True, probe=True, claude_model="sonnet", codex_model=None,
          claude_auth="subscription", codex_auth="subscription", services=True, semantic=True,
          wezterm_config=None, **injected):
    """Bring a home from nothing to `awb open`. Returns what each step did."""
    home = Path(home)
    from .app import Application

    Application(home)  # creates the state and default configuration
    work = Setup(home, str(Path(executable).expanduser().resolve(strict=True)), **injected)
    paperclip_label = label_prefix + ".paperclip"
    if paperclip:
        work.node()
        work.pnpm()
        work.rust()
        work.paperclip(paperclip_label, paperclip_port)
    if agents:
        work.agents(claude_model, codex_model, claude_auth, codex_auth, probe, semantic)
    settings = config(home)
    if services:
        name = work.bridge(label_prefix + ".bridge", bridge_port)
        opened = settings.setdefault("open", {})
        opened["bridge_service"] = name
        if paperclip:
            unit = paperclip_label if work.manager == "launchd" else paperclip_label + ".service"
            opened["services"] = [unit] + [s for s in opened.get("services", []) if s != unit]
        if bridge_port != 8765:
            opened["port"] = bridge_port
        atomic(home / "config.json", canonical(settings) + "\n")
        work.step("Bridge service", "installed", name)
    if wezterm_config is not False:
        work.terminal(wezterm_config or Path.home() / ".wezterm.lua")
    return {"home": str(home), "steps": work.steps, "next": "awb open"}

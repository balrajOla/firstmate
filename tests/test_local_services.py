"""Generated local-service definitions; nothing is started here."""

import importlib.util
import json
from pathlib import Path
import plistlib

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("local_services", ROOT / "scripts/local_services.py")
local_services = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(local_services)


def spec_for(tmp_path):
    """The shipped example with its placeholder root moved under tmp_path."""
    text = (ROOT / "examples/local-services.json").read_text().replace("/home/you", str(tmp_path / "you"))
    spec = json.loads(text)
    for name in ("surreal.password", "encryption.key"):
        path = tmp_path / "you/awb/state/open-notebook" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("secret-" + name + "\n")
        path.chmod(0o600)
    return spec


def test_launchd_agents_carry_paths_and_keep_secrets_private(tmp_path):
    spec = spec_for(tmp_path)
    out = tmp_path / "agents"
    result = local_services.generate(spec, "launchd", out)
    assert "secret-" not in json.dumps(result)
    agents = {p.name: p for p in out.glob("*.plist")}
    assert set(agents) == {"com.assistant-workbench." + n + ".plist" for n in (
        "paperclip", "llm", "embeddings", "hindsight", "openviking", "surrealdb",
        "open-notebook", "open-notebook-worker")}
    for path in agents.values():
        assert path.stat().st_mode & 0o777 == 0o600
    embed = plistlib.loads(agents["com.assistant-workbench.embeddings.plist"].read_bytes())
    argv = embed["ProgramArguments"]
    assert "--embedding" in argv and argv[argv.index("-b") + 1] == argv[argv.index("-c") + 1]
    assert embed["EnvironmentVariables"]["PATH"].startswith(str(tmp_path / "you/awb/tools/bin") + ":")
    hindsight = plistlib.loads(agents["com.assistant-workbench.hindsight.plist"].read_bytes())
    assert hindsight["EnvironmentVariables"]["HOME"] == str(tmp_path / "you/awb/state/hindsight-home")
    assert "127.0.0.1" in hindsight["ProgramArguments"]
    surreal = plistlib.loads(agents["com.assistant-workbench.surrealdb.plist"].read_bytes())
    assert surreal["EnvironmentVariables"]["SURREAL_PASS"] == "secret-surreal.password"
    assert surreal["EnvironmentVariables"]["SURREAL_BIND"] == "127.0.0.1:8000"
    # The worker runs from the same directory as the API, where upstream's
    # relative paths resolve through links to the pinned source.
    run = tmp_path / "you/awb/state/open-notebook/run"
    worker = plistlib.loads(agents["com.assistant-workbench.open-notebook-worker.plist"].read_bytes())
    assert worker["WorkingDirectory"] == str(run)
    assert (run / "open_notebook").is_symlink() and (run / "commands").is_symlink()
    conf = json.loads((tmp_path / "you/awb/state/openviking-home/ov.conf").read_text())
    assert conf["embedding"]["dense"]["dimension"] == 768 and conf["vlm"]["model"] == "local-chat"


def test_systemd_units_read_secrets_from_a_private_file(tmp_path):
    out = tmp_path / "units"
    local_services.generate(spec_for(tmp_path), "systemd", out)
    unit = (out / "com.assistant-workbench.surrealdb.service").read_text()
    assert "secret-" not in unit and "EnvironmentFile=" in unit and "Restart=always" in unit
    env = out / "com.assistant-workbench.surrealdb.env"
    assert env.read_text() == "SURREAL_PASS=secret-surreal.password\n"
    assert env.stat().st_mode & 0o777 == 0o600
    assert "EnvironmentFile" not in (out / "com.assistant-workbench.llm.service").read_text()


def test_a_finished_soak_is_not_restarted(tmp_path):
    spec = {"logs": str(tmp_path), "services": {"soak": {
        "python": "/usr/bin/python3", "script": "/opt/awb/scripts/soak_live.py", "awb": "/opt/awb/bin/awb",
        "home": "/opt/awb/state", "live_project": "Soak", "intake_project": "Intake", "output": "/opt/awb/soak"}}}
    local_services.generate(spec, "launchd", tmp_path / "a")
    agent = plistlib.loads((tmp_path / "a/com.assistant-workbench.soak.plist").read_bytes())
    assert agent["KeepAlive"] == {"SuccessfulExit": False}
    local_services.generate(spec, "systemd", tmp_path / "u")
    assert "Restart=on-failure" in (tmp_path / "u/com.assistant-workbench.soak.service").read_text()


@pytest.mark.parametrize("change,message", [
    (lambda s: s["services"]["llm"].update(model="models/chat.gguf"), "absolute path"),
    (lambda s: s["services"].update(mystery={}), "Unknown services"),
    (lambda s: s["services"]["llm"].update(port=70000), "TCP port"),
])
def test_invalid_specs_are_refused(tmp_path, change, message):
    spec = spec_for(tmp_path)
    change(spec)
    with pytest.raises(local_services.SpecError, match=message):
        local_services.generate(spec, "launchd", tmp_path / "out")


def test_a_shared_secret_file_must_be_private(tmp_path):
    spec = spec_for(tmp_path)
    (tmp_path / "you/awb/state/open-notebook/surreal.password").chmod(0o644)
    with pytest.raises(local_services.SpecError, match="chmod 600"):
        local_services.generate(spec, "launchd", tmp_path / "out")

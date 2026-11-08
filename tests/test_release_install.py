"""Release activation, repeat install and rollback without building a venv."""

import json
from pathlib import Path

import pytest
from assistant_workbench import operations
from assistant_workbench.util import WorkbenchError


@pytest.fixture
def builds(monkeypatch):
    """Stand in for venv/pip: create the interpreter path a release needs."""
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[1:3] == ["-m", "venv"]:
            python = Path(argv[3]) / "bin/python"
            python.parent.mkdir(parents=True)
            python.write_text("#!/bin/sh\n")
        return {"stdout": "0.1.0", "stderr": "", "code": 0}

    monkeypatch.setattr(operations, "run_process", run)
    return calls


def source(tmp_path, marker):
    root = tmp_path / ("source-" + marker)
    (root / "src/assistant_workbench").mkdir(parents=True)
    for name in ("pyproject.toml", "LICENSE", "requirements-runtime.lock"):
        (root / name).write_text(name)
    (root / "src/assistant_workbench/__init__.py").write_text("VERSION = " + json.dumps(marker))
    return root


def launcher_target(prefix):
    return (prefix / "bin/awb").read_text().split("exec ")[1].split(" -m ")[0].strip("'")


def test_repeat_install_keeps_the_real_rollback_target(tmp_path, builds):
    prefix = tmp_path / "Managed Prefix"
    first = operations.install(source(tmp_path, "one"), prefix)
    two = source(tmp_path, "two")
    second = operations.install(two, prefix)
    assert second["previous"] == first["release"]
    again = operations.install(two, prefix)
    assert again["release"] == second["release"]
    # The idempotent reinstall does not make the active release its own fallback.
    assert again["previous"] == first["release"]


def test_rollback_returns_to_the_previous_release_and_back(tmp_path, builds):
    prefix = tmp_path / "Managed Prefix"
    first = operations.install(source(tmp_path, "one"), prefix)
    second = operations.install(source(tmp_path, "two"), prefix)
    back = operations.rollback(prefix)
    assert back["release"] == first["release"] and back["rolled_back_from"] == second["release"]
    assert launcher_target(prefix) == first["release"] + "/venv/bin/python"
    assert builds[-1][1:] == ["-m", "assistant_workbench", "--version"]
    forward = operations.rollback(prefix)
    assert forward["release"] == second["release"]


def test_rollback_refuses_without_an_intact_previous_release(tmp_path, builds):
    prefix = tmp_path / "prefix"
    operations.install(source(tmp_path, "one"), prefix)
    with pytest.raises(WorkbenchError, match="No previous release"):
        operations.rollback(prefix)
    first = json.loads((prefix / "lib/assistant-workbench/install.json").read_text())["release"]
    operations.install(source(tmp_path, "two"), prefix)
    (Path(first) / "ready.json").unlink()
    with pytest.raises(WorkbenchError, match="missing or incomplete"):
        operations.rollback(prefix)


def test_rollback_preserves_a_launcher_the_installer_does_not_own(tmp_path, builds):
    prefix = tmp_path / "prefix"
    operations.install(source(tmp_path, "one"), prefix)
    operations.install(source(tmp_path, "two"), prefix)
    (prefix / "bin/awb").write_text("#!/bin/sh\necho local edit\n")
    with pytest.raises(WorkbenchError, match="not owned"):
        operations.rollback(prefix)
    assert "local edit" in (prefix / "bin/awb").read_text()


def test_a_receipt_naming_itself_as_previous_is_not_a_rollback_target(tmp_path, builds):
    prefix = tmp_path / "prefix"
    operations.install(source(tmp_path, "one"), prefix)
    receipt = prefix / "lib/assistant-workbench/install.json"
    data = json.loads(receipt.read_text())
    data["previous"] = data["release"]
    receipt.write_text(json.dumps(data))
    with pytest.raises(WorkbenchError, match="No previous release"):
        operations.rollback(prefix)

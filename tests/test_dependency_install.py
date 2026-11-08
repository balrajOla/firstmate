import json
import subprocess
import sys
from pathlib import Path
import pytest
from assistant_workbench.operations import install_dependencies
from assistant_workbench.util import WorkbenchError


def _source_repo(tmp_path):
    repo = tmp_path / "upstream"
    repo.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
        ).stdout.strip()

    git("init")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Fixture")
    (repo / "build.py").write_text(
        "import pathlib, sys\n"
        "if not pathlib.Path('toolchain-ready').exists():\n"
        "    print('compiling runner', flush=True)\n"
        "    sys.stderr.write('sh: cargo: command not found\\n')\n"
        "    sys.exit(1)\n"
        "pathlib.Path('built').write_text('ok')\n"
    )
    git("add", ".")
    git("commit", "-m", "fixture")
    return repo, git("rev-parse", "HEAD")


def _registry(repo, revision):
    return {
        "fixture": {
            "kind": "git",
            "url": str(repo),
            "revision": revision,
            "build": [[sys.executable, "build.py"]],
            "qualification": "not implied by installation",
        }
    }


def test_failed_build_reports_its_output_and_allows_a_clean_retry(tmp_path):
    repo, revision = _source_repo(tmp_path)
    home = tmp_path / "home"
    registry = _registry(repo, revision)
    with pytest.raises(WorkbenchError) as failure:
        install_dependencies(home, ["fixture"], registry=registry)
    message = str(failure.value)
    # The cause must be visible, not just "exit 1: <program>".
    assert "cargo: command not found" in message
    log = Path(message.split("Build log: ", 1)[1].strip())
    assert log.is_file() and log.stat().st_mode & 0o777 == 0o600
    assert "compiling runner" in log.read_text()
    dest = home / "dependencies" / "fixture" / revision
    assert not dest.exists(), "a failed attempt must not block the next install"
    assert log.is_relative_to(home / "dependencies" / "fixture")

    # Fix the toolchain problem upstream and retry without manual cleanup.
    (repo / "toolchain-ready").write_text("")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "toolchain"], check=True
    )
    fixed = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    record = install_dependencies(home, ["fixture"], registry=_registry(repo, fixed))[0]
    assert record["state"] == "installed"
    installed = home / "dependencies" / "fixture" / fixed
    assert (installed / "source" / "built").read_text() == "ok"
    assert json.loads((installed / "installed.json").read_text())["revision"] == fixed


def test_retry_after_failure_at_same_revision_runs_again(tmp_path):
    repo, revision = _source_repo(tmp_path)
    home = tmp_path / "home"
    for _ in range(2):
        with pytest.raises(WorkbenchError, match="cargo: command not found"):
            install_dependencies(home, ["fixture"], registry=_registry(repo, revision))
    failed = sorted((home / "dependencies" / "fixture").glob("*.failed-*"))
    assert len(failed) == 2, "each failed attempt is retained for inspection"

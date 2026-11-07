"""Installed wheel / CLI smoke path, enabled by AWB_INSTALLED_CLI."""

import json, os, subprocess
import pytest


@pytest.mark.skipif(
    not os.environ.get("AWB_INSTALLED_CLI"),
    reason="Set AWB_INSTALLED_CLI to test an independently installed artifact",
)
def test_installed_prompt_outcome_backup_restore(tmp_path):
    cli = os.environ["AWB_INSTALLED_CLI"]
    home = tmp_path / "state"
    prefix = [cli, "--home", str(home)]

    def call(*args):
        return json.loads(
            subprocess.run(
                [*prefix, *args], capture_output=True, text=True, check=True
            ).stdout
        )

    def action(name, **args):
        return call("request", json.dumps({"action": name, "args": args}))

    call("init")
    project = call("project", "add", "Installed project")["id"]
    req = call(
        "submit", "Produce a concise report", "--project", project, "--key", "installed"
    )["request_id"]
    outcome = action(
        "outcome.propose",
        request_id=req,
        revision=1,
        body="The result is saved. Live model execution remains untested.",
        claims=[],
        author="fixture-author",
    )
    action(
        "outcome.review",
        outcome_id=outcome["id"],
        reviewer="fixture-reviewer",
        verdict="accept",
        notes="Validated the synthetic fixture outcome",
    )
    assert call("snapshot")["outcomes"][0]["status"] == "accepted"
    call("export", req, str(tmp_path / "export"))
    call("backup", str(tmp_path / "backup"))
    call("restore", str(tmp_path / "backup"), str(tmp_path / "restored"))
    assert (tmp_path / "export/outcome.html").exists() and (
        tmp_path / "restored/workbench.sqlite"
    ).exists()

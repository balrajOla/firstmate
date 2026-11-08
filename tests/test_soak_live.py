"""The live soak runner, driven through the real CLI against a temporary state.

No bridge or coordinator runs here, so live requests stay queued; this checks
the runner's own accounting, not live service behavior.
"""

import importlib.util
import json
from pathlib import Path
import sys

import pytest

from assistant_workbench.app import Application

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("soak_live", ROOT / "scripts/soak_live.py")
soak_live = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(soak_live)
CLI = [sys.executable, "-m", "assistant_workbench"]


@pytest.fixture
def home(tmp_path):
    app = Application(tmp_path / "state")
    app.store.add_project("Live", None, [])
    intake = app.store.add_project("Intake", None, [])
    app.dispatch("workflow.project_policy", {"project": intake["id"], "priority": 50, "paused": True})
    return app.home


def test_cycles_save_every_intake_prompt_and_resume_the_same_soak(home, tmp_path):
    out = tmp_path / "soak"
    first = soak_live.soak(CLI, home, out, "Live", "Intake", hours=1, interval_seconds=0,
                           burst=3, max_cycles=2)
    assert first["status"] == "RUNNING" and first["cycles"] == 2
    assert first["intake_lost"] == 0 and first["intake_errors"] == 0
    assert first["live"]["submitted"] == 2 and first["live"]["by_status"] == {"queued": 2}
    lines = [json.loads(line) for line in (out / "cycles.jsonl").read_text().splitlines()]
    assert [r["intake"]["saved"] for r in lines] == [3, 3]
    assert lines[0]["health"]["bridge"] == {"status": "missing"}
    # Intake requests were cancelled; live ones were not.
    app = Application(home)
    statuses = {r["title"].split(" ")[0]: r["status"] for r in app.store.rows("SELECT * FROM requests")}
    assert statuses["Soak"] == "cancelled"
    # A restarted runner continues the same run, and the time it was not
    # running is a gap, not soaked time.
    state = json.loads((out / "state.json").read_text())
    state["last_cycle_end"] -= 3600
    (out / "state.json").write_text(json.dumps(state))
    resumed = soak_live.soak(CLI, home, out, "Live", "Intake", hours=1, interval_seconds=0,
                             burst=1, max_cycles=1)
    assert resumed["run_id"] == first["run_id"] and resumed["cycles"] == 3
    assert len(resumed["gaps"]) == 1 and resumed["gaps"][0]["seconds"] >= 3600
    assert resumed["soaked_hours"] < 0.5


def test_soak_finishes_only_after_the_target_time(home, tmp_path):
    result = soak_live.soak(CLI, home, tmp_path / "soak", "Live", "Intake", hours=0.0001,
                            interval_seconds=0, burst=1)
    assert result["status"] == "FINISHED" and result["soaked_seconds"] >= 0.36


def test_refuses_an_intake_project_that_could_dispatch(home, tmp_path):
    with pytest.raises(SystemExit, match="Pause the intake project"):
        soak_live.soak(CLI, home, tmp_path / "soak", "Intake", "Live", hours=1, interval_seconds=0)
    with pytest.raises(SystemExit, match="at most 72 hours"):
        soak_live.soak(CLI, home, tmp_path / "soak", "Live", "Intake", hours=73, interval_seconds=0)

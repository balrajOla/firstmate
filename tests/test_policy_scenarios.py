"""Natural task phrases from the three-project exercise, not a quality benchmark."""

import pytest
from assistant_workbench.policy import classification, classify, compile_plan


@pytest.mark.parametrize(
    "prompt, expected",
    [
        ("Create a website for HarborWeb with accessible event booking.", "code"),
        ("Create a mobile app for PocketTasks with offline habits.", "code"),
        ("Report the architecture review.", "report"),
        ("Add dark mode to the website.", "code"),
        (
            "Research offline-first mobile architecture and provide a report.",
            "research",
        ),
        ("Write code that produces a report.", "code"),
        ("Create a plan for building a website.", "design"),
        ("Write a plan for building a website.", "design"),
        ("Please summarize the research about code review.", "report"),
        ("Fix the failing test without changing the research report.", "code"),
        ("Compare two designs before implementing either.", "research"),
        ("Design the architecture for a mobile app.", "design"),
        ("Create a report from the architecture decisions.", "report"),
    ],
)
def test_requested_action_drives_workflow(prompt, expected):
    decision = classification(prompt)
    assert decision["workflow"] == classify(prompt) == expected
    assert decision["requires_review"] and decision["method"] == "lexical policy"
    assert decision["reason"]


def test_website_and_mobile_use_engineer_and_reviewer(app, project, configured):
    for prompt in ["Create a website", "Create a mobile app"]:
        receipt = app.store.submit(prompt, prompt, project["id"])
        request = app.store.one(
            "SELECT * FROM requests WHERE id=?", (receipt["request_id"],)
        )
        plan = compile_plan(request, configured)
        assert plan["workflow"] == "code"
        assert [node["role"] for node in plan["nodes"]] == ["engineer", "qa"]
        assert plan["nodes"][1]["depends_on"] == ["implement"]


@pytest.mark.parametrize(
    "scenario_id, expected",
    [
        ("S10", "code"),
        ("S11", "code"),
        ("S16", "code"),
        ("S17", "code"),
        ("S25", "report"),
    ],
)
def test_manifest_context_prefix_keeps_requested_work(scenario_id, expected):
    import json
    from pathlib import Path

    manifest = json.loads(
        (Path(__file__).parents[1] / "evals/scenarios-30.json").read_text()
    )
    prompt = next(
        x["instruction"] for x in manifest["scenarios"] if x["id"] == scenario_id
    )
    assert classify(prompt) == expected

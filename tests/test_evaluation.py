import json
import pytest
from assistant_workbench.evaluation import (
    judge_agreement,
    routing_cases,
    propose_policy,
    apply_policy,
)
from assistant_workbench.util import WorkbenchError


def test_judges_report_false_accepts():
    result = judge_agreement(
        [
            {"human": "reject", "judge": "accept"},
            {"human": "accept", "judge": "accept"},
            {"human": "accept", "judge": "reject"},
        ]
    )
    assert (
        result["false_accept"] == 1
        and result["false_reject"] == 1
        and result["agreement"] == 1 / 3
    )


def test_routing_reports_misses_instead_of_hiding_them():
    result = routing_cases(
        [
            {
                "id": "ambiguous",
                "prompt": "Build a website",
                "workflow": "research",
            }
        ]
    )
    assert result["accuracy"] == 0 and not result["cases"][0]["passed"]


def test_policy_review_and_conflicting_rollback(app, configured):
    change = propose_policy(
        app.store,
        "model_priorities",
        {"test-process": 4},
        "Measured canary ordering",
        [{"fixture": "fixture-only"}],
    )
    assert (
        json.loads((app.home / "config.json").read_text())["models"][0]["priority"] == 1
    )
    apply_policy(app.store, change["id"], "operator")
    assert (
        json.loads((app.home / "config.json").read_text())["models"][0]["priority"] == 4
    )
    apply_policy(app.store, change["id"], "operator", rollback=True)
    assert (
        json.loads((app.home / "config.json").read_text())["models"][0]["priority"] == 1
    )


def test_invalid_policy_is_not_applied(app, configured):
    change = propose_policy(
        app.store,
        "model_priorities",
        {"unknown": 1},
        "Attempted change",
        [{"test": "fixture"}],
    )
    with pytest.raises(WorkbenchError):
        apply_policy(app.store, change["id"], "operator")

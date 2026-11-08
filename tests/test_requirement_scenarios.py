"""Revision, grouping and review scenarios using the real durable application.

These are application-state tests, not live model or coordinator evidence.
"""

from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from assistant_workbench.quality import Outcomes
from assistant_workbench.util import WorkbenchError


def request(app, project, text="Build the website", key="initial"):
    return app.store.submit(text, key, project["id"])["request_id"]


def obligations(app, ident, revision=1, descriptions=("Website", "Accessibility")):
    return app.dispatch("workflow.obligations", {
        "request_id": ident, "revision": revision,
        "items": [{"description": text, "acceptance": "Verify " + text} for text in descriptions],
    })["ids"]


def rows(app, ident, revision):
    return app.store.rows("SELECT * FROM obligations WHERE request_id=? AND revision=? ORDER BY rowid", (ident, revision))


def propose(app, ident, body="A proposed result, subject to independent review.", revision=1):
    return Outcomes(app.store).propose(ident, body, [], "author", revision)["id"]


def review(app, outcome):
    return Outcomes(app.store).review(outcome, "reviewer", "accept", "Checked the explicit acceptance criteria")


def test_amendments_carry_unfulfilled_deliverables_to_each_revision(app, project):
    ident = request(app, project)
    obligations(app, ident)
    app.store.submit("Add search", "amend-1", reply_to=ident, expected_revision=1)
    app.store.submit("Add dark mode", "amend-2", reply_to=ident, expected_revision=2)
    current = rows(app, ident, 3)
    assert [r["description"] for r in current] == ["Website", "Accessibility"]
    assert all(r["status"] == "pending" and r["outcome_id"] is None for r in current)
    outcome = propose(app, ident, revision=3)
    with pytest.raises(WorkbenchError):
        review(app, outcome)
    assert len(app.store.rows("SELECT * FROM prompts WHERE request_id=?", (ident,))) == 3


def test_grouping_preserves_each_explicit_acceptance_criterion(app, project):
    a = request(app, project, "Add website search", "a")
    b = request(app, project, "Add website filters", "b")
    obligations(app, a, descriptions=("Search handles empty queries",))
    obligations(app, b, descriptions=("Filters preserve URL state",))
    leader = app.dispatch("workflow.group", {"requests": [a, b], "reason": "One search experience"})["request_id"]
    descriptions = {r["description"] for r in rows(app, leader, 1)}
    assert {"Search handles empty queries", "Filters preserve URL state"} <= descriptions


def test_grouped_child_amendment_updates_parent_without_duplicate_dispatch(app, project):
    a = request(app, project, "Add website search", "a")
    b = request(app, project, "Add website filters", "b")
    leader = app.dispatch("workflow.group", {"requests": [a, b], "reason": "Related website work"})["request_id"]
    app.store.submit("Support keyboard navigation", "child-update", reply_to=a, expected_revision=1)
    parent = app.dispatch("request.get", {"request_id": leader})
    child = app.dispatch("request.get", {"request_id": a})
    assert child["status"] == "grouped" and parent["revision"] == 2
    assert "Support keyboard navigation" in parent["body"] and "website filters" in parent["body"]
    pending = app.store.rows("SELECT * FROM outbox WHERE status='pending'")
    assert [(r["request_id"], r["revision"]) for r in pending] == [(leader, 2)]


def test_group_withdrawal_keeps_custom_parent_and_survivor_criteria(app, project):
    a = request(app, project, "Website checkout", "a")
    b = request(app, project, "Website browsing", "b")
    obligations(app, a, descriptions=("Payment receipt",))
    obligations(app, b, descriptions=("Accessible cards",))
    leader = app.dispatch("workflow.group", {"requests": [a, b], "reason": "Website"})["request_id"]
    obligations(app, leader, descriptions=("Regression suite",))
    app.store.submit("Keep the release notes concise", "parent-update", reply_to=leader, expected_revision=1)
    app.store.cancel(a)
    parent = app.dispatch("request.get", {"request_id": leader})
    assert "Keep the release notes concise" in parent["body"]
    descriptions = {r["description"] for r in rows(app, leader, parent["revision"])}
    assert {"Accessible cards", "Regression suite"} <= descriptions
    assert "Payment receipt" not in descriptions


def test_reopen_preserves_deliverables_and_links_historical_request(app, project):
    ident = request(app, project)
    obligations(app, ident)
    app.store.cancel(ident)
    reopened = app.store.reopen(ident)["request_id"]
    assert {r["description"] for r in rows(app, reopened, 1)} == {"Website", "Accessibility"}
    assert app.store.rows("SELECT * FROM request_links WHERE parent=? AND child=? AND kind='reopen'", (reopened, ident))
    with pytest.raises(WorkbenchError):
        review(app, propose(app, reopened))


def test_separate_deliverables_can_be_reviewed_without_premature_completion(app, project):
    ident = request(app, project)
    first, second = obligations(app, ident)
    out_a = propose(app, ident, "Website implementation is ready for review.")
    app.dispatch("workflow.fulfill", {"obligation_id": first, "outcome_id": out_a})
    review(app, out_a)
    assert app.dispatch("request.get", {"request_id": ident})["status"] != "accepted"
    assert [r["status"] for r in rows(app, ident, 1)] == ["accepted", "pending"]
    out_b = propose(app, ident, "Accessibility checks are ready for review.")
    app.dispatch("workflow.fulfill", {"obligation_id": second, "outcome_id": out_b})
    review(app, out_b)
    assert app.dispatch("request.get", {"request_id": ident})["status"] == "accepted"
    assert all(r["status"] == "accepted" for r in rows(app, ident, 1))


def test_competing_outcomes_cannot_both_close_one_request(app, project):
    ident = request(app, project)
    candidates = [propose(app, ident, f"Candidate {i}") for i in range(2)]

    def accept(outcome):
        try:
            review(app, outcome)
            return True
        except WorkbenchError:
            return False

    with ThreadPoolExecutor(2) as pool:
        accepted = list(pool.map(accept, candidates))
    assert accepted.count(True) == 1
    assert len(app.store.rows("SELECT * FROM outcomes WHERE request_id=? AND status='accepted'", (ident,))) == 1


def test_unassigned_amendment_still_requires_project_assignment(app):
    ident = app.store.submit("An unspecified new project", "a")["request_id"]
    app.store.submit("Also include a report", "b", reply_to=ident, expected_revision=1)
    assert app.dispatch("request.get", {"request_id": ident})["status"] == "needs_project"
    project = app.store.add_project("Assigned")
    app.store.assign(ident, project["id"])
    assert app.store.one("SELECT * FROM outbox WHERE request_id=?", (ident,))["revision"] == 2


def test_retrying_grouped_amendment_does_not_increment_parent_twice(app, project):
    a = request(app, project, "Website search", "a")
    b = request(app, project, "Website filters", "b")
    leader = app.dispatch("workflow.group", {"requests": [a, b], "reason": "Website"})["request_id"]
    kwargs = dict(text="Keyboard support", client_key="retry", reply_to=a, expected_revision=1)
    app.store.submit(**kwargs)
    receipt = app.store.submit(**kwargs)
    assert receipt["duplicate"]
    assert app.dispatch("request.get", {"request_id": leader})["revision"] == 2
    assert len(app.store.rows("SELECT * FROM prompts WHERE request_id=?", (a,))) == 2


def test_parallel_group_member_amendments_preserve_both_updates(app, project):
    ids = [request(app, project, text, str(i)) for i, text in enumerate(["Website", "Mobile app"])]
    leader = app.dispatch("workflow.group", {"requests": ids, "reason": "Shared release"})["request_id"]
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda i: app.store.submit(f"Feature {i}", f"update-{i}", reply_to=ids[i], expected_revision=1), range(2)))
    parent = app.dispatch("request.get", {"request_id": leader})
    assert parent["revision"] == 3
    assert "Feature 0" in parent["body"] and "Feature 1" in parent["body"]
    assert len(app.store.rows("SELECT * FROM outbox WHERE request_id=? AND status='pending'", (leader,))) == 1


def test_group_amendment_after_parent_acceptance_is_rejected_without_writes(app, project):
    ids = [request(app, project, text, str(i)) for i, text in enumerate(["Website", "Mobile app"])]
    leader = app.dispatch("workflow.group", {"requests": ids, "reason": "Shared release"})["request_id"]
    outcome = propose(app, leader)
    for item in rows(app, leader, 1):
        app.dispatch("workflow.fulfill", {"obligation_id": item["id"], "outcome_id": outcome})
    review(app, outcome)
    with pytest.raises(WorkbenchError):
        app.store.submit("Late update", "late", reply_to=ids[0], expected_revision=1)
    assert len(app.store.rows("SELECT * FROM prompts")) == 2
    assert app.dispatch("request.get", {"request_id": leader})["status"] == "accepted"


def test_reopened_member_can_be_withdrawn_without_leaking_its_old_requirements(app, project):
    old = request(app, project, "Website checkout", "old")
    obligations(app, old, descriptions=("Payment receipt",))
    app.store.cancel(old)
    a = app.store.reopen(old)["request_id"]
    b = request(app, project, "Website catalogue", "b")
    obligations(app, b, descriptions=("Search controls",))
    leader = app.dispatch("workflow.group", {"requests": [a, b], "reason": "Website"})["request_id"]
    app.store.cancel(a)
    assert "Payment receipt" not in {r["description"] for r in rows(app, leader, 2)}
    assert "Search controls" in {r["description"] for r in rows(app, leader, 2)}


def test_nested_groups_are_rejected_before_any_request_is_changed(app, project):
    ids = [request(app, project, f"Part {i}", str(i)) for i in range(3)]
    leader = app.dispatch("workflow.group", {"requests": ids[:2], "reason": "Two parts"})["request_id"]
    with pytest.raises(WorkbenchError, match="Nested"):
        app.dispatch("workflow.group", {"requests": [leader, ids[2]], "reason": "Nested"})
    assert app.dispatch("request.get", {"request_id": leader})["status"] == "queued"
    assert app.dispatch("request.get", {"request_id": ids[2]})["status"] == "queued"


def test_last_group_withdrawal_cannot_reopen_a_blank_scope(app, project):
    ids = [request(app, project, f"Part {i}", str(i)) for i in range(2)]
    leader = app.dispatch("workflow.group", {"requests": ids, "reason": "Two parts"})["request_id"]
    for ident in ids:
        app.store.cancel(ident)
    with pytest.raises(WorkbenchError, match="No active group scope"):
        app.store.reopen(leader)


def test_accepted_deliverable_cannot_be_reassigned_while_others_remain(app, project):
    ident = request(app, project)
    first, _ = obligations(app, ident)
    out_a, out_b = propose(app, ident, "First"), propose(app, ident, "Alternative")
    app.dispatch("workflow.fulfill", {"obligation_id": first, "outcome_id": out_a})
    review(app, out_a)
    with pytest.raises(WorkbenchError, match="accepted deliverable"):
        app.dispatch("workflow.fulfill", {"obligation_id": first, "outcome_id": out_b})
    assert rows(app, ident, 1)[0]["outcome_id"] == out_a


def test_cancelled_outcome_cannot_be_fulfilled_or_reviewed(app, project):
    ident = request(app, project)
    criterion = obligations(app, ident, descriptions=("Website",))[0]
    outcome = propose(app, ident)
    app.store.cancel(ident)
    with pytest.raises(WorkbenchError):
        app.dispatch("workflow.fulfill", {"obligation_id": criterion, "outcome_id": outcome})
    with pytest.raises(WorkbenchError):
        review(app, outcome)
    assert app.store.one("SELECT * FROM outcomes WHERE id=?", (outcome,))["status"] == "cancelled"


def test_proposal_cannot_reopen_request_accepted_during_source_validation(app, project, tmp_path):
    from assistant_workbench.context import Context
    ident = request(app, project)
    prior = propose(app, ident)
    path = tmp_path / "source.txt"
    path.write_text("The supported amount is 14.")
    source = Context(app.store).ingest(project["id"], path)
    outcomes = Outcomes(app.store)
    original = outcomes.context.source
    entered = False

    def accept_during_validation(*args):
        nonlocal entered
        if not entered:
            entered = True
            review(app, prior)
        return original(*args)

    outcomes.context.source = accept_during_validation
    with pytest.raises(WorkbenchError, match="changed while drafting"):
        outcomes.propose(ident, "The supported amount is 14.", [{"source_id": source["id"], "quote": "The supported amount is 14.", "statement": "The supported amount is 14.", "kind": "fact"}], "other-author", 1)
    assert app.dispatch("request.get", {"request_id": ident})["status"] == "accepted"
    assert len(app.store.rows("SELECT * FROM outcomes WHERE request_id=?", (ident,))) == 1


@pytest.mark.parametrize("reviewer", ["author ", " AUTHOR", "\tAuthor\n"])
def test_cosmetic_identity_changes_cannot_self_review(app, project, reviewer):
    ident = request(app, project)
    outcome = propose(app, ident)
    with pytest.raises(WorkbenchError, match="own independent reviewer"):
        Outcomes(app.store).review(outcome, reviewer, "accept", "Claiming independence")


def test_outcome_style_uses_the_configured_audience_budget(app, project):
    config = json.loads((app.home / "config.json").read_text())
    config["audience"] = {"reader": "busy reader", "style": "concise", "max_words": 3}
    (app.home / "config.json").write_text(json.dumps(config))
    ident = request(app, project)
    outcome = Outcomes(app.store).propose(ident, "Four words need editing.", [], "author", 1)
    assert outcome["checks"]["style"]["over_word_budget"]
    assert outcome["checks"]["audience"]["reader"] == "busy reader"


@pytest.mark.parametrize("invalidation", ["revoke", "corrupt"])
def test_invalidated_accepted_deliverable_blocks_request_completion(app, project, tmp_path, invalidation):
    from assistant_workbench.context import Context

    ident = request(app, project)
    first, second = obligations(app, ident)
    path = tmp_path / "reference.txt"
    statement = "The supported amount is 14."
    path.write_text(statement)
    context = Context(app.store)
    source = context.ingest(project["id"], path)
    out_a = Outcomes(app.store).propose(
        ident, statement,
        [{"source_id": source["id"], "quote": statement, "statement": statement, "kind": "fact"}],
        "author", 1,
    )["id"]
    app.dispatch("workflow.fulfill", {"obligation_id": first, "outcome_id": out_a})
    review(app, out_a)
    if invalidation == "revoke":
        context.revoke(project["id"], source["id"])
    else:
        (app.home / "archive" / source["hash"][:2] / source["hash"]).write_text("corruption")
    out_b = propose(app, ident, "The second deliverable is ready.")
    app.dispatch("workflow.fulfill", {"obligation_id": second, "outcome_id": out_b})
    with pytest.raises(WorkbenchError):
        review(app, out_b)
    assert app.dispatch("request.get", {"request_id": ident})["status"] != "accepted"
    assert app.store.one("SELECT * FROM outcomes WHERE id=?", (out_b,))["status"] == "needs_review"
    # Explicitly revising the request resets criteria and retains the review history.
    app.store.submit("Replace the invalidated evidence", "replace-evidence", reply_to=ident, expected_revision=1)
    assert all(item["status"] == "pending" for item in rows(app, ident, 2))
    assert app.store.rows("SELECT * FROM reviews WHERE outcome_id=?", (out_a,))


def test_legacy_group_criteria_keep_ownership_after_member_withdrawal(app, project):
    a = request(app, project, "Website checkout", "a")
    b = request(app, project, "Website catalogue", "b")
    leader = app.dispatch("workflow.group", {"requests": [a, b], "reason": "Website"})["request_id"]
    obligations(app, leader, descriptions=("Regression suite",))
    # A pre-upgrade group has its original obligations but no ownership rows.
    with app.store.transaction() as db:
        db.execute("DELETE FROM obligation_origins")
    app.store.submit("Keep release notes short", "legacy-parent-update", reply_to=leader, expected_revision=1)
    app.store.cancel(a)
    descriptions = [r["description"] for r in rows(app, leader, 3)]
    assert descriptions == ["Regression suite", "Website catalogue"]
    assert len(rows(app, leader, 1)) == 3


def test_ambiguous_legacy_group_ownership_stops_without_dropping_requirements(app, project):
    ids = [request(app, project, "Same request", str(i)) for i in range(2)]
    leader = app.dispatch("workflow.group", {"requests": ids, "reason": "Legacy duplicate descriptions"})["request_id"]
    with app.store.transaction() as db:
        db.execute("DELETE FROM obligation_origins")
    with pytest.raises(WorkbenchError, match="ownership"):
        app.store.cancel(ids[0])
    assert app.dispatch("request.get", {"request_id": ids[0]})["status"] == "grouped"
    assert app.dispatch("request.get", {"request_id": leader})["revision"] == 1
    assert len(rows(app, leader, 1)) == 2

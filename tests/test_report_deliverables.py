"""Export complete current deliverables using real review and evidence lifecycles."""

import json
from html.parser import HTMLParser
from pathlib import Path

import pytest

from assistant_workbench.context import Context
from assistant_workbench.quality import Outcomes
from assistant_workbench.reports import export_report


def new_request(app, project, key="report"):
    request = app.store.submit("Produce the report and implementation plan", key, project["id"])["request_id"]
    obligations = app.dispatch("workflow.obligations", {
        "request_id": request, "revision": 1,
        "items": [
            {"description": "Report <img onerror=bad()>", "acceptance": "Verify original evidence"},
            {"description": "Implementation plan", "acceptance": "Assign an owner and test <script>bad()</script>"},
        ],
    })["ids"]
    return request, obligations


def propose(app, request, body, obligations=(), claims=(), revision=1):
    outcome = Outcomes(app.store).propose(request, body, list(claims), "writer <svg onload=bad()>", revision)["id"]
    for obligation in obligations:
        app.dispatch("workflow.fulfill", {"obligation_id": obligation, "outcome_id": outcome})
    return outcome


def accept(app, outcome):
    return Outcomes(app.store).review(outcome, "independent-reviewer", "accept", "Checked the cited evidence and acceptance criteria <img onerror=bad()>")


def read_export(app, request, destination):
    result = export_report(app.store, request, destination)
    return result, Path(result["html"]).read_text(), Path(result["markdown"]).read_text(), json.loads(Path(result["json"]).read_text())


def source_claim(app, project, tmp_path, statement="Recorded amount is 14."):
    path = tmp_path / "evidence <svg onload=bad()>.md"
    path.write_text(statement + " This is a fixture source, not a research result.")
    source = Context(app.store).ingest(project["id"], path)
    return source, {"source_id": source["id"], "quote": statement, "statement": statement, "kind": "fact"}


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.seen = []

    def handle_starttag(self, tag, attrs):
        self.seen.append((tag, attrs))


def test_export_all_separately_accepted_deliverables_with_provenance(app, project, tmp_path):
    request, obligations = new_request(app, project)
    source, claim = source_claim(app, project, tmp_path)
    first = propose(app, request, "Recorded amount is 14. FIRST-ACCEPTED", [obligations[0]], [claim])
    accept(app, first)
    second = propose(app, request, "SECOND-ACCEPTED: Owner is the implementation team.", [obligations[1]])
    accept(app, second)
    _, html, markdown, record = read_export(app, request, tmp_path / "complete")
    for rendered in (html, markdown):
        assert "FIRST-ACCEPTED" in rendered and "SECOND-ACCEPTED" in rendered
        assert "Accepted deliverables: 2/2" in rendered
        assert source["hash"] in rendered and source["id"] in rendered
        assert "independent-reviewer" in rendered and "Source project:" in rendered
        assert first in rendered and second in rendered and request in rendered
    assert record["presentation"]["status"] == "accepted"
    assert record["presentation"]["accepted_outcome_ids"] == [first, second]
    assert {o["id"] for o in record["outcomes"]} == {first, second}
    assert record["sources"][0]["locator"] == source["locator"]
    assert "text" not in record["sources"][0]
    parser = Tags()
    parser.feed(html)
    assert not any(tag in {"img", "script", "svg"} for tag, _ in parser.seen)
    assert not any(name.startswith("on") for _, attrs in parser.seen for name, _ in attrs)
    assert "Content-Security-Policy" in html


def test_partial_export_does_not_replace_accepted_work_with_latest_rejected_draft(app, project, tmp_path):
    request, obligations = new_request(app, project)
    first = propose(app, request, "ACCEPTED-REPORT", [obligations[0]])
    accept(app, first)
    rejected = propose(app, request, "REJECTED-PLAN-MUST-NOT-LOOK-ACCEPTED", [obligations[1]])
    Outcomes(app.store).review(rejected, "reviewer", "revise", "Missing an execution owner")
    result, html, markdown, record = read_export(app, request, tmp_path / "partial")
    assert result["status"] == "needs_revision"
    for rendered in (html, markdown):
        assert "ACCEPTED-REPORT" in rendered and "Accepted deliverables: 1/2" in rendered
        assert "REJECTED-PLAN-MUST-NOT-LOOK-ACCEPTED" not in rendered
        assert rejected in rendered and "needs_revision" in rendered
    assert record["presentation"]["accepted_outcome_ids"] == [first]
    assert record["presentation"]["accepted_deliverables"] == 1
    assert len(record["outcomes"]) == 2


def test_revision_change_excludes_previously_accepted_body_and_keeps_audit_history(app, project, tmp_path):
    request, obligations = new_request(app, project)
    old = propose(app, request, "OLD-REVISION-ACCEPTED-BODY", [obligations[0]])
    accept(app, old)
    app.store.submit("Change the accounting period", "amend", reply_to=request, expected_revision=1)
    current = propose(app, request, "CURRENT-REVISION-DRAFT", revision=2)
    result, html, markdown, record = read_export(app, request, tmp_path / "revision")
    assert result["status"] == "needs_review"
    for rendered in (html, markdown):
        assert "CURRENT-REVISION-DRAFT" in rendered
        assert "OLD-REVISION-ACCEPTED-BODY" not in rendered
        assert "Latest proposed outcome" in rendered
        assert "Accepted deliverables: 0/2" in rendered
    assert record["presentation"]["accepted_outcome_ids"] == []
    assert record["presentation"]["displayed_outcome_ids"] == [current]
    assert {o["id"] for o in record["outcomes"]} == {old, current}
    assert all(o["revision"] == 2 for o in record["obligations"])


def test_revoked_evidence_removes_one_accepted_deliverable_from_current_report(app, project, tmp_path):
    request, obligations = new_request(app, project)
    source, claim = source_claim(app, project, tmp_path)
    revoked = propose(app, request, "Recorded amount is 14. REVOKED-RESULT", [obligations[0]], [claim])
    accept(app, revoked)
    remaining = propose(app, request, "STILL-VALID-PLAN", [obligations[1]])
    accept(app, remaining)
    Context(app.store).revoke(project["id"], source["id"])
    result, html, markdown, record = read_export(app, request, tmp_path / "revoked")
    assert result["status"] == "needs_review"
    for rendered in (html, markdown):
        assert "STILL-VALID-PLAN" in rendered and "REVOKED-RESULT" not in rendered
        assert "Accepted deliverables: 1/2" in rendered
        assert "source_revoked" in rendered and "accepted evidence unavailable" in rendered
    assert record["presentation"]["accepted_outcome_ids"] == [remaining]


@pytest.mark.parametrize("invalidate", ["scope", "archive"])
def test_unavailable_evidence_never_exports_as_accepted(app, project, tmp_path, invalidate):
    source_project = app.store.add_project("Evidence owner")
    app.store.grant(project["id"], source_project["id"])
    source, claim = source_claim(app, source_project, tmp_path)
    request = app.store.submit("Use explicitly shared evidence", "shared", project["id"])["request_id"]
    outcome = propose(app, request, "Recorded amount is 14. NO-LONGER-QUALIFIED", claims=[claim])
    accept(app, outcome)
    if invalidate == "scope":
        with app.store.transaction() as db:
            db.execute("DELETE FROM grants WHERE project_id=?", (project["id"],))
    else:
        (app.home / "archive" / source["hash"][:2] / source["hash"]).write_text("Corrupt original")
    result, html, markdown, record = read_export(app, request, tmp_path / invalidate)
    assert result["status"] == "needs_review"
    for rendered in (html, markdown):
        assert "NO-LONGER-QUALIFIED" not in rendered
        assert "Cited evidence is unavailable" in rendered
    assert record["presentation"]["accepted_outcome_ids"] == []
    assert "locator" not in record["sources"][0] and "hash" not in record["sources"][0]
    # The existing JSON audit contract retains historical saved outcomes explicitly.
    assert record["outcomes"][0]["id"] == outcome


def test_one_outcome_can_cover_multiple_deliverables_without_duplicate_body(app, project, tmp_path):
    request, obligations = new_request(app, project)
    outcome = propose(app, request, "ONE-BODY-COVERS-BOTH", obligations)
    accept(app, outcome)
    _, html, markdown, record = read_export(app, request, tmp_path / "deduplicated")
    assert html.count("ONE-BODY-COVERS-BOTH") == markdown.count("ONE-BODY-COVERS-BOTH") == 1
    assert record["presentation"]["accepted_deliverables"] == 2
    assert record["presentation"]["accepted_outcome_ids"] == [outcome]


def test_export_scope_excludes_unrelated_outcomes_and_cross_project_link(app, project, tmp_path):
    request = app.store.submit("Selected request", "selected", project["id"])["request_id"]
    propose(app, request, "SELECTED-DRAFT")
    other_project = app.store.add_project("Unrelated project")
    other = app.store.submit("PRIVATE-PROMPT-OTHER-PROJECT", "other", other_project["id"])["request_id"]
    propose(app, other, "PRIVATE-OUTCOME-OTHER-PROJECT")
    same_project = app.store.submit("Different request same project", "same", project["id"])["request_id"]
    propose(app, same_project, "UNRELATED-OUTCOME-SAME-PROJECT")
    with app.store.transaction() as db:
        db.execute("INSERT INTO request_links VALUES(?,?,'group')", (request, other))
    _, html, markdown, record = read_export(app, request, tmp_path / "scoped")
    for rendered in (html, markdown, json.dumps(record)):
        assert "PRIVATE-PROMPT-OTHER-PROJECT" not in rendered
        assert "PRIVATE-OUTCOME-OTHER-PROJECT" not in rendered
        assert "UNRELATED-OUTCOME-SAME-PROJECT" not in rendered
        assert "SELECTED-DRAFT" in rendered
    assert record["project"]["id"] == project["id"]


def test_cancelled_request_does_not_present_historical_acceptance_as_current(app, project, tmp_path):
    request, obligations = new_request(app, project)
    outcome = propose(app, request, "PRE-CANCELLATION-ACCEPTED", [obligations[0]])
    accept(app, outcome)
    app.store.cancel(request)
    result, html, markdown, record = read_export(app, request, tmp_path / "cancelled")
    assert result["status"] == "cancelled"
    assert "PRE-CANCELLATION-ACCEPTED" not in html + markdown
    assert record["outcomes"][0]["body"] == "PRE-CANCELLATION-ACCEPTED"
    assert record["presentation"]["accepted_outcome_ids"] == []

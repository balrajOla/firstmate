import json
import pytest
from assistant_workbench.context import Context
from assistant_workbench.quality import Outcomes, lint
from assistant_workbench.util import WorkbenchError


def source(
    app, project, tmp_path, text="Revenue was 14, not 40. This estimate excludes tax."
):
    path = tmp_path / "source.md"
    path.write_text(text)
    return Context(app.store).ingest(project["id"], path)


def test_scope_originals_and_corruption(app, project, tmp_path):
    c = Context(app.store)
    s = source(app, project, tmp_path)
    other = app.store.add_project("Other")
    assert c.search(other["id"], "Revenue") == []
    with pytest.raises(WorkbenchError):
        c.source(other["id"], s["id"])
    app.store.grant(other["id"], project["id"])
    assert c.search(other["id"], "Revenue")
    (app.home / "archive" / s["hash"][:2] / s["hash"]).write_text("corruption")
    with pytest.raises(WorkbenchError):
        c.source(project["id"], s["id"])


def test_source_supersession_and_packet_qualification(app, project, tmp_path):
    c = Context(app.store)
    first = source(app, project, tmp_path)
    second = source(
        app, project, tmp_path, "Revenue is revised to 12. This excludes refunds."
    )
    assert second["supersedes"] == first["id"]
    assert [r["id"] for r in c.search(project["id"], "Revenue")] == [second["id"]]
    packet = c.packet(project["id"], "Revenue", 1000)
    assert "excludes refunds" in packet["sources"][0]["passages"][0]["text"]
    assert packet["used_chars"] <= 1000 and "not proof" in packet["coverage"]


def test_oversized_passage_is_visible_omission(app, project, tmp_path):
    s = source(app, project, tmp_path, "Revenue " + ("long " * 300))
    packet = Context(app.store).packet(project["id"], "Revenue", 100)
    assert packet["sources"] == [] and packet["omitted"] == [s["id"]]


def test_coverage_does_not_treat_extraction_as_review(app, project, tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "one.md").write_text("facts")
    (root / "two.pdf").write_bytes(b"\xff\x00")
    c = Context(app.store)
    c.inventory(project["id"], root)
    c.ingest(project["id"], root / "one.md")
    assert not c.coverage(project["id"])["complete"]
    c.mark(project["id"], str(root / "one.md"), "reviewed")
    c.mark(
        project["id"],
        str(root / "two.pdf"),
        "excluded",
        "Unreadable attachment; user excluded it",
    )
    assert c.coverage(project["id"])["complete"]


def test_grounded_outcome_review_and_revocation(app, project, tmp_path):
    s = source(app, project, tmp_path)
    r = app.store.submit("Write revenue report", "a", project["id"])["request_id"]
    out = Outcomes(app.store).propose(
        r,
        "Revenue was 14.",
        [
            {
                "source_id": s["id"],
                "quote": "Revenue was 14, not 40.",
                "statement": "Revenue was 14.",
                "kind": "fact",
            }
        ],
        "writer",
        1,
    )
    assert out["checks"]["citations"][0]["entailment"] == "requires review"
    with pytest.raises(WorkbenchError):
        Outcomes(app.store).review(out["id"], "writer", "accept", "Looks good")
    Outcomes(app.store).review(
        out["id"], "reviewer", "accept", "Checked amount and exclusion against original"
    )
    Context(app.store).revoke(project["id"], s["id"])
    assert app.store.one("SELECT * FROM outcomes")["status"] == "source_revoked"


def test_fabricated_quote_and_stale_revision_rejected(app, project, tmp_path):
    s = source(app, project, tmp_path)
    r = app.store.submit("Report", "a", project["id"])["request_id"]
    with pytest.raises(WorkbenchError):
        Outcomes(app.store).propose(
            r,
            "Revenue was 40.",
            [
                {
                    "source_id": s["id"],
                    "quote": "Revenue was 40.",
                    "statement": "Revenue was 40.",
                    "kind": "fact",
                }
            ],
            "writer",
            1,
        )
    out = Outcomes(app.store).propose(
        r, "An inference, not an audited result.", [], "writer", 1
    )
    app.store.submit("Change the period", "b", reply_to=r, expected_revision=1)
    with pytest.raises(WorkbenchError):
        Outcomes(app.store).review(out["id"], "reviewer", "accept", "Reviewed")


def test_obligations_gate_acceptance(app, project):
    r = app.store.submit("Two deliverables", "a", project["id"])["request_id"]
    obs = app.dispatch(
        "workflow.obligations",
        {
            "request_id": r,
            "revision": 1,
            "items": [
                {"description": "Report", "acceptance": "Amount shown"},
                {"description": "Plan", "acceptance": "Owner assigned"},
            ],
        },
    )
    out = Outcomes(app.store).propose(
        r, "Report and plan with explicit limitations.", [], "writer", 1
    )
    with pytest.raises(WorkbenchError):
        Outcomes(app.store).review(out["id"], "reviewer", "accept", "Checked")
    for ident in obs["ids"]:
        app.dispatch(
            "workflow.fulfill", {"obligation_id": ident, "outcome_id": out["id"]}
        )
    Outcomes(app.store).review(
        out["id"], "reviewer", "accept", "Both acceptance criteria checked"
    )
    assert all(
        o["status"] == "accepted" for o in app.store.rows("SELECT * FROM obligations")
    )


def test_lint_is_advisory_not_truth_detector():
    checked = lint("This seamlessly unlocks things. Delve deeper.", 4)
    assert checked["over_word_budget"] and checked["vague_phrases"]
    assert "do not prove" in checked["notice"]


def test_extracted_text_tampering_is_detected(app, project, tmp_path):
    src = source(app, project, tmp_path)
    with app.store.transaction() as db:
        db.execute(
            "UPDATE sources_text SET text=? WHERE id=?", ("Invented amount", src["id"])
        )
    with pytest.raises(WorkbenchError, match="Extracted text"):
        Context(app.store).source(project["id"], src["id"])

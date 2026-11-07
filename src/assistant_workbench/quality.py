from __future__ import annotations

import json
import re
from .context import Context
from .util import WorkbenchError, canonical, now, uid

SLOP = re.compile(
    r"\b(delve|game[ -]changing|seamlessly|in today's fast.paced|unlock the potential|it's worth noting)\b",
    re.I,
)


def lint(text, max_words=600):
    return {
        "word_count": len(text.split()),
        "over_word_budget": len(text.split()) > max_words,
        "vague_phrases": [m.group() for m in SLOP.finditer(text)],
        "notice": "Style checks do not prove correctness or readability.",
    }


class Outcomes:
    def __init__(self, store):
        self.store = store
        self.context = Context(store)

    def propose(self, request_id, body, claims, author, revision):
        req = self.store.one("SELECT * FROM requests WHERE id=?", (request_id,))
        if (
            req["status"] in {"cancelled", "accepted", "grouped"}
            or req["revision"] != revision
        ):
            raise WorkbenchError("Cannot publish a closed or stale request revision")
        if not body.strip() or not author:
            raise WorkbenchError("Outcome body and author are required")
        checks = []
        for claim in claims:
            if set(claim) - {"source_id", "quote", "statement", "kind"} or claim.get(
                "kind"
            ) not in {"fact", "inference"}:
                raise WorkbenchError(
                    "Claims require source_id, quote, statement and kind fact/inference"
                )
            src = self.context.source(req["project_id"], claim["source_id"])
            if not claim.get("quote") or claim["quote"] not in src["text"]:
                raise WorkbenchError("Cited passage does not occur in the source")
            if not claim.get("statement") or claim["statement"] not in body:
                raise WorkbenchError("Claim statement must occur in the outcome")
            checks.append(
                {
                    "source_id": src["id"],
                    "hash": src["hash"],
                    "quote_present": True,
                    "entailment": "requires review",
                }
            )
        result = {
            "style": lint(body),
            "citations": checks,
            "factual_verdict": "not independently reviewed",
        }
        ident = uid("o_")
        with self.store.transaction() as db:
            current = db.execute(
                "SELECT revision,status FROM requests WHERE id=?", (request_id,)
            ).fetchone()
            if current["revision"] != revision or current["status"] == "cancelled":
                raise WorkbenchError("Request changed while drafting")
            db.execute(
                "INSERT INTO outcomes(id,request_id,revision,body,claims,status,checks,author,created) VALUES(?,?,?,?,?,'needs_review',?,?,?)",
                (
                    ident,
                    request_id,
                    revision,
                    body,
                    canonical(claims),
                    canonical(result),
                    author,
                    now(),
                ),
            )
            db.execute(
                "UPDATE requests SET status='needs_review',updated=? WHERE id=?",
                (now(), request_id),
            )
            self.store.event(db, "outcome.proposed", request_id, {"outcome_id": ident})
        return {"id": ident, "status": "needs_review", "checks": result}

    def review(self, outcome_id, reviewer, verdict, notes):
        if verdict not in {"accept", "revise"} or not notes.strip():
            raise WorkbenchError("Review requires accept/revise and concrete notes")
        out = self.store.one("SELECT * FROM outcomes WHERE id=?", (outcome_id,))
        if not reviewer or reviewer == out["author"]:
            raise WorkbenchError("The author cannot be its own independent reviewer")
        req = self.store.one("SELECT * FROM requests WHERE id=?", (out["request_id"],))
        for claim in json.loads(out["claims"]):
            self.context.source(req["project_id"], claim["source_id"])
        with self.store.transaction() as db:
            req = db.execute(
                "SELECT * FROM requests WHERE id=?", (out["request_id"],)
            ).fetchone()
            current = db.execute(
                "SELECT * FROM outcomes WHERE id=?", (outcome_id,)
            ).fetchone()
            if (
                req["revision"] != out["revision"]
                or req["status"] in {"cancelled", "grouped"}
                or current["status"] not in {"needs_review", "needs_revision"}
            ):
                raise WorkbenchError("Outcome is closed, superseded or cancelled")
            for claim in json.loads(out["claims"]):
                source = db.execute(
                    "SELECT * FROM sources WHERE id=?", (claim["source_id"],)
                ).fetchone()
                if source["revoked"]:
                    raise WorkbenchError("Cited source was revoked during review")
            if verdict == "accept":
                pending = db.execute(
                    "SELECT 1 FROM obligations WHERE request_id=? AND revision=? AND (outcome_id IS NULL OR outcome_id!=?) LIMIT 1",
                    (req["id"], req["revision"], outcome_id),
                ).fetchone()
                if pending:
                    raise WorkbenchError(
                        "Not all explicit deliverables are addressed by this outcome"
                    )
            db.execute(
                "INSERT INTO reviews VALUES(?,?,?,?,?,?)",
                (uid("rv_"), outcome_id, reviewer, verdict, notes, now()),
            )
            status = "accepted" if verdict == "accept" else "needs_revision"
            db.execute("UPDATE outcomes SET status=? WHERE id=?", (status, outcome_id))
            db.execute(
                "UPDATE requests SET status=?,updated=? WHERE id=?",
                (status, now(), out["request_id"]),
            )
            if verdict == "accept":
                db.execute(
                    "UPDATE obligations SET status='accepted' WHERE outcome_id=?",
                    (outcome_id,),
                )
            self.store.event(
                db,
                "outcome.reviewed",
                out["request_id"],
                {"outcome_id": outcome_id, "verdict": verdict, "reviewer": reviewer},
            )
        return {"id": outcome_id, "status": status}

from __future__ import annotations

import json
import re
from .context import Context
from .config import config
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
        if (
            not isinstance(body, str)
            or not body.strip()
            or not isinstance(author, str)
            or not author.strip()
        ):
            raise WorkbenchError("Outcome body and author are required")
        author = author.strip()
        if not isinstance(claims, list):
            raise WorkbenchError("Claims must be a list")
        checks = []
        for claim in claims:
            if (
                not isinstance(claim, dict)
                or set(claim) != {"source_id", "quote", "statement", "kind"}
                or not all(isinstance(v, str) for v in claim.values())
                or claim.get("kind") not in {"fact", "inference"}
            ):
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
        audience = config(self.store.home)["audience"]
        result = {
            "style": lint(body, audience["max_words"]),
            "audience": audience,
            "citations": checks,
            "factual_verdict": "not independently reviewed",
        }
        ident = uid("o_")
        with self.store.transaction() as db:
            current = db.execute(
                "SELECT revision,status FROM requests WHERE id=?", (request_id,)
            ).fetchone()
            if current["revision"] != revision or current["status"] in {
                "accepted", "cancelled", "grouped"
            }:
                raise WorkbenchError("Request changed while drafting")
            for claim in claims:
                self.context.source(req["project_id"], claim["source_id"])
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
        if (
            verdict not in {"accept", "revise"}
            or not isinstance(notes, str)
            or not notes.strip()
        ):
            raise WorkbenchError("Review requires accept/revise and concrete notes")
        out = self.store.one("SELECT * FROM outcomes WHERE id=?", (outcome_id,))
        if (
            not isinstance(reviewer, str)
            or not reviewer.strip()
            or reviewer.strip().casefold() == out["author"].strip().casefold()
        ):
            raise WorkbenchError("The author cannot be its own independent reviewer")
        reviewer = reviewer.strip()
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
                or req["status"] in {"accepted", "cancelled", "grouped"}
                or current["status"] not in {"needs_review", "needs_revision"}
            ):
                raise WorkbenchError("Outcome is closed, superseded or cancelled")
            for claim in json.loads(out["claims"]):
                self.context.source(req["project_id"], claim["source_id"])
                source = db.execute(
                    "SELECT * FROM sources WHERE id=?", (claim["source_id"],)
                ).fetchone()
                if source["revoked"]:
                    raise WorkbenchError("Cited source was revoked during review")
            if verdict == "accept":
                explicit = db.execute(
                    "SELECT COUNT(*) FROM obligations WHERE request_id=? AND revision=?",
                    (req["id"], req["revision"]),
                ).fetchone()[0]
                linked = db.execute(
                    "SELECT COUNT(*) FROM obligations WHERE request_id=? AND revision=? AND outcome_id=? AND status='submitted'",
                    (req["id"], req["revision"], outcome_id),
                ).fetchone()[0]
                if explicit and not linked:
                    raise WorkbenchError(
                        "No explicit deliverable is submitted by this outcome"
                    )
                accepted = db.execute(
                    "SELECT DISTINCT o.id,o.status,o.claims FROM obligations b LEFT JOIN outcomes o ON o.id=b.outcome_id WHERE b.request_id=? AND b.revision=? AND b.status='accepted'",
                    (req["id"], req["revision"]),
                ).fetchall()
                for sibling in accepted:
                    if sibling["status"] != "accepted":
                        raise WorkbenchError(
                            "An accepted deliverable is no longer valid; amend the request and review current evidence"
                        )
                    try:
                        for claim in json.loads(sibling["claims"]):
                            self.context.source(req["project_id"], claim["source_id"])
                    except WorkbenchError as exc:
                        raise WorkbenchError(
                            "An accepted deliverable's evidence is no longer valid; amend the request and review current evidence"
                        ) from exc
            db.execute(
                "INSERT INTO reviews VALUES(?,?,?,?,?,?)",
                (uid("rv_"), outcome_id, reviewer, verdict, notes, now()),
            )
            status = "accepted" if verdict == "accept" else "needs_revision"
            db.execute("UPDATE outcomes SET status=? WHERE id=?", (status, outcome_id))
            request_status = status
            remaining = None
            if verdict == "accept":
                db.execute(
                    "UPDATE obligations SET status='accepted' WHERE outcome_id=?",
                    (outcome_id,),
                )
                remaining = db.execute(
                    "SELECT COUNT(*) FROM obligations WHERE request_id=? AND revision=? AND status!='accepted'",
                    (req["id"], req["revision"]),
                ).fetchone()[0]
                if remaining:
                    request_status = "needs_review"
                else:
                    db.execute(
                        "UPDATE outcomes SET status='superseded' WHERE request_id=? AND revision=? AND id!=? AND status IN ('needs_review','needs_revision')",
                        (req["id"], req["revision"], outcome_id),
                    )
            db.execute(
                "UPDATE requests SET status=?,updated=? WHERE id=?",
                (request_status, now(), out["request_id"]),
            )
            self.store.event(
                db,
                "outcome.reviewed",
                out["request_id"],
                {
                    "outcome_id": outcome_id,
                    "verdict": verdict,
                    "reviewer": reviewer,
                    "request_status": request_status,
                    "remaining_deliverables": remaining,
                },
            )
        return {
            "id": outcome_id,
            "status": status,
            "request_status": request_status,
            "remaining_deliverables": remaining,
        }

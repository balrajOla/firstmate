"""Deterministic evaluation accounting; model judges are fallible observations."""

from __future__ import annotations
import json
from .policy import classify
from .util import WorkbenchError, atomic, canonical, now, uid


def routing_cases(cases):
    results = []
    for case in cases:
        predicted = classify(case["prompt"])
        results.append(
            {
                "id": case["id"],
                "expected": case["workflow"],
                "predicted": predicted,
                "passed": predicted == case["workflow"],
            }
        )
    return {
        "kind": "deterministic workflow classification",
        "total": len(results),
        "correct": sum(r["passed"] for r in results),
        "accuracy": sum(r["passed"] for r in results) / max(1, len(results)),
        "cases": results,
    }


def judge_agreement(judgments):
    if not judgments:
        raise WorkbenchError("Judgment pairs are required")
    counts = {"true_accept": 0, "false_accept": 0, "false_reject": 0, "true_reject": 0}
    for j in judgments:
        if j.get("human") not in {"accept", "reject"} or j.get("judge") not in {
            "accept",
            "reject",
        }:
            raise WorkbenchError(
                "Judgments require human and judge accept/reject labels"
            )
        key = (
            ("true_accept" if j["human"] == "accept" else "false_accept")
            if j["judge"] == "accept"
            else ("false_reject" if j["human"] == "accept" else "true_reject")
        )
        counts[key] += 1
    return {
        **counts,
        "agreement": (counts["true_accept"] + counts["true_reject"]) / len(judgments),
        "notice": "Judge agreement is measured on these examples only. Consensus does not establish factual truth.",
    }


def propose_policy(store, kind, candidate, rationale, evidence):
    if (
        kind not in {"model_priorities", "audience"}
        or not rationale.strip()
        or not evidence
    ):
        raise WorkbenchError(
            "Policy changes require a supported kind, rationale and evaluation evidence"
        )
    db = store.connect()
    try:
        db.execute(
            "CREATE TABLE IF NOT EXISTS policy_changes(id TEXT PRIMARY KEY,kind TEXT,candidate TEXT,rationale TEXT,evidence TEXT,status TEXT,before TEXT,created REAL)"
        )
    finally:
        db.close()
    ident = uid("policy_")
    with store.transaction() as db:
        db.execute(
            "INSERT INTO policy_changes VALUES(?,?,?,?,?,'proposed',NULL,?)",
            (ident, kind, canonical(candidate), rationale, canonical(evidence), now()),
        )
    return {
        "id": ident,
        "status": "proposed",
        "notice": "No production configuration changed. Review measured regressions before applying.",
    }


def apply_policy(store, change_id, reviewer, rollback=False):
    from .config import config

    if not reviewer.strip():
        raise WorkbenchError("A reviewer identity is required")
    change = store.one("SELECT * FROM policy_changes WHERE id=?", (change_id,))
    settings = config(store.home)
    if rollback:
        if change["status"] != "applied":
            raise WorkbenchError("Only an applied change can be rolled back")
        before = json.loads(change["before"])
        # A rollback may not overwrite later, unrelated operator changes.
        current = (
            settings["audience"]
            if change["kind"] == "audience"
            else {m["id"]: m.get("priority", 100) for m in settings["models"]}
        )
        if current != json.loads(change["candidate"]):
            raise WorkbenchError(
                "Configuration changed after this policy; reconcile before rollback"
            )
        candidate = before
        status = "rolled_back"
    else:
        if change["status"] != "proposed":
            raise WorkbenchError("Policy change is not pending review")
        before = (
            settings["audience"]
            if change["kind"] == "audience"
            else {m["id"]: m.get("priority", 100) for m in settings["models"]}
        )
        candidate = json.loads(change["candidate"])
        status = "applied"
    if change["kind"] == "audience":
        if (
            set(candidate) != {"reader", "style", "max_words"}
            or not isinstance(candidate["max_words"], int)
            or candidate["max_words"] < 1
        ):
            raise WorkbenchError("Invalid audience profile")
        settings["audience"] = candidate
    else:
        if set(candidate) != {m["id"] for m in settings["models"]} or any(
            not isinstance(v, int) or v < 0 for v in candidate.values()
        ):
            raise WorkbenchError("Priority update must name every configured profile")
        for model in settings["models"]:
            model["priority"] = candidate[model["id"]]
    atomic(store.home / "config.json", canonical(settings) + "\n")
    with store.transaction() as db:
        db.execute(
            "UPDATE policy_changes SET status=?,before=? WHERE id=?",
            (status, canonical(before), change_id),
        )
        store.event(db, "policy." + status, change_id, {"reviewer": reviewer})
    return {"id": change_id, "status": status}

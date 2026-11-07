"""Evidence-backed release gates. Absence is never a pass."""

from __future__ import annotations
import json
import platform
from pathlib import Path
from .util import WorkbenchError, atomic, canonical, digest, now, read_json

GATES = (
    "installed_cli",
    "coordinator",
    "real_workers",
    "context_recovery",
    "terminal_gui",
    "migration_restore",
    "research",
    "quality",
    "soak",
)


def record(store, gate, profile, result, artifacts, notes, observer="operator"):
    if gate not in GATES or not notes.strip():
        raise WorkbenchError("Use a known gate and describe what was actually observed")
    if result == "passed" and not artifacts:
        raise WorkbenchError("Passed gates require retained evidence artifacts")
    files = []
    for name in artifacts:
        p = Path(name).resolve(strict=True)
        if not p.is_file():
            raise WorkbenchError("Evidence must be a file")
        files.append({"path": str(p), "sha256": digest(p.read_bytes())})
    details = {
        "artifacts": files,
        "notes": notes,
        "observer": observer,
        "host": platform.node(),
        "classification": "operator attestation; inspect evidence before release",
    }
    store.record_evidence(gate, profile, result, details)
    return {"gate": gate, "result": result, "details": details}


def qualification(app, profile, output=None, migration=None):
    entries = app.store.rows(
        "SELECT * FROM evidence WHERE profile=? ORDER BY created DESC", (profile,)
    )
    latest = {}
    for e in entries:
        if e["gate"] not in latest:
            latest[e["gate"]] = e
    gates = {}
    details = {}
    for gate in GATES:
        row = latest.get(gate)
        if not row:
            gates[gate] = "not_run"
            continue
        detail = json.loads(row["details"])
        valid = (
            detail.get("host") == platform.node()
            and 0 <= now() - row["created"] < 86400
        )
        for a in detail.get("artifacts", []):
            p = Path(a["path"])
            valid = valid and p.is_file() and digest(p.read_bytes()) == a["sha256"]
        gates[gate] = (
            row["result"]
            if valid and (row["result"] != "passed" or detail.get("artifacts"))
            else "blocked"
        )
        details[gate] = detail
    passed = all(v == "passed" for v in gates.values())
    result = {
        "schema": 1,
        "created": now(),
        "profile": profile,
        "host": platform.node(),
        "qualified": passed,
        "qualified_for_cutover": False,
        "gates": gates,
        "evidence": details,
        "notice": "This report cannot certify unexecuted features, exhaustive edge cases or zero hallucinations.",
    }
    if migration:
        from .migration import verify

        backup = verify(migration)
        result["migration_id"] = backup["id"]
    if output:
        atomic(Path(output), canonical(result) + "\n")
    return result


def cutover(app, profile, migration, custom_changes_report, output):
    from .migration import verify, _legacy_services_stopped

    record = verify(migration)
    _legacy_services_stopped(record)
    result = qualification(app, profile, migration=migration)
    if not result["qualified"]:
        raise WorkbenchError(
            "Target-machine qualification is incomplete; no cutover certificate issued"
        )
    report = Path(custom_changes_report).resolve(strict=True)
    result.update(
        qualified_for_cutover=True,
        legacy_processes_stopped=True,
        custom_changes_reconciled=True,
        custom_changes_report={
            "path": str(report),
            "sha256": digest(report.read_bytes()),
        },
    )
    validate_cutover(result)
    atomic(Path(output), canonical(result) + "\n")
    return result


def validate_cutover(proof):
    if proof.get("host") != platform.node():
        raise WorkbenchError("Qualification was not produced on this machine")
    for gate in GATES:
        entry = proof.get("evidence", {}).get(gate, {})
        if proof.get("gates", {}).get(gate) != "passed" or not entry.get("artifacts"):
            raise WorkbenchError("Missing target-machine evidence for " + gate)
        for item in entry["artifacts"]:
            path = Path(item["path"])
            if not path.is_file() or digest(path.read_bytes()) != item["sha256"]:
                raise WorkbenchError("Cutover evidence changed or is unavailable")
    change = proof.get("custom_changes_report", {})
    path = Path(change.get("path", ""))
    if not path.is_file() or digest(path.read_bytes()) != change.get("sha256"):
        raise WorkbenchError("Local-change reconciliation report is missing or changed")

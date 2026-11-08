"""Evaluate feature evidence without treating fixture passes as live qualification.

Run --write-run-metadata before pytest, then evaluate the matching JUnit files.
This is evidence accounting: signed-in agents, a GUI or semantic correctness are
never inferred from test names, file existence or the evaluator's own tests.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


FEATURE_IDS = [f"PF{i:02d}" for i in range(1, 72)]
MATURITIES = {
    "local-tested", "partial", "adapter-tested", "target-pending",
    "adapter-only", "not-implemented (optional)", "not-implemented",
    "implemented", "qualified", "contract-tested", "integrated-not-qualified",
}
EVIDENCE_KINDS = {
    "live_model", "live_service", "target_gui", "human_review", "load_run",
    "real_package", "local_runtime",
}


class EvidenceError(ValueError):
    pass


def sha256_file(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def source_fingerprint(root):
    root = Path(root).resolve()
    names = {"pyproject.toml", "MANIFEST.in", "features.json", "evals/feature-evaluations.json"}
    names.update(str(p.relative_to(root)) for p in root.glob("requirements-*.lock"))
    for directory in ("src", "tests", "scripts", "evals", "examples", ".github"):
        names.update(
            str(p.relative_to(root)) for p in (root / directory).rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
            and p.suffix in {".py", ".json", ".md", ".lua", ".yaml", ".yml", ".ini", ".cjs", ".sh"}
        )
    inventory = {name: sha256_file(root / name) for name in sorted(names) if (root / name).is_file()}
    return hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def parse_time(value):
    if not isinstance(value, str):
        raise EvidenceError("Evidence timestamps must use ISO 8601 with a timezone")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceError("Invalid evidence timestamp") from exc
    if stamp.tzinfo is None:
        raise EvidenceError("Evidence timestamps require a timezone")
    return stamp.timestamp()


def known_test_functions(root):
    names = set()
    for path in sorted((Path(root) / "tests").glob("test_*.py")):
        tree = ast.parse(path.read_text())
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                names.add(str(path.relative_to(root)) + "::" + node.name)
    return names


def validate_manifest(manifest, features, root=None):
    if manifest.get("schema") != 1:
        raise EvidenceError("Unsupported feature-evaluation schema")
    feature_ids = [f.get("id") for f in features]
    cases = manifest.get("features", [])
    if sorted(feature_ids) != FEATURE_IDS or sorted(c.get("feature_id") for c in cases) != FEATURE_IDS:
        raise EvidenceError("Feature catalog and evaluation manifest must each contain PF01–PF71 exactly once")
    if any(f.get("status") not in MATURITIES for f in features):
        raise EvidenceError("Unknown implementation maturity; review the catalog before evaluating")
    gates = manifest.get("gates", {})
    if not gates:
        raise EvidenceError("Feature evaluations require explicit evidence gate definitions")
    for name, gate in gates.items():
        if not isinstance(name, str) or not gate.get("description") or not gate.get("kinds"):
            raise EvidenceError("Each evidence gate requires a name, description and permitted evidence kinds")
        if set(gate["kinds"]) - EVIDENCE_KINDS:
            raise EvidenceError("Fixture/synthetic evidence cannot be permitted for a live gate")
        for field in ("minimum_metrics", "maximum_metrics"):
            metrics = gate.get(field, {})
            if not isinstance(metrics, dict) or any(
                not isinstance(key, str) or not key
                or isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for key, value in metrics.items()
            ):
                raise EvidenceError("Evidence gate metrics must be named finite numeric thresholds")
    known = known_test_functions(Path(root)) if root else None
    journeys = None
    if root and manifest.get("journey_manifest"):
        path = (Path(root) / manifest["journey_manifest"]).resolve()
        if not path.is_relative_to(Path(root).resolve()) or not path.is_file():
            raise EvidenceError("Journey manifest is missing or outside the repository")
        journey_data = json.loads(path.read_text())
        journey_rows = journey_data.get("scenarios", [])
        ids = [row.get("id") for row in journey_rows]
        if ids != [f"S{i:02d}" for i in range(1, 31)]:
            raise EvidenceError("Journey manifest must contain S01–S30 exactly once in order")
        journeys = {row["id"]: row for row in journey_rows}
    for case in cases:
        if case.get("scenario_id") != "E-" + case["feature_id"]:
            raise EvidenceError("Every feature requires its own stable scenario ID")
        for field in ("scenario", "happy_path", "failure_path", "edge_case", "automated_scope", "limit"):
            if not isinstance(case.get(field), str) or not case[field].strip():
                raise EvidenceError(f"{case['feature_id']} is missing {field}")
        for field in ("prerequisites", "automated_tests", "required_gates", "journey_tags"):
            if not isinstance(case.get(field), list) or any(not isinstance(x, str) or not x for x in case[field]):
                raise EvidenceError(f"{case['feature_id']} requires a string list for {field}")
            if len(case[field]) != len(set(case[field])):
                raise EvidenceError(f"{case['feature_id']} repeats an item in {field}")
        if not case["prerequisites"] or not case["required_gates"]:
            raise EvidenceError("Every complete-feature scenario needs prerequisites and observed evidence")
        if set(case["required_gates"]) - set(gates):
            raise EvidenceError(f"{case['feature_id']} refers to an undefined evidence gate")
        for nodeid in case["automated_tests"]:
            if not nodeid.startswith("tests/test_") or "::test_" not in nodeid:
                raise EvidenceError("Use exact pytest node IDs, not invented descriptive labels")
            if known is not None and nodeid.split("[", 1)[0] not in known:
                raise EvidenceError("Mapped test function does not exist: " + nodeid)
        refs = case.get("journey_scenarios", [])
        if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs) or len(refs) != len(set(refs)):
            raise EvidenceError("Journey scenario references must be a unique list")
        if refs and root and journeys is None:
            raise EvidenceError("Journey references require an existing declared manifest")
        if journeys is not None and any(
            ref not in journeys or case["feature_id"] not in journeys[ref].get("feature_ids", [])
            for ref in refs
        ):
            raise EvidenceError("Feature-to-journey reference does not match its actual scenario")
    return cases


def parse_junit(paths):
    cases, timestamps = {}, []
    for path in paths:
        tree = ET.parse(path)
        suites = list(tree.getroot().iter("testsuite"))
        for suite in suites:
            timestamp = suite.get("timestamp")
            if timestamp:
                timestamps.append(parse_time(timestamp))
            elif suite.findall("testcase"):
                raise EvidenceError("Every JUnit suite with cases requires its own timestamp")
        for item in tree.getroot().iter("testcase"):
            classname = item.get("classname", "")
            name = item.get("name", "")
            if not classname.startswith("tests.") or not name:
                raise EvidenceError("JUnit requires pytest test classnames and names")
            nodeid = classname.replace(".", "/") + ".py::" + name
            status = "passed"
            if item.find("failure") is not None or item.find("error") is not None:
                status = "failed"
            elif item.find("skipped") is not None:
                status = "skipped"
            observed = {"status": status, "seconds": float(item.get("time", 0)), "junit": str(path)}
            previous = cases.get(nodeid)
            # A failure in any supplied run stays visible; a separate installed-
            # package pass may replace its initial skip, never a failure.
            rank = {"skipped": 0, "passed": 1, "failed": 2}
            if previous is None or rank[status] > rank[previous["status"]]:
                cases[nodeid] = observed
    return {"cases": cases, "suite_timestamps": timestamps}


def gate_receipt_status(receipt, gate, root, fingerprint, now):
    if not receipt:
        return "blocked", "No feature-specific observed evidence"
    if receipt.get("status") == "failed":
        return "failed", receipt.get("notes") or "Observed scenario failure"
    if receipt.get("status") != "passed":
        return "blocked", receipt.get("notes") or "Scenario was not completed"
    if receipt.get("kind") not in gate["kinds"]:
        return "blocked", "Wrong evidence kind; fixtures do not certify live features"
    if receipt.get("source_fingerprint") != fingerprint:
        return "blocked", "Evidence does not identify the evaluated implementation"
    if not receipt.get("observer") or not receipt.get("notes"):
        return "blocked", "Evidence requires an observer and concrete observations"
    acceptance = receipt.get("acceptance", {})
    if not isinstance(acceptance, dict) or set(acceptance) != {"happy_path", "failure_path", "edge_case"}:
        return "blocked", "Record all three scenario acceptance paths, not just its happy path"
    if any(value == "failed" for value in acceptance.values()):
        return "failed", "A recorded scenario acceptance path failed"
    if any(value != "passed" for value in acceptance.values()):
        return "blocked", "A scenario acceptance path was not completed"
    metrics = receipt.get("metrics", {})
    if not isinstance(metrics, dict):
        return "blocked", "Observed metrics must be an object"
    for field, lower_bound in (("minimum_metrics", True), ("maximum_metrics", False)):
        for name, bound in gate.get(field, {}).items():
            observed = metrics.get(name)
            if isinstance(observed, bool) or not isinstance(observed, (int, float)) or not math.isfinite(observed):
                return "blocked", "Missing finite observed metric: " + name
            if (lower_bound and observed < bound) or (not lower_bound and observed > bound):
                return "failed", "Observed metric does not meet the required threshold: " + name
    try:
        age = now - parse_time(receipt.get("observed_at"))
    except EvidenceError as exc:
        return "blocked", str(exc)
    if age < -300 or age > gate.get("max_age_hours", 24) * 3600:
        return "blocked", "Evidence is expired or dated in the future"
    artifacts = receipt.get("artifacts", [])
    if not isinstance(artifacts, list) or not artifacts:
        return "blocked", "Passed evidence requires retained artifacts"
    for item in artifacts:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            return "blocked", "Invalid artifact record"
        path = (root / item["path"]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return "blocked", "Artifact is missing or outside the evidence root"
        if item.get("sha256") != sha256_file(path):
            return "blocked", "Artifact hash changed"
    return "passed", "Observed evidence retained; inspect its contents before a release decision"


def evaluate(features, manifest, junit, root, run_metadata=None, receipts=None, now=None):
    root = Path(root).resolve()
    scenarios = validate_manifest(manifest, features, root)
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    fingerprint = source_fingerprint(root)
    binding_errors = []
    if not run_metadata or run_metadata.get("source_fingerprint") != fingerprint:
        binding_errors.append("JUnit is not bound to the current source fingerprint")
    else:
        try:
            started = parse_time(run_metadata.get("started_at"))
            if started > now + 300 or now - started > 86400:
                binding_errors.append("Test-run metadata is expired or dated in the future")
            if not junit["suite_timestamps"] or min(junit["suite_timestamps"]) < started - 5:
                binding_errors.append("JUnit predates this recorded test run or has no timestamp")
            if junit["suite_timestamps"] and max(junit["suite_timestamps"]) > now + 300:
                binding_errors.append("JUnit is dated in the future")
        except EvidenceError as exc:
            binding_errors.append(str(exc))
    index = {}
    if receipts is not None and (not isinstance(receipts, dict) or receipts.get("schema") != 1 or not isinstance(receipts.get("results"), list)):
        raise EvidenceError("Observed evidence requires schema 1 and a results list")
    expected_receipts = {
        (case["feature_id"], case["scenario_id"], gate)
        for case in scenarios for gate in case["required_gates"]
    }
    for receipt in (receipts or {}).get("results", []):
        if not isinstance(receipt, dict):
            raise EvidenceError("Invalid observed evidence record")
        key = (receipt.get("feature_id"), receipt.get("scenario_id"), receipt.get("gate"))
        if key not in expected_receipts:
            raise EvidenceError("Observed evidence does not match a required feature/scenario/gate")
        if key in index:
            raise EvidenceError("Duplicate feature/gate receipts must be reconciled, not silently overwritten")
        index[key] = receipt
    catalog = {f["id"]: f for f in features}
    results = []
    for case in scenarios:
        feature = catalog[case["feature_id"]]
        tests = [{"nodeid": name, **junit["cases"].get(name, {"status": "missing"})} for name in case["automated_tests"]]
        local_status = (
            "failed" if any(t["status"] == "failed" for t in tests)
            else "blocked" if not tests or any(t["status"] != "passed" for t in tests)
            else "passed"
        )
        gates = []
        for gate_id in case["required_gates"]:
            receipt = index.get((case["feature_id"], case["scenario_id"], gate_id))
            status, reason = gate_receipt_status(receipt, manifest["gates"][gate_id], root, fingerprint, now)
            gates.append({"gate": gate_id, "status": status, "reason": reason})
        reasons = list(binding_errors)
        if local_status == "blocked" and tests:
            reasons.append("One or more mapped automated cases were skipped or not run")
        if not tests:
            reasons.append("No direct pytest scenario was mapped; observed scenario evidence is still required")
        if feature["status"] in {"partial", "adapter-only", "contract-tested", "integrated-not-qualified"}:
            reasons.append("Implementation catalog still records incomplete integration")
        reasons.extend(g["reason"] + " (" + g["gate"] + ")" for g in gates if g["status"] == "blocked")
        if local_status == "failed" or any(g["status"] == "failed" for g in gates):
            status = "failed"
        elif feature["status"].startswith("not-implemented"):
            status = "not_implemented"
        elif reasons:
            status = "blocked"
        else:
            status = "passed"
        results.append({
            "feature_id": feature["id"], "name": feature["name"],
            "scenario_id": case["scenario_id"], "implementation_status": feature["status"],
            "journey_scenarios": case.get("journey_scenarios", []),
            "status": status, "local_status": local_status, "tests": tests,
            "required_observations": gates, "reasons": reasons,
            "remaining": feature["remaining"],
        })
    counts = Counter(r["status"] for r in results)
    mapped_tests = {name for case in scenarios for name in case["automated_tests"]}
    unmapped_failures = sorted(
        name for name, result in junit["cases"].items()
        if result["status"] == "failed" and name not in mapped_tests
    )
    return {
        "schema": 1, "generated_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "source_fingerprint": fingerprint,
        "evidence_binding_status": "blocked" if binding_errors else "passed",
        "binding_errors": binding_errors,
        "qualified": all(r["status"] == "passed" for r in results) and not unmapped_failures,
        "unmapped_test_failures": unmapped_failures,
        "counts": {state: counts[state] for state in ("passed", "failed", "blocked", "not_implemented")},
        "local_counts": dict(Counter(r["local_status"] for r in results)),
        "notice": "Scenario evidence accounting, not proof of exhaustive correctness. Test existence, fixtures and hashes do not prove live agent execution or semantic quality. A passed result applies only to the listed scenario and retained observations.",
        "features": results,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--features", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--junit", action="append", type=Path, default=[])
    parser.add_argument("--run-metadata", type=Path)
    parser.add_argument("--live-evidence", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--write-run-metadata", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.write_run_metadata:
            args.write_run_metadata.parent.mkdir(parents=True, exist_ok=True)
            args.write_run_metadata.write_text(json.dumps({
                "schema": 1, "started_at": datetime.now(timezone.utc).isoformat(),
                "source_fingerprint": source_fingerprint(args.root),
            }, indent=2) + "\n")
            return 0
        features = json.loads((args.features or args.root / "features.json").read_text())
        manifest = json.loads((args.manifest or args.root / "evals/feature-evaluations.json").read_text())
        metadata = json.loads(args.run_metadata.read_text()) if args.run_metadata else None
        receipts = json.loads(args.live_evidence.read_text()) if args.live_evidence else None
        result = evaluate(features, manifest, parse_junit(args.junit), args.root, metadata, receipts)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps({"qualified": result["qualified"], "counts": result["counts"], "local_counts": result["local_counts"], "unmapped_test_failures": result["unmapped_test_failures"]}))
        return 1 if result["counts"]["failed"] or result["unmapped_test_failures"] else 0 if result["qualified"] else 3
    except (EvidenceError, OSError, ValueError, ET.ParseError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

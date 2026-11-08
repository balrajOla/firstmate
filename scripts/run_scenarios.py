"""Run thirty durable public-API scenarios without inventing completed products.

All mutations use Application.dispatch. Read-only Store audits check fields not
yet exposed by a public read action. Optional live probes use the configured
coordinator, never a replacement executor or a fabricated assistant result.
"""

from __future__ import annotations

import argparse
from collections import Counter
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from assistant_workbench.app import Application
from assistant_workbench.config import config
from assistant_workbench.util import WorkbenchError


ROOT = Path(__file__).resolve().parents[1]
ACTIONS = {"submit", "amend", "group", "cancel", "reopen", "retry", "stale_amend",
           "assign", "pause", "resume", "context_scope", "oversized", "restart", "outcomes"}
PROJECTS = {"HarborWeb", "PocketTasks", "EvidenceReview"}


class ScenarioError(ValueError):
    pass


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def append_journal(path, value):
    descriptor = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "a") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def validate_manifest(manifest):
    if not isinstance(manifest, dict) or manifest.get("schema") != 1:
        raise ScenarioError("Scenario manifest requires schema 1")
    steps = manifest.get("scenarios", [])
    if [step.get("id") for step in steps] != [f"S{i:02d}" for i in range(1, 31)]:
        raise ScenarioError("Exactly S01–S30 in execution order are required")
    if {p.get("name") for p in manifest.get("projects", [])} != PROJECTS:
        raise ScenarioError("The three named isolated projects are required")
    aliases = set()
    for step in steps:
        if step.get("action") not in ACTIONS or step.get("project") not in PROJECTS | {None}:
            raise ScenarioError("Unsupported action or project")
        if not isinstance(step.get("instruction"), str) or not step["instruction"].strip():
            raise ScenarioError("Every step needs its natural user instruction")
        if not step.get("expectations") or not all(isinstance(x, str) and x for x in step["expectations"]):
            raise ScenarioError("Every step needs explicit expectations")
        features = step.get("feature_ids", [])
        if not features or len(features) != len(set(features)) or any(
            not isinstance(f, str) or not re.fullmatch(r"PF(?:0[1-9]|[1-6][0-9]|7[01])", f) for f in features
        ):
            raise ScenarioError("Every step needs valid unique feature IDs")
        targets = ([step["target"]] if "target" in step else []) + step.get("targets", [])
        if any(target not in aliases for target in targets):
            raise ScenarioError("Scenario target must refer to an earlier request alias")
        if "ref" in step:
            if step["ref"] in aliases:
                raise ScenarioError("Request aliases cannot be reused")
            aliases.add(step["ref"])
        if step["action"] == "oversized":
            payload = step.get("payload", {})
            size = payload.get("target_characters")
            if type(size) is not int or not 24000 < size < 900000 or not payload.get("repeat_line"):
                raise ScenarioError("Large-prompt control needs a bounded explicit payload")
    return steps


def require(condition, explanation):
    if not condition:
        raise AssertionError(explanation)


class ScenarioRunner:
    def __init__(self, home, output, manifest, run_id):
        self.home, self.output = Path(home).resolve(), Path(output).resolve()
        self.manifest, self.run_id = manifest, run_id
        self.app = Application(self.home)
        self.refs, self.original_submissions = {}, {}
        self.snapshots = self.output.parent / (self.output.stem + "-snapshots")
        self.journal = self.output.with_suffix(".jsonl")
        self.projects = {}
        existing = {p["name"]: p for p in self.call("project.list")}
        if self.app.store.rows("SELECT id FROM prompts WHERE substr(client_key,1,?)=? LIMIT 1", (len(run_id) + 1, run_id + ":")):
            raise ScenarioError("Run ID already has durable prompts; use a new run ID and evidence path")
        for name in PROJECTS & existing.keys():
            if existing[name]["repository"] != str(self.home / "scenario-projects" / name):
                raise ScenarioError("Use an isolated scenario home; an existing named project has another repository")
        for name in sorted(PROJECTS):
            repo = self.home / "scenario-projects" / name
            if name in existing:
                self.projects[name] = existing[name]
            else:
                repo.mkdir(parents=True, exist_ok=True)
                self.projects[name] = self.call("project.add", name=name, repository=str(repo))

    def call(self, action, **args):
        return self.app.dispatch(action, args)

    def request(self, alias):
        if alias not in self.refs:
            raise ScenarioError("An earlier required request was not created: " + alias)
        return self.call("request.get", request_id=self.refs[alias])

    def history(self, request):
        # Public history is project scoped; an unresolved request has no project
        # yet, so the saved original is audited read-only until assignment.
        if request["project_id"]:
            rows = self.call("context.history", project=request["project_id"], query="", limit=100)
            return [row for row in rows if row["request_id"] == request["id"]]
        return self.app.store.rows("SELECT id,request_id,body FROM prompts WHERE request_id=? ORDER BY rowid", (request["id"],))

    def obligations(self, request):
        return self.app.store.rows("SELECT * FROM obligations WHERE request_id=? AND revision=? ORDER BY rowid", (request["id"], request["revision"]))

    def submit(self, step, text=None):
        text = step["instruction"] if text is None else text
        args = {"text": text, "client_key": self.run_id + ":" + step["id"]}
        if step["project"]:
            args["project"] = self.projects[step["project"]]["id"]
        receipt = self.call("prompt.submit", **args)
        require(not receipt["duplicate"], "A fresh scenario unexpectedly reused a prompt key")
        if step.get("ref"):
            self.refs[step["ref"]] = receipt["request_id"]
            self.original_submissions[step["ref"]] = {"args": args, "receipt": receipt}
        request = self.call("request.get", request_id=receipt["request_id"])
        require(request["body"] == text, "The original prompt changed during intake")
        if step.get("deliverables"):
            self.call("workflow.obligations", request_id=request["id"], revision=request["revision"], items=[
                {"description": item, "acceptance": "Independently verify: " + item} for item in step["deliverables"]
            ])
            require({x["description"] for x in self.obligations(request)} == set(step["deliverables"]), "A deliverable was lost")
        return receipt

    def audit_snapshot(self):
        return {
            "public_snapshot": self.call("snapshot"),
            "audit": {
                "prompts": self.app.store.rows("SELECT id,client_key,request_id,body FROM prompts ORDER BY rowid"),
                "obligations": self.app.store.rows("SELECT * FROM obligations ORDER BY rowid"),
                "request_links": self.app.store.rows("SELECT * FROM request_links ORDER BY rowid"),
                "project_policy": self.app.store.rows("SELECT * FROM project_policy ORDER BY project_id"),
                "outbox": self.app.store.rows("SELECT id,request_id,revision,operation,status,error FROM outbox ORDER BY rowid"),
            },
            "request_aliases": self.refs,
            "notice": "Actual application state; planned/queued work is not a generated product or a running agent.",
        }

    def step(self, step):
        action = step["action"]
        details = {}
        if action == "submit":
            receipt = self.submit(step)
            req = self.call("request.get", request_id=receipt["request_id"])
            if step["project"] is None:
                require(req["project_id"] is None and req["status"] == "needs_project", "Ambiguous ownership was guessed")
            else:
                require(req["project_id"] == self.projects[step["project"]]["id"], "Wrong destination project")
            details["receipt"] = receipt
        elif action in {"amend", "stale_amend"}:
            before = self.request(step["target"])
            original = self.history(before)
            prior_obligations = {x["description"] for x in self.obligations(before)}
            args = {"text": step["instruction"], "client_key": self.run_id + ":" + step["id"], "reply_to": before["id"], "expected_revision": step.get("expected_revision", before["revision"])}
            if action == "stale_amend":
                try:
                    self.call("prompt.submit", **args)
                except WorkbenchError as exc:
                    require("Revision changed" in str(exc), "Stale control failed for a different reason")
                    details["expected_rejection"] = str(exc)
                else:
                    raise AssertionError("A stale amendment was accepted")
                require(self.request(step["target"]) == before and self.history(before) == original, "Rejected stale work mutated durable state")
            else:
                receipt = self.call("prompt.submit", **args)
                current = self.request(step["target"])
                require(current["revision"] == before["revision"] + 1, "Amendment did not advance exactly once")
                require(before["body"] in current["body"] and step["instruction"] in current["body"], "Amendment lost an earlier requirement")
                history = {p["id"]: p["body"] for p in self.history(current)}
                require(all(history.get(p["id"]) == p["body"] for p in original), "An original prompt was overwritten")
                require(prior_obligations <= {x["description"] for x in self.obligations(current)}, "Amendment discarded a pending deliverable")
                if receipt.get("group"):
                    parent = self.call("request.get", request_id=receipt["group"]["request_id"])
                    pending = self.app.store.rows("SELECT * FROM outbox WHERE request_id=? AND status='pending'", (parent["id"],))
                    require(current["status"] == "grouped" and step["instruction"] in parent["body"] and len(pending) == 1, "Grouped amendment created divergent dispatch")
                details["receipt"] = receipt
        elif action == "group":
            requests = [self.request(alias) for alias in step["targets"]]
            criteria = {x["description"] for req in requests for x in self.obligations(req)}
            receipt = self.call("workflow.group", requests=[r["id"] for r in requests], reason=step["instruction"])
            self.refs[step["ref"]] = receipt["request_id"]
            parent = self.request(step["ref"])
            require(criteria <= {x["description"] for x in self.obligations(parent)}, "Grouping erased an explicit criterion")
            require(all(req["body"] in parent["body"] for req in requests), "Grouping erased an original demand")
            links = self.app.store.rows("SELECT child FROM request_links WHERE parent=? AND kind='group'", (parent["id"],))
            require({x["child"] for x in links} == {r["id"] for r in requests}, "Grouping lost request identity")
            details["receipt"] = receipt
        elif action == "cancel":
            target = self.request(step["target"])
            parents = self.app.store.rows("SELECT parent FROM request_links WHERE child=? AND kind='group'", (target["id"],))
            unrelated = self.request("website")
            details["receipt"] = self.call("request.cancel", request_id=target["id"])
            require(self.request(step["target"])["status"] == "cancelled", "Cancellation was not saved")
            require(self.request("website") == unrelated, "Scoped cancellation changed unrelated website work")
            for link in parents:
                parent = self.call("request.get", request_id=link["parent"])
                require(target["body"] not in parent["body"] and self.request("web_search")["body"] in parent["body"], "Withdrawing a demand lost surviving group scope")
        elif action == "reopen":
            old = self.request(step["target"])
            receipt = self.call("request.reopen", request_id=old["id"])
            self.refs[step["ref"]] = receipt["request_id"]
            fresh = self.request(step["ref"])
            require(fresh["id"] != old["id"] and fresh["body"] == old["body"], "Historical reopen lost content or reused identity")
            require({x["description"] for x in self.obligations(old)} == {x["description"] for x in self.obligations(fresh)}, "Historical reopen lost deliverables")
            require(receipt["reopened_from"] == old["id"], "Reopen lineage is missing")
            details["receipt"] = receipt
        elif action == "retry":
            original = self.original_submissions[step["target"]]
            before = self.audit_snapshot()["audit"]
            receipt = self.call("prompt.submit", **original["args"])
            require(receipt["duplicate"] and receipt["request_id"] == original["receipt"]["request_id"], "Retry created another request")
            require(self.audit_snapshot()["audit"] == before, "Retry changed durable state")
            details["receipt"] = receipt
        elif action == "assign":
            prior = self.request(step["target"])
            grants = self.app.store.rows("SELECT * FROM grants")
            self.call("request.assign", request_id=prior["id"], project=self.projects[step["project"]]["id"])
            current = self.request(step["target"])
            require(current["project_id"] == self.projects[step["project"]]["id"] and current["status"] == "queued", "Explicit routing correction was not applied")
            require(self.app.store.rows("SELECT * FROM grants") == grants, "Assignment silently granted source access")
            details["request_id"] = current["id"]
        elif action in {"pause", "resume"}:
            paused = action == "pause"
            project_id = self.projects[step["project"]]["id"]
            pending = self.app.store.rows("SELECT id,request_id,revision,status FROM outbox ORDER BY rowid")
            receipt = self.call("workflow.project_policy", project=project_id, paused=paused)
            require(receipt["paused"] == paused, "Project pause state differs from the request")
            require(self.app.store.one("SELECT paused FROM project_policy WHERE project_id=?", (project_id,))["paused"] == int(paused), "Pause was not durable")
            require(self.app.store.rows("SELECT id,request_id,revision,status FROM outbox ORDER BY rowid") == pending, "Pause/resume changed queued intent")
            require(not self.app.store.rows("SELECT * FROM project_policy WHERE project_id!=? AND paused=1", (project_id,)), "Another project was unexpectedly paused")
            details["receipt"] = receipt
        elif action == "context_scope":
            details["receipt"] = self.submit(step)
            corpus = self.home / "scenario-inputs" / self.run_id
            corpus.mkdir(parents=True, exist_ok=True)
            research = corpus / "research-control-note.md"
            research.write_text("SCENARIO CONTROL NOTE, not a paper finding. EvidenceReview must compare retrieval and long context; preserve counterevidence. SCOPE-RESEARCH-ONLY\n")
            private = corpus / "website-private-note.md"
            private.write_text("SCENARIO CONTROL NOTE, not production data. HarborWeb private marker SCOPE-WEBSITE-PRIVATE\n")
            research_id = self.projects["EvidenceReview"]["id"]
            source = self.call("context.ingest", project=research_id, path=str(research))
            foreign = self.call("context.ingest", project=self.projects["HarborWeb"]["id"], path=str(private))
            hits = self.call("context.search", project=research_id, query="SCOPE-RESEARCH-ONLY")
            require(any(row["id"] == source["id"] for row in hits), "Scoped own-source retrieval failed")
            require(not self.call("context.search", project=research_id, query="SCOPE-WEBSITE-PRIVATE"), "Private source leaked through search")
            try:
                self.call("context.source", project=research_id, source_id=foreign["id"])
            except WorkbenchError:
                details["foreign_read"] = "denied"
            else:
                raise AssertionError("Private source read was allowed without a grant")
            details["source_id"] = source["id"]
            details["packet"] = self.call("context.packet", project=research_id, query="SCOPE-RESEARCH-ONLY", max_chars=1000)
            require("not proof" in details["packet"]["coverage"], "Targeted retrieval claimed exhaustive understanding")
        elif action == "oversized":
            payload = step["payload"]
            checklist = (payload["repeat_line"] + "\n") * (payload["target_characters"] // (len(payload["repeat_line"]) + 1) + 1)
            text = step["instruction"] + "\n\n" + checklist
            details["receipt"] = self.submit(step, text)
            path = self.home / "scenario-inputs" / (self.run_id + "-long-checklist.md")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(checklist)
            project_id = self.projects[step["project"]]["id"]
            source = self.call("context.ingest", project=project_id, path=str(path))
            # A single large paragraph must be visibly omitted, never silently
            # truncated. This is a context-packet check, not a model rollover.
            packet = self.call("context.packet", project=project_id, query="Acceptance", max_chars=1000)
            require(packet["used_chars"] <= 1000, "Context packet exceeded its declared bound")
            require(source["id"] in packet["omitted"], "Oversized checklist did not have an explicit omission record")
            details.update(prompt_chars=len(text), original_sha256=hashlib.sha256(text.encode()).hexdigest(), context_packet=packet,
                           execution_preflight={"status": "blocked", "reason": "Execution-packet guard requires a qualified configured worker path; this step only exercised public context assembly."})
        elif action == "restart":
            before = {alias: self.request(alias) for alias in self.refs}
            self.app = Application(self.home)
            require(all(self.request(alias) == req for alias, req in before.items()), "Fresh controller lost or changed saved state")
            details["receipt"] = self.submit(step)
            details["recovered_requests"] = len(before)
        elif action == "outcomes":
            details["receipt"] = self.submit(step)
            outcomes = {name: self.call("outcome.list", project=project["id"]) for name, project in self.projects.items()}
            require(all(row["request_id"] in {req["id"] for req in self.call("snapshot")["requests"] if req["project_id"] == self.projects[name]["id"]} for name, rows in outcomes.items() for row in rows), "Outcome view crossed project ownership")
            details["outcomes_by_project"] = outcomes
            details["product_completion"] = "Not inferred from saved prompts, state snapshots, source fixtures or empty outcome lists."
        else:
            raise ScenarioError("Unsupported scenario action")
        return details


def inspect_delivery(app, refs):
    """Report candidate evidence without manufacturing semantic acceptance."""
    projects = {}
    for alias in ("website", "mobile", "research"):
        if alias not in refs:
            projects[alias] = {"status": "blocked", "missing": ["Initial request was not created"]}
            continue
        request = app.dispatch("request.get", {"request_id": refs[alias]})
        outcomes = [row for row in app.dispatch("outcome.list", {"project": request["project_id"]}) if row["request_id"] == request["id"] and row["revision"] == request["revision"]]
        missing = []
        execution = []
        accepted = []
        for outcome in outcomes:
            checks = json.loads(outcome["checks"])
            if checks.get("execution", {}).get("run_id"):
                execution.append(outcome["id"])
            if outcome["status"] == "accepted":
                reviews = app.store.rows("SELECT * FROM reviews WHERE outcome_id=? AND verdict='accept'", (outcome["id"],))
                if any(row["reviewer"].strip().casefold() != outcome["author"].strip().casefold() for row in reviews):
                    accepted.append(outcome["id"])
        if not execution:
            missing.append("No current outcome with an actual coordinator run receipt")
        if not accepted:
            missing.append("No current outcome with a distinct accepting reviewer")
        # Existing artifact manifests bind a workspace, not an individual
        # requirement revision. File presence alone cannot certify this task.
        missing.append("Current-revision artifact, build/device/browser and source correctness still require independent verification")
        projects[alias] = {"status": "blocked", "request_id": request["id"], "revision": request["revision"], "request_status": request["status"], "execution_candidate_outcomes": execution, "review_candidate_outcomes": accepted, "missing": missing}
    return {"status": "blocked", "qualified": False, "projects": projects,
            "notice": "This runner does not infer usable websites, mobile binaries or verified research from status strings or fixture text. Use the separate feature evidence gates."}


def coordinator_probe(home, action, destination, timeout):
    """Bound a synchronous public action even if its service is unresponsive."""
    command = [sys.executable, str(Path(__file__).resolve()), "--home", str(home), "--output", str(destination), "--probe-action", action]
    try:
        completed = subprocess.run(command, timeout=timeout, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    except subprocess.TimeoutExpired:
        return {"action": action, "status": "blocked", "reason": "Coordinator call reached the bounded deadline; any uncertain effect requires reconciliation"}
    if not destination.is_file():
        return {"action": action, "status": "blocked", "reason": "Coordinator probe produced no durable receipt", "exit_code": completed.returncode}
    return json.loads(destination.read_text())


def run_scenarios(home, output, *, manifest_path=None, live=False, max_wait_seconds=30, on_step=None, run_id=None):
    """Persist each step before on_step(step_id, result); never run shell callbacks.

    The callback may return an observation dictionary, e.g. an actual terminal
    screenshot path. Its exceptions become visible capture failures. The runner
    preserves configuration and never accepts an outcome on the user's behalf.
    """
    if type(max_wait_seconds) not in (int, float) or not 0 < max_wait_seconds <= 300:
        raise ScenarioError("Use a bounded live wait of more than zero and at most 300 seconds")
    run_id = run_id or "scenario-" + uuid.uuid4().hex[:12]
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", run_id):
        raise ScenarioError("Run ID must be a safe unique identifier")
    manifest_path = Path(manifest_path or ROOT / "evals/scenarios-30.json")
    manifest = json.loads(manifest_path.read_text())
    steps = validate_manifest(manifest)
    output = Path(output).resolve()
    journal = output.with_suffix(".jsonl")
    if output.is_relative_to(Path(home).resolve()):
        raise ScenarioError("Write scenario evidence outside the runtime home so it cannot overwrite application state")
    if output == journal:
        raise ScenarioError("The JSON report and JSONL journal need distinct paths; use a .json report name")
    if output.exists() or journal.exists():
        raise ScenarioError("Evidence already exists; use a new output path instead of overwriting a previous run")
    runner = ScenarioRunner(home, output, manifest, run_id)
    report = {"schema": 1, "run_id": run_id, "mode": "live" if live else "intake-control",
              "home": str(runner.home), "started_at": timestamp(), "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
              "steps": [], "projects": runner.projects, "capture_failures": [],
              "notice": "Bookkeeping tests are distinct from actual agent execution, artifact correctness and independent acceptance."}
    save_json(output, report)
    append_journal(journal, {"type": "run_started", "run_id": run_id, "started_at": report["started_at"]})
    for step in steps:
        result = {"id": step["id"], "action": step["action"], "instruction": step["instruction"],
                  "feature_ids": step["feature_ids"], "expectations": step["expectations"], "started_at": timestamp()}
        started = time.monotonic()
        try:
            result["details"] = runner.step(step)
            result["status"] = "passed"
        except ScenarioError as exc:
            result.update(status="blocked", error=str(exc))
        except (AssertionError, WorkbenchError, OSError, ValueError, KeyError, TypeError) as exc:
            result.update(status="failed", error=str(exc), error_type=type(exc).__name__)
        result.update(finished_at=timestamp(), seconds=round(time.monotonic() - started, 4))
        snapshot = runner.snapshots / (step["id"] + ".json")
        save_json(snapshot, runner.audit_snapshot())
        result["snapshot"] = str(snapshot)
        report["steps"].append(result)
        report["request_aliases"] = dict(runner.refs)
        save_json(output, report)
        append_journal(journal, {"type": "step", **result})
        if on_step:
            try:
                observed = on_step(step["id"], copy.deepcopy(result))
                if observed is not None:
                    if not isinstance(observed, dict):
                        raise ScenarioError("on_step must return an observation object or None")
                    result["observation"] = observed
            except Exception as exc:
                error = {"step": step["id"], "error_type": type(exc).__name__, "error": str(exc)}
                report["capture_failures"].append(error)
                result["observation"] = {"status": "failed", **error}
            save_json(output, report)
            append_journal(journal, {"type": "observation", "id": step["id"], "observation": result.get("observation")})
    if live:
        deadline = time.monotonic() + max_wait_seconds
        probes = []
        while time.monotonic() < deadline:
            for action in ("coordinator.tick", "coordinator.observe"):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                destination = runner.snapshots / ("probe-" + str(len(probes) + 1) + ".json")
                receipt = coordinator_probe(runner.home, action, destination, remaining)
                probes.append(receipt)
                append_journal(journal, {"type": "coordinator_probe", **receipt})
            if any(item.get("status") == "blocked" for item in probes[-2:]):
                break
            time.sleep(min(.25, max(0, deadline - time.monotonic())))
        report["live_probes"] = probes
    report["delivery"] = inspect_delivery(runner.app, runner.refs)
    report["counts"] = dict(Counter(step["status"] for step in report["steps"]))
    report["bookkeeping_status"] = "passed" if all(x["status"] == "passed" for x in report["steps"]) else "failed" if report["counts"].get("failed") else "blocked"
    report["durable_prompts"] = len(runner.audit_snapshot()["audit"]["prompts"])
    report["finished_at"] = timestamp()
    save_json(runner.snapshots / "final.json", runner.audit_snapshot())
    save_json(output, report)
    append_journal(journal, {"type": "run_finished", "bookkeeping_status": report["bookkeeping_status"], "delivery_status": report["delivery"]["status"], "finished_at": report["finished_at"]})
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--max-wait-seconds", type=float, default=30)
    parser.add_argument("--run-id")
    parser.add_argument("--probe-action", choices=["coordinator.tick", "coordinator.observe"], help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.probe_action:
            try:
                value = Application(args.home).dispatch(args.probe_action)
                receipt = {"action": args.probe_action, "status": "observed", "result": value}
                if isinstance(value, dict) and value.get("status") in {"blocked", "uncertain", "retry"}:
                    receipt.update(status="blocked", reason=value.get("error") or "Coordinator work remains " + value["status"])
            except Exception as exc:
                receipt = {"action": args.probe_action, "status": "blocked", "reason": str(exc), "error_type": type(exc).__name__}
            save_json(args.output, receipt)
            return 0 if receipt["status"] == "observed" else 3
        report = run_scenarios(args.home, args.output, manifest_path=args.manifest, live=args.live,
                               max_wait_seconds=args.max_wait_seconds, run_id=args.run_id)
        print(json.dumps({"bookkeeping_status": report["bookkeeping_status"], "counts": report["counts"], "durable_prompts": report["durable_prompts"], "delivery_status": report["delivery"]["status"], "capture_failures": len(report["capture_failures"])}))
        return 1 if report["bookkeeping_status"] == "failed" or report["capture_failures"] else 3 if report["bookkeeping_status"] == "blocked" or args.live else 0
    except (ScenarioError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

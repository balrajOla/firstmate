"""Translate runtime observations into durable, unreviewed operator results.

A completed workflow is evidence of execution only. It never grants acceptance,
marks obligations fulfilled, or treats generated text as a factual source.
"""

from __future__ import annotations

import fcntl
import json

from .quality import Outcomes
from .util import WorkbenchError, atomic, canonical, digest, now, read_json


def reconcile_observations(store, observed):
    if not isinstance(observed, dict):
        raise WorkbenchError("Runtime observations must be an object")
    with open(store.home / "observation-reconcile.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = read_json(store.home / "observations.json", {})
        normalized = {}
        # A single-request refresh must not erase unrelated project progress.
        for identity, items in previous.items() if isinstance(previous, dict) else []:
            request = store.rows(
                "SELECT revision FROM requests WHERE id=?", (identity,)
            )
            if (
                request
                and isinstance(items, dict)
                and all(
                    isinstance(item, dict)
                    and item.get("revision") == request[0]["revision"]
                    for item in items.values()
                )
            ):
                normalized[identity] = items
        for key, observation in observed.items():
            if not isinstance(observation, dict) or ":" not in key:
                raise WorkbenchError("Invalid runtime observation")
            request_id, raw_revision = key.rsplit(":", 1)
            req = store.one("SELECT * FROM requests WHERE id=?", (request_id,))
            if (
                raw_revision != str(req["revision"])
                or observation.get("revision") != req["revision"]
            ):
                continue
            plan = json.loads(req["plan"] or "{}")
            nodes = plan.get("nodes", [])
            state = observation.get("status", "unknown")
            result = observation.get("result") or {}
            snapshot = observation.get("snapshot") or {}
            node_results = result.get("nodes", {})
            node_states = snapshot.get("nodes", {})
            items = {}
            for node in nodes:
                detail = node_results.get(node["id"], {})
                progress = node_states.get(node["id"], {})
                items[node["id"]] = {
                    "status": (
                        "done; review required"
                        if detail
                        else progress.get("status", state)
                    ),
                    "workflow_id": observation.get("workflow_id"),
                    "executionRunId": observation.get("run_id"),
                    "host_id": detail.get("host_id", progress.get("host_id")),
                    "revision": req["revision"],
                    "observed_at": now(),
                    "execution_only": True,
                }
            normalized[request_id] = items
            if state in {"failed", "terminated", "timed_out"} and req["status"] not in {
                "accepted",
                "cancelled",
                "grouped",
                "needs_revision",
            }:
                with store.transaction() as db:
                    changed = db.execute(
                        "UPDATE requests SET status='needs_revision',updated=? WHERE id=? AND revision=? AND status NOT IN ('accepted','cancelled','grouped','needs_revision')",
                        (now(), request_id, req["revision"]),
                    ).rowcount
                    if changed:
                        store.event(
                            db,
                            "execution.failed",
                            request_id,
                            {
                                "backend": "temporal",
                                "state": state,
                                "revision": req["revision"],
                                "notice": "Inspect the worker receipt and amend requirements before retrying; no outcome was accepted.",
                            },
                        )
            if state != "completed" or req["status"] in {
                "accepted",
                "cancelled",
                "grouped",
            }:
                continue
            if (
                result.get("request_id") != request_id
                or result.get("revision") != req["revision"]
            ):
                raise WorkbenchError(
                    "Completed result identity does not match its request"
                )
            if set(node_results) != {node["id"] for node in nodes}:
                raise WorkbenchError("Completed result is missing planned nodes")
            for node in nodes:
                value = node_results[node["id"]]
                if (
                    not isinstance(value, dict)
                    or not isinstance(value.get("body"), str)
                    or not value["body"].strip()
                ):
                    raise WorkbenchError("Completed node has no assistant output")
                if (
                    value.get("exit_code") != 0
                    or value.get("node") != node["id"]
                    or value.get("role") != node["role"]
                ):
                    raise WorkbenchError(
                        "Completed node receipt does not match the plan"
                    )
            # Use the last producing role, keeping QA output as review evidence.
            # Topological order is derived from dependencies, not list position.
            remaining = {n["id"]: n for n in nodes}
            ordered = []
            while remaining:
                batch = [
                    n
                    for n in remaining.values()
                    if set(n.get("depends_on", [])).issubset({x["id"] for x in ordered})
                ]
                if not batch:
                    raise WorkbenchError("Observed plan has a dependency cycle")
                ordered.extend(batch)
                for n in batch:
                    del remaining[n["id"]]
            producers = [n for n in ordered if n["role"] != "qa"] or ordered
            producer = producers[-1]
            body = node_results[producer["id"]]["body"]
            author = (
                "temporal:"
                + str(observation.get("workflow_id"))
                + ":"
                + str(observation.get("run_id"))
            )
            existing = store.rows(
                "SELECT * FROM outcomes WHERE request_id=? AND revision=? AND author=?",
                (request_id, req["revision"], author),
            )
            if existing:
                if any(row["body"] != body for row in existing):
                    raise WorkbenchError(
                        "Runtime output changed after its first observation"
                    )
                continue
            proposed = Outcomes(store).propose(
                request_id, body, [], author, req["revision"]
            )
            checks = proposed["checks"]
            checks["execution"] = {
                "backend": "temporal",
                "workflow_id": observation.get("workflow_id"),
                "run_id": observation.get("run_id"),
                "output_hash": digest(canonical(result)),
                "producer": producer["id"],
                "node_receipts": {
                    k: {
                        field: v.get(field)
                        for field in (
                            "node",
                            "role",
                            "host_id",
                            "stdout_sha256",
                            "exit_code",
                        )
                    }
                    for k, v in node_results.items()
                },
                "reviewer_outputs": {
                    n["id"]: node_results[n["id"]]["body"]
                    for n in ordered
                    if n["role"] == "qa"
                },
                "notice": "Execution receipts and reviewer text are not independent acceptance or factual verification. Check artifacts, sources and obligations before accepting.",
            }
            with store.transaction() as db:
                db.execute(
                    "UPDATE outcomes SET checks=? WHERE id=?",
                    (canonical(checks), proposed["id"]),
                )
        atomic(store.home / "observations.json", canonical(normalized))
        fcntl.flock(lock, fcntl.LOCK_UN)
    return observed

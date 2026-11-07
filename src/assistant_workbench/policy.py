from __future__ import annotations

import json
import re
from pathlib import Path
from .util import WorkbenchError, digest

TEMPLATES = {
    "simple": [("worker", "general", [])],
    "code": [("implement", "engineer", []), ("verify", "qa", ["implement"])],
    "design": [
        ("design", "engineer", []),
        ("critique", "qa", ["design"]),
        ("plan", "pm", ["critique"]),
    ],
    "report": [("draft", "general", []), ("edit", "qa", ["draft"])],
    "research": [
        ("research-a", "researcher", []),
        ("research-b", "researcher", []),
        ("synthesize", "general", ["research-a", "research-b"]),
        ("verify", "qa", ["synthesize"]),
    ],
}
WORKFLOW_SKILLS = {
    "simple": [],
    "code": ["test-and-review"],
    "design": ["brainstorm", "write-plan"],
    "report": ["clear-writing"],
    "research": ["research", "clear-writing"],
}


def classify(text):
    if re.search(r"\b(research|investigate|compare)\b", text, re.I):
        return "research"
    if re.search(r"\b(architecture|brainstorm|design|plan)\b", text, re.I):
        return "design"
    if re.search(r"\b(code|implement|build|bug|test|fix|function)\b", text, re.I):
        return "code"
    if re.search(r"\b(report|document|summarize|write)\b", text, re.I):
        return "report"
    return "simple"


def select_model(config, kind, override=None):
    eligible = []
    reasons = []
    for m in config["models"]:
        why = []
        if override and m["id"] != override:
            why.append("does not match explicit choice")
        from .profiles import readiness

        ready, qualification_reason = readiness(m)
        if not ready:
            why.append(qualification_reason)
        if kind not in m["classes"] and "all" not in m["classes"]:
            why.append("not qualified for this task class")
        if m.get("effort") not in m.get("efforts", []):
            why.append("unsupported effort")
        reasons.append({"profile": m["id"], "eligible": not why, "reasons": why})
        if not why:
            eligible.append(m)
    if not eligible:
        raise WorkbenchError(
            "No qualified model profile for "
            + kind
            + "; configure and qualify an explicit harness/model/effort/auth combination"
        )
    chosen = sorted(eligible, key=lambda m: (m.get("priority", 100), m["id"]))[0]
    return chosen, {
        "selected": chosen["id"],
        "reason": (
            "Explicit choice"
            if override
            else "First qualified profile in configured priority order; not an inferred benchmark ranking"
        ),
        "candidates": reasons,
    }


def skill_packets(config, names):
    packets = []
    builtins = Path(__file__).parent / "assets"
    for name in names:
        configured = config.get("skills", {}).get(name)
        paths = (
            [Path(x) for x in configured]
            if isinstance(configured, list)
            else [Path(configured)] if configured else [builtins / (name + ".md")]
        )
        for path in paths:
            if not path.is_file():
                raise WorkbenchError("Required procedure is unavailable: " + name)
            text = path.read_text()
            packets.append(
                {"name": name, "sha256": digest(text), "text": text, "path": str(path)}
            )
    return packets


def compile_plan(request, config, kind=None, model_override=None):
    kind = kind or classify(request["body"])
    if kind not in TEMPLATES:
        raise WorkbenchError("Unknown workflow")
    if re.search(r"\bsuperpowers?\b", request["body"], re.I) and not config.get(
        "skills", {}
    ).get("brainstorm"):
        raise WorkbenchError(
            "Superpowers was explicitly requested but its pinned procedures have not been activated"
        )
    model, decision = select_model(config, kind, model_override)
    nodes = [
        {
            "id": i,
            "role": role,
            "depends_on": deps,
            "model_profile": model["id"],
            "lifetime": "task",
            "parent": None,
        }
        for i, role, deps in TEMPLATES[kind]
    ]
    counts = {}
    for node in nodes:
        node["slot"] = counts.get(node["role"], 0)
        counts[node["role"]] = node["slot"] + 1
    plan = {
        "schema": 1,
        "request_id": request["id"],
        "revision": request["revision"],
        "workflow": kind,
        "nodes": nodes,
        "model": model,
        "selection": decision,
        "skills": skill_packets(config, WORKFLOW_SKILLS[kind]),
        "limits": config["limits"],
        "audience": config["audience"],
    }
    validate_plan(plan, config)
    return plan


def validate_plan(plan, config):
    nodes = plan.get("nodes", [])
    if not nodes or len(nodes) > config["limits"]["max_agents"]:
        raise WorkbenchError("Team exceeds configured capacity or is empty")
    ids = [n["id"] for n in nodes]
    if len(ids) != len(set(ids)):
        raise WorkbenchError("Duplicate team node")
    configured = {m["id"]: m for m in config["models"]}
    if (
        plan.get("model", {}).get("id") not in configured
        or plan["model"] != configured[plan["model"]["id"]]
    ):
        raise WorkbenchError("Plan model must match an installed configuration profile")
    if any(n["model_profile"] != plan["model"]["id"] for n in nodes):
        raise WorkbenchError("Mixed-profile teams require separate qualified plans")
    graph = {n["id"]: n["depends_on"] for n in nodes}
    visiting = set()
    visited = set()

    def visit(node):
        if node not in graph:
            raise WorkbenchError("Missing dependency")
        if node in visiting:
            raise WorkbenchError("Cyclic team dependency")
        if node in visited:
            return
        visiting.add(node)
        for dep in graph[node]:
            visit(dep)
        visiting.remove(node)
        visited.add(node)

    for node in ids:
        visit(node)
    for node in nodes:
        depth = 0
        parent = node.get("parent")
        seen = {node["id"]}
        while parent:
            if parent not in ids or parent in seen:
                raise WorkbenchError("Invalid child ownership")
            seen.add(parent)
            depth += 1
            parent = next(n for n in nodes if n["id"] == parent).get("parent")
        if depth > config["limits"]["max_child_depth"]:
            raise WorkbenchError("Child depth exceeded")
    return plan

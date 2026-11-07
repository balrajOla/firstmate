from __future__ import annotations

import html
import json
from pathlib import Path
from .util import atomic, canonical, now, safe_text


def plan_svg(plan):
    nodes = plan.get("nodes", [])
    positions = {}
    levels = {}

    def level(node, seen=frozenset()):
        if node["id"] in seen:
            return 0
        return 1 + max(
            (
                level(next(n for n in nodes if n["id"] == d), seen | {node["id"]})
                for d in node["depends_on"]
            ),
            default=-1,
        )

    for node in nodes:
        row = level(node)
        column = levels.get(row, 0)
        levels[row] = column + 1
        positions[node["id"]] = (20 + column * 240, 25 + row * 100)
    width = max(320, max(levels.values(), default=1) * 240 + 20)
    height = max(100, (max(levels.keys(), default=0) + 1) * 100 + 20)
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="Task dependencies">',
        '<defs><marker id="arrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6" fill="#526576"/></marker></defs>',
    ]
    for n in nodes:
        x, y = positions[n["id"]]
        for dep in n["depends_on"]:
            a, b = positions[dep]
            svg.append(
                f'<path d="M{a+100},{b+52} L{x+100},{y}" stroke="#526576" fill="none" marker-end="url(#arrow)"/>'
            )
    for n in nodes:
        x, y = positions[n["id"]]
        svg.append(
            f'<rect x="{x}" y="{y}" width="210" height="52" rx="8" fill="#eaf3f8" stroke="#264c60"/><text x="{x+10}" y="{y+21}" font-family="sans-serif" font-size="14">{html.escape(n["id"])}</text><text x="{x+10}" y="{y+40}" font-family="sans-serif" font-size="12">{html.escape(n["role"])}</text>'
        )
    return "".join(svg) + "</svg>"


def export_report(store, request_id, directory):
    req = store.one("SELECT * FROM requests WHERE id=?", (request_id,))
    outcomes = store.rows(
        "SELECT * FROM outcomes WHERE request_id=? ORDER BY created DESC", (request_id,)
    )
    prompts = store.rows(
        "SELECT * FROM prompts WHERE request_id=? ORDER BY created", (request_id,)
    )
    links = store.rows("SELECT * FROM request_links WHERE parent=?", (request_id,))
    for link in links:
        prompts += store.rows(
            "SELECT * FROM prompts WHERE request_id=?", (link["child"],)
        )
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    plan = json.loads(req["plan"]) if req["plan"] else None
    body = outcomes[0]["body"] if outcomes else "No outcome has been submitted."
    status = outcomes[0]["status"] if outcomes else req["status"]
    checks = outcomes[0]["checks"] if outcomes else "{}"
    page = (
        """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'">
<title>Assistant Workbench outcome</title><style>body{max-width:960px;margin:3rem auto;padding:0 1rem;font:17px/1.6 system-ui;color:#213440;background:#fbfcfd}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}small{color:#4b6270}details{border-top:1px solid #b6c7ce;padding:1rem 0}svg{max-height:480px;width:100%}h1{line-height:1.2}</style>"""
        + "<h1>"
        + html.escape(safe_text(req["title"]))
        + "</h1><small>Status: "
        + html.escape(status)
        + " · Requirement revision "
        + str(req["revision"])
        + "</small><pre>"
        + html.escape(safe_text(body))
        + "</pre>"
        + ("<h2>Task dependencies</h2>" + plan_svg(plan) if plan else "")
        + "<details><summary>Evidence and checks</summary><pre>"
        + html.escape(json.dumps(json.loads(checks), indent=2))
        + "</pre></details>"
        + "<details><summary>Original prompts</summary><pre>"
        + html.escape("\n\n".join(p["body"] for p in prompts))
        + "</pre></details></html>"
    )
    record = {
        "schema": 1,
        "request": req,
        "outcomes": outcomes,
        "prompts": prompts,
        "created": now(),
    }
    # Immutable export directories are preferred: never silently overwrite a human edit.
    for name in ("outcome.html", "outcome.md", "outcome.json"):
        if (directory / name).exists():
            raise FileExistsError("Choose a new export directory: " + str(directory))
    atomic(directory / "outcome.html", page)
    atomic(
        directory / "outcome.md",
        "# "
        + safe_text(req["title"])
        + "\n\nStatus: "
        + status
        + "\n\n"
        + safe_text(body)
        + "\n",
    )
    atomic(directory / "outcome.json", canonical(record) + "\n")
    return {
        "html": str(directory / "outcome.html"),
        "markdown": str(directory / "outcome.md"),
        "json": str(directory / "outcome.json"),
        "status": status,
        "notice": "Rendering is separate from factual review. Exports are snapshots; source revocations require re-export.",
    }

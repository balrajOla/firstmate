from __future__ import annotations

import html
import json
from contextlib import closing
from pathlib import Path

from .context import Context
from .util import WorkbenchError, atomic, canonical, now, safe_text


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


def _report_snapshot(store, request_id):
    """Read related records from one SQLite snapshot while other work continues."""
    with closing(store.connect()) as db:
        db.execute("BEGIN")

        def rows(sql, params=()):
            return [dict(row) for row in db.execute(sql, params)]

        requests = rows("SELECT * FROM requests WHERE id=?", (request_id,))
        if not requests:
            raise WorkbenchError("Record not found")
        req = requests[0]
        projects = rows("SELECT * FROM projects WHERE id=?", (req["project_id"],))
        outcomes = rows(
            "SELECT * FROM outcomes WHERE request_id=? ORDER BY created DESC,rowid DESC",
            (request_id,),
        )
        # A corrupted link must not pull another project's prompts into this report.
        prompts = rows(
            "SELECT p.* FROM prompts p JOIN requests r ON r.id=p.request_id "
            "WHERE r.project_id IS ? AND (r.id=? OR r.id IN "
            "(SELECT child FROM request_links WHERE parent=?)) ORDER BY p.created,p.id",
            (req["project_id"], request_id, request_id),
        )
        obligations = rows(
            "SELECT * FROM obligations WHERE request_id=? AND revision=? ORDER BY rowid",
            (request_id, req["revision"]),
        )
        reviews = rows(
            "SELECT v.* FROM reviews v JOIN outcomes o ON o.id=v.outcome_id "
            "WHERE o.request_id=? ORDER BY v.created,v.id", (request_id,),
        )
    return req, projects[0] if projects else None, outcomes, prompts, obligations, reviews


def _escaped(value):
    return html.escape(safe_text(str(value)))


def _markdown_cell(value):
    return safe_text(str(value)).replace("|", "\\|").replace("\n", " ")


def export_report(store, request_id, directory):
    req, project, outcomes, prompts, obligations, reviews = _report_snapshot(store, request_id)
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    # Immutable export directories never silently replace a human edit.
    for name in ("outcome.html", "outcome.md", "outcome.json"):
        if (directory / name).exists():
            raise FileExistsError("Choose a new export directory: " + str(directory))

    plan = json.loads(req["plan"]) if req["plan"] else None
    current = [o for o in outcomes if o["revision"] == req["revision"]]
    context = Context(store)
    sources = {}
    invalid = {}
    for outcome in current:
        if outcome["status"] not in {"accepted", "needs_review", "needs_revision"}:
            continue
        for claim in json.loads(outcome["claims"]):
            identity = claim["source_id"]
            if identity not in sources:
                try:
                    src = context.source(req["project_id"], identity)
                    sources[identity] = {k: v for k, v in src.items() if k != "text"}
                    sources[identity]["validation"] = "Archive and extracted text integrity checked at export"
                except (WorkbenchError, OSError) as exc:
                    # Do not include an out-of-scope locator, text or metadata.
                    sources[identity] = {"id": identity, "validation": "unavailable", "reason": str(exc)}
            if sources[identity]["validation"] == "unavailable":
                invalid[outcome["id"]] = "Cited evidence is unavailable; requires review"

    accepted = [o for o in reversed(current) if o["status"] == "accepted" and o["id"] not in invalid]
    displayed = accepted[:]
    if not displayed and req["status"] not in {"cancelled", "grouped"}:
        # Preserve useful draft-only exports without promoting a draft to acceptance.
        displayed = next(([o] for o in current if o["status"] in {"needs_review", "needs_revision"} and o["id"] not in invalid), [])
    accepted_ids = {o["id"] for o in accepted}
    displayed_ids = {o["id"] for o in displayed}
    completed = sum(item["status"] == "accepted" and item["outcome_id"] in accepted_ids for item in obligations)
    status = req["status"]
    if status == "accepted" and (not accepted or completed < len(obligations)):
        status = "needs_review"

    title = safe_text(req["title"])
    project_label = project["name"] + " (" + project["id"] + ")" if project else "Unassigned"
    summary = f"Status: {status}\nRequirement revision: {req['revision']}\nRequest: {req['id']}\nProject: {project_label}"
    markdown = ["# " + title, summary]
    sections = ["<h1>" + _escaped(title) + "</h1><pre>" + _escaped(summary) + "</pre>"]
    if obligations:
        progress = f"Accepted deliverables: {completed}/{len(obligations)}"
        markdown += [progress, "| Deliverable | State | Acceptance criterion |\n|---|---|---|"]
        table = ["<h2>Deliverables</h2><p>" + _escaped(progress) + "</p><table><thead><tr><th>Deliverable</th><th>State</th><th>Acceptance criterion</th></tr></thead><tbody>"]
        for item in obligations:
            state = item["status"]
            if state == "accepted" and item["outcome_id"] not in accepted_ids:
                state = "requires review; accepted evidence unavailable"
            values = [item["description"], state, item["acceptance"]]
            markdown[-1] += "\n| " + " | ".join(_markdown_cell(v) for v in values) + " |"
            table.append("<tr>" + "".join("<td>" + _escaped(v) + "</td>" for v in values) + "</tr>")
        sections.append("".join(table) + "</tbody></table>")

    for index, outcome in enumerate(displayed, 1):
        heading = f"Accepted outcome {index}" if outcome["id"] in accepted_ids else "Latest proposed outcome — " + outcome["status"]
        linked = [o for o in obligations if o["outcome_id"] == outcome["id"]]
        provenance = f"Outcome: {outcome['id']}\nAuthor: {outcome['author']}\nStatus: {outcome['status']}\nRevision: {outcome['revision']}"
        if linked:
            provenance += "\nDeliverables: " + "; ".join(o["description"] for o in linked)
        body = safe_text(outcome["body"])
        markdown += ["## " + heading, body, provenance]
        section = ["<section><h2>" + _escaped(heading) + "</h2><pre>" + _escaped(body) + "</pre><small><pre>" + _escaped(provenance) + "</pre></small>"]
        claims = json.loads(outcome["claims"])
        evidence = []
        for claim in claims:
            src = sources[claim["source_id"]]
            evidence.append(
                f"{claim['kind']}: {claim['statement']}\nQuoted passage: {claim['quote']}\n"
                f"Source: {src['id']}\nLocator: {src['locator']}\nSHA-256: {src['hash']}\n"
                f"Authority: {src['authority']}\nSource project: {src['project_id']}\n{src['validation']}"
            )
        evidence_text = "\n\n".join(evidence) or "No source-backed claims were supplied."
        checks = json.dumps(json.loads(outcome["checks"]), indent=2, ensure_ascii=False)
        notes = "\n\n".join(f"{r['reviewer']}: {r['verdict']}\n{r['notes']}" for r in reviews if r["outcome_id"] == outcome["id"]) or "No independent review recorded."
        markdown += ["### Claims and sources", evidence_text, "### Review notes", notes, "### Evidence and checks\n\n```json\n" + safe_text(checks) + "\n```"]
        for label, text in [("Claims and sources", evidence_text), ("Review notes", notes), ("Evidence and checks", checks)]:
            section.append("<details><summary>" + label + "</summary><pre>" + _escaped(text) + "</pre></details>")
        sections.append("".join(section) + "</section>")

    if not displayed:
        empty = "No outcome has been submitted." if not outcomes else "No current outcome is available for presentation. Historical or invalidated results remain in the JSON audit record."
        markdown.append(empty)
        sections.append("<p>" + _escaped(empty) + "</p>")
    excluded = [{"id": o["id"], "revision": o["revision"], "status": o["status"], "reason": invalid.get(o["id"], "Not a current accepted deliverable")} for o in current if o["id"] not in displayed_ids]
    if excluded:
        note = "\n".join(f"{o['id']}: {o['status']} — {o['reason']}" for o in excluded)
        markdown += ["## Other current outcomes", note]
        sections.append("<details><summary>Other current outcomes</summary><pre>" + _escaped(note) + "</pre></details>")
    historical = len(outcomes) - len(current)
    if historical:
        note = f"{historical} outcome(s) from earlier revisions are retained only in the JSON audit record."
        markdown.append(note)
        sections.append("<p>" + _escaped(note) + "</p>")
    if plan:
        sections.append("<h2>Task dependencies</h2>" + plan_svg(plan))
    sections.append("<details><summary>Original prompts</summary><pre>" + _escaped("\n\n".join(p["body"] for p in prompts)) + "</pre></details>")
    notice = "Rendering is separate from factual review. Exports are snapshots; source revocations require re-export."
    markdown.append(notice)
    sections.append("<p><small>" + notice + "</small></p>")
    page = (
        """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'">
<title>Assistant Workbench outcome</title><style>body{max-width:960px;margin:3rem auto;padding:0 1rem;font:17px/1.6 system-ui;color:#213440;background:#fbfcfd}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}small{color:#4b6270}details{border-top:1px solid #b6c7ce;padding:1rem 0}svg{max-height:480px;width:100%}h1{line-height:1.2}table{border-collapse:collapse;width:100%}td,th{border:1px solid #b6c7ce;padding:.5rem;text-align:left;vertical-align:top}</style><body>"""
        + "".join(sections) + "</body></html>"
    )
    record = {
        "schema": 1,
        "request": req,
        "outcomes": outcomes,
        "prompts": prompts,
        "project": project,
        "obligations": obligations,
        "reviews": reviews,
        "sources": list(sources.values()),
        "presentation": {
            "revision": req["revision"], "status": status,
            "accepted_outcome_ids": [o["id"] for o in accepted],
            "displayed_outcome_ids": [o["id"] for o in displayed],
            "accepted_deliverables": completed, "total_deliverables": len(obligations),
            "excluded_current_outcomes": excluded,
            "notice": "Historical outcomes remain in this audit record; they are not current accepted deliverables.",
        },
        "created": now(),
    }
    atomic(directory / "outcome.html", page)
    atomic(directory / "outcome.md", "\n\n".join(markdown) + "\n")
    atomic(directory / "outcome.json", canonical(record) + "\n")
    return {
        "html": str(directory / "outcome.html"),
        "markdown": str(directory / "outcome.md"),
        "json": str(directory / "outcome.json"),
        "status": status,
        "notice": notice,
    }

"""A terminal view over durable records, independent of worker scrollback."""

from __future__ import annotations

import json
from pathlib import Path
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import (
    Header,
    Footer,
    Static,
    Input,
    Select,
    TabbedContent,
    TabPane,
    DataTable,
)
from .app import Application
from .util import WorkbenchError, canonical, safe_text, uid, read_json


class WorkbenchApp(App):
    TITLE = "Assistant Workbench"
    SUB_TITLE = "Requests • context • outcomes"
    CSS = """
    Screen { background: $surface; }
    #compose { height: 3; }
    #project { width: 24; }
    #prompt { width: 1fr; }
    #notice { height: 2; padding: 0 1; color: $text-muted; }
    TabbedContent { height: 1fr; min-height: 8; }
    DataTable { height: 1fr; }
    #details-scroll { height: 9; border-top: solid $primary; padding: 0 1; }
    #details { height: auto; }
    #search { height: 3; }
    """
    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+n", "compose", "New prompt"),
        ("ctrl+r", "refresh", "Refresh"),
        ("ctrl+b", "pin", "Pin outcome"),
        ("ctrl+d", "read", "Mark read"),
        ("ctrl+o", "reopen", "Reopen request"),
    ]

    def __init__(self, home):
        super().__init__()
        self.work = Application(Path(home))
        self.selected = None
        self.records = {}
        self.previous = {}

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="compose"):
            yield Select(
                [
                    (p["name"], p["id"])
                    for p in self.work.store.rows(
                        "SELECT * FROM projects ORDER BY name"
                    )
                ],
                prompt="Choose project",
                id="project",
            )
            yield Input(
                placeholder="Send a prompt; independent requests can run in parallel",
                id="prompt",
            )
        yield Static(
            "Saved outcomes stay here when logs move on. Ctrl+N to write.",
            id="notice",
            markup=False,
        )
        with TabbedContent(initial="inbox"):
            with TabPane("Inbox", id="inbox"):
                yield DataTable(id="inbox-table", cursor_type="row")
            with TabPane("Requests", id="requests"):
                yield DataTable(id="requests-table", cursor_type="row")
            with TabPane("Outcomes", id="outcomes"):
                yield Input(placeholder="Search saved outcomes", id="search")
                yield DataTable(id="outcomes-table", cursor_type="row")
            with TabPane("Teams", id="teams"):
                yield DataTable(id="teams-table", cursor_type="row")
            with TabPane("Context", id="context"):
                yield DataTable(id="context-table", cursor_type="row")
            with TabPane("Health", id="health"):
                yield DataTable(id="health-table", cursor_type="row")
            with TabPane("Logs", id="logs"):
                yield DataTable(id="logs-table", cursor_type="row")
        with VerticalScroll(id="details-scroll"):
            yield Static(
                "Select a row for the answer, checks, dependencies or blocker.",
                id="details",
                markup=False,
            )
        yield Footer()

    def on_mount(self):
        columns = {
            "inbox": ["Request", "Needs attention", "Detail"],
            "requests": ["Request", "Project", "State", "Rev"],
            "outcomes": ["Answer", "State", "New", "Pin"],
            "teams": ["Request / role", "Model", "Depends on", "State"],
            "context": ["Project", "Authority", "State", "Source"],
            "health": ["Component", "State", "Detail"],
            "logs": ["Sequence", "Event", "Entity"],
        }
        widths = {
            "inbox": [20, 16, 34],
            "requests": [30, 12, 16, 4],
            "outcomes": [42, 16, 3, 3],
            "teams": [30, 15, 18, 15],
            "context": [12, 10, 10, 38],
            "health": [14, 14, 42],
            "logs": [10, 30, 28],
        }
        for name, labels in columns.items():
            for label, width in zip(labels, widths[name]):
                self.query_one("#" + name + "-table", DataTable).add_column(
                    label, width=width
                )
        self.action_refresh()
        self.set_interval(2, self.action_refresh)

    def table(self, name, rows):
        table = self.query_one("#" + name + "-table", DataTable)
        current = {str(key): cells for key, cells, detail in rows}
        old = self.previous.get(name, {})
        for key in old.keys() - current.keys():
            table.remove_row(key)
        for key, cells, detail in rows:
            key = str(key)
            self.records[(name, key)] = detail
            values = [
                Text(
                    safe_text(str(c))
                    .replace("needs_review", "Needs review")
                    .replace("needs_revision", "Needs revision")
                    .replace("needs_project", "Choose project")
                    .replace("source_revoked", "Source revoked")
                )
                for c in cells
            ]
            if key not in old:
                table.add_row(*values, key=key)
            elif old[key] != cells:
                for column, value in zip(table.columns, values):
                    table.update_cell(key, column, value)
        self.previous[name] = current

    def action_refresh(self):
        snapshot = self.work.store.snapshot()
        projects = {p["id"]: p["name"] for p in snapshot["projects"]}
        requests = snapshot["requests"]
        self.table(
            "requests",
            [
                (
                    r["id"],
                    [
                        r["title"],
                        projects.get(r["project_id"], "Unassigned"),
                        r["status"],
                        r["revision"],
                    ],
                    r,
                )
                for r in requests
            ],
        )
        attention = [
            (r["id"], [r["title"], r["status"], r["id"]], r)
            for r in requests
            if r["status"] in {"needs_project", "needs_review", "needs_revision"}
        ]
        attention += [
            (r["id"], [r["request_id"], r["status"], r["error"] or ""], r)
            for r in snapshot["attention"]
        ]
        self.table("inbox", attention)
        query = self.query_one("#search", Input).value.lower()
        outcomes = (
            self.work.dispatch("outcome.search", {"query": query})
            if query
            else snapshot["outcomes"]
        )
        self.table(
            "outcomes",
            [
                (
                    o["id"],
                    [
                        o["body"].splitlines()[0][:120],
                        o["status"],
                        "No" if o["is_read"] else "Yes",
                        "Yes" if o["pinned"] else "No",
                    ],
                    o,
                )
                for o in outcomes
            ],
        )
        teams = []
        for r in requests:
            if not r["plan"]:
                continue
            plan = json.loads(r["plan"])
            observed = read_json(self.work.home / "observations.json", {}).get(
                r["id"], {}
            )
            for n in plan["nodes"]:
                key = r["id"] + ":" + n["id"]
                state = observed.get(n["id"], {}).get("status", "No live observation")
                detail = {
                    "request": r["id"],
                    "node": n,
                    "model": plan["model"],
                    "selection": plan["selection"],
                    "observation": observed.get(n["id"], {}),
                }
                teams.append(
                    (
                        key,
                        [
                            r["title"][:35] + " / " + n["role"],
                            n["model_profile"],
                            ", ".join(n["depends_on"]) or "Independent",
                            state,
                        ],
                        detail,
                    )
                )
        self.table("teams", teams)
        self.table(
            "context",
            [
                (
                    s["id"],
                    [
                        projects.get(s["project_id"], s["project_id"]),
                        s["authority"],
                        "Revoked" if s["revoked"] else "Archived",
                        s["locator"],
                    ],
                    s,
                )
                for s in snapshot["sources"]
            ],
        )
        health = read_json(
            self.work.home / "bridge-health.json",
            {
                "status": "not observed",
                "reason": "Start awb serve; run awb doctor for an explicit probe",
            },
        )
        self.table(
            "health",
            [
                (
                    "bridge",
                    ["Bridge", health.get("status"), health.get("reason", "")],
                    health,
                ),
                (
                    "release",
                    [
                        "Release",
                        "Experimental",
                        "Live harness and real GUI qualification required",
                    ],
                    {"notice": "Headless TUI tests do not qualify WezTerm or a model."},
                ),
            ],
        )
        self.table(
            "logs",
            [
                (e["seq"], [e["seq"], e["kind"], e["entity"]], e)
                for e in snapshot["events"]
            ],
        )
        # Refreshing data does not replace the selected detail or its scroll position.

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        name = event.data_table.id.removesuffix("-table")
        key = event.row_key.value
        self.selected = (name, key)
        record = self.records.get(self.selected, {})
        if name == "outcomes":
            text = (
                record["body"]
                + "\n\nStatus: "
                + record["status"]
                + " | Revision: "
                + str(record["revision"])
                + "\nChecks: "
                + record["checks"]
            )
        elif name == "requests":
            text = (
                record["body"]
                + "\n\nState: "
                + record["status"]
                + " | Request: "
                + record["id"]
            )
        else:
            text = json.dumps(record, indent=2, ensure_ascii=False)
        self.query_one("#details", Static).update(safe_text(text))

    def on_input_submitted(self, event: Input.Submitted):
        if event.input.id != "prompt":
            return
        try:
            selected = self.query_one("#project", Select).value
            receipt = self.work.store.submit(
                event.value,
                uid("tui_"),
                None if selected is Select.NULL else str(selected),
            )
            event.input.value = ""
            self.query_one("#notice", Static).update(
                "Saved " + receipt["request_id"] + " • " + receipt["routing"]
            )
            self.action_refresh()
        except WorkbenchError as exc:
            self.query_one("#notice", Static).update(safe_text(str(exc)))

    def on_input_changed(self, event: Input.Changed):
        if event.input.id == "search" and self.is_mounted:
            self.action_refresh()

    def action_compose(self):
        self.query_one("#prompt", Input).focus()

    def mark(self, field):
        if not self.selected or self.selected[0] != "outcomes":
            return
        record = self.records[self.selected]
        value = 1 if field == "is_read" else not record[field]
        self.work.dispatch(
            "outcome.mark", {"outcome_id": record["id"], "field": field, "value": value}
        )
        self.action_refresh()

    def action_pin(self):
        self.mark("pinned")

    def action_read(self):
        self.mark("is_read")

    def action_reopen(self):
        if not self.selected:
            return
        record = self.records[self.selected]
        ident = record.get("request_id") or (
            record.get("id") if self.selected[0] == "requests" else None
        )
        if ident:
            receipt = self.work.store.reopen(ident)
            self.query_one("#notice", Static).update(
                "Reopened as " + receipt["request_id"]
            )
            self.action_refresh()

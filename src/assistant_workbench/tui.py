"""Terminal controls over durable records; worker execution belongs to the backend."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Select,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)

from .app import Application
from .runtime_backends import Fleet
from .util import WorkbenchError, atomic, canonical, now, read_json, safe_text, uid

TABS = ("inbox", "requests", "outcomes", "teams", "context", "health", "logs")


class EntryDialog(ModalScreen):
    """A reversible operator form, with no hidden dispatch or provider call."""

    DEFAULT_CSS = """
    EntryDialog { align: center middle; background: $background 65%; }
    EntryDialog > Vertical { width: 70; max-width: 95%; height: auto; max-height: 95%;
        border: thick $primary; background: $surface; padding: 1 2; }
    EntryDialog .dialog-title { text-style: bold; height: 2; }
    EntryDialog .dialog-label { height: auto; }
    EntryDialog Input { height: 3; }
    EntryDialog TextArea { height: 7; }
    EntryDialog Horizontal { height: 3; align-horizontal: right; }
    EntryDialog Button { min-width: 12; margin-left: 1; }
    """
    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, title, fields=(), multiline=None, confirm="Save", message=""):
        super().__init__()
        self.title_text = title
        self.fields = fields
        self.multiline = multiline
        self.confirm_text = confirm
        self.message = message

    def compose(self):
        with Vertical():
            yield Static(self.title_text, classes="dialog-title", markup=False)
            if self.message:
                yield Static(self.message, classes="dialog-label", markup=False)
            for identity, label, value in self.fields:
                yield Static(label, classes="dialog-label", markup=False)
                yield Input(value=value, id=identity)
            if self.multiline:
                identity, label, value = self.multiline
                yield Static(label, classes="dialog-label", markup=False)
                yield TextArea(value, id=identity)
            with Horizontal():
                yield Button("Back", id="dialog-back")
                yield Button(self.confirm_text, id="dialog-submit", variant="primary")

    def on_mount(self):
        if self.fields:
            self.query_one("#" + self.fields[0][0], Input).focus()
        elif self.multiline:
            self.query_one("#" + self.multiline[0], TextArea).focus()
        else:
            self.query_one("#dialog-back", Button).focus()

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "dialog-back":
            self.dismiss(None)
            return
        result = {identity: self.query_one("#" + identity, Input).value for identity, _, _ in self.fields}
        if self.multiline:
            result[self.multiline[0]] = self.query_one("#" + self.multiline[0], TextArea).text
        self.dismiss(result)

    def on_input_submitted(self, event: Input.Submitted):
        # Enter in a path/name field moves to the visible submit action.
        self.query_one("#dialog-submit", Button).focus()

    def action_close(self):
        self.dismiss(None)


class WorkbenchApp(App):
    TITLE = "Assistant Workbench"
    SUB_TITLE = "Requests • context • outcomes"
    CSS = """
    Screen { background: $surface; }
    #compose { height: 3; }
    #project { width: 21; }
    #project-filter { width: 21; }
    #prompt { width: 1fr; }
    #notice { height: 2; padding: 0 1; color: $text-muted; }
    TabbedContent { height: 1fr; min-height: 5; }
    DataTable { height: 1fr; }
    #details-scroll { height: 9; border-top: solid $primary; padding: 0 1; }
    #details { height: auto; }
    #search, #request-search { height: 3; }
    """
    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+n", "compose", "New prompt"),
        ("ctrl+r", "refresh", "Refresh"),
        ("ctrl+b", "pin", "Pin outcome"),
        ("ctrl+d", "read", "Mark read"),
        ("ctrl+o", "reopen", "Reopen request"),
        Binding("f1", "help", "Actions"),
        Binding("f2", "new_project", "New project", show=False),
        Binding("f3", "amend", "Amend request", show=False),
        Binding("f4", "cancel_request", "Cancel request", show=False),
        Binding("f5", "pause_project", "Pause/resume project", show=False),
        Binding("f6", "export", "Export outcome", show=False),
    ]

    def __init__(self, home):
        super().__init__()
        self.work = Application(Path(home))
        self.selected = None
        self.records = {}
        self.previous = {}
        self._tables_ready = False
        self._project_options = None
        self._selected_detail = None
        self._diagnostic_errors = {}
        self._saved_view = None
        self._pending_view = {}
        self._notice = "F1 actions • Send chooses the prompt destination; View filters the saved work."

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="compose"):
            yield Select([], prompt="Send: auto", id="project")
            yield Select([("View: all", "*")], value="*", allow_blank=False, id="project-filter")
            yield Input(placeholder="Send a prompt", id="prompt")
        yield Static(self._notice, id="notice", markup=False)
        with TabbedContent(initial="inbox"):
            with TabPane("Inbox", id="inbox"):
                yield DataTable(id="inbox-table", cursor_type="row")
            with TabPane("Requests", id="requests"):
                yield Input(placeholder="Search request text or full ticket ID", id="request-search")
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
            yield Static("Select a row to inspect its result, requirements, checks or blocker.", id="details", markup=False)
        yield Footer()

    def on_mount(self):
        columns = {
            "inbox": [("Ticket", 12), ("Needs attention", 16), ("Detail", 40)],
            "requests": [("Ticket", 12), ("Request", 27), ("Project", 12), ("State", 16), ("Rev", 3), ("Done", 7)],
            "outcomes": [("Answer", 39), ("State", 15), ("New", 3), ("Pin", 3), ("Ticket", 12)],
            "teams": [("Ticket / role", 28), ("Model", 15), ("Depends on", 18), ("Observed state", 24)],
            "context": [("Project", 12), ("Authority", 10), ("State", 10), ("Source", 38)],
            "health": [("Component", 14), ("State", 14), ("Detail", 42)],
            "logs": [("Sequence", 10), ("Event", 30), ("Entity", 28)],
        }
        for name, items in columns.items():
            for label, width in items:
                self.query_one("#" + name + "-table", DataTable).add_column(label, width=width)
        self._tables_ready = True
        self._resize_details()
        self._restore_view()
        self.action_refresh()
        self.set_interval(2, self.action_refresh)

    def on_resize(self):
        if self._tables_ready:
            self._resize_details()

    def _resize_details(self):
        self.query_one("#details-scroll").styles.height = 6 if self.size.height <= 27 else 9

    def _restore_view(self):
        """Reopen on the tab, filters and row that were open last time."""
        try:
            saved = read_json(self.work.home / "tui-state.json", {})
            if not isinstance(saved, dict):
                raise WorkbenchError("Expected an object")
        except WorkbenchError:
            self.notice("The saved view could not be read; starting from the inbox.")
            return
        if saved.get("tab") in TABS:
            self.query_one(TabbedContent).active = saved["tab"]
        for widget in ("request_search", "search"):
            if isinstance(saved.get(widget), str):
                self.query_one("#" + widget.replace("_", "-"), Input).value = saved[widget]
        selected = saved.get("selected")
        self._pending_view = {
            "view": saved.get("view"),
            "send": saved.get("send"),
            "selected": tuple(selected) if isinstance(selected, list) and len(selected) == 2 else None,
        }

    def _save_view(self):
        send = self.query_one("#project", Select).value
        view = self.query_one("#project-filter", Select).value
        state = {
            "tab": self.query_one(TabbedContent).active,
            "send": None if send is Select.NULL else send,
            "view": None if view is Select.NULL else view,
            "selected": list(self.selected) if self.selected else None,
            "request_search": self.query_one("#request-search", Input).value,
            "search": self.query_one("#search", Input).value,
        }
        if state != self._saved_view:
            try:
                atomic(self.work.home / "tui-state.json", canonical(state))
                self._saved_view = state
            except OSError:
                pass  # Only the convenience of reopening in place is lost.

    def on_tabbed_content_tab_activated(self, event):
        if self._tables_ready:
            self._save_view()

    @staticmethod
    def ticket(identity):
        return identity[:12] if identity else "Unassigned"

    def notice(self, text):
        self._notice = safe_text(text)
        self.query_one("#notice", Static).update(self._notice)

    def table(self, name, rows):
        table = self.query_one("#" + name + "-table", DataTable)
        current = {str(key): cells for key, cells, detail in rows}
        old = self.previous.get(name, {})
        old_order = list(old)
        new_order = list(current)
        cursor_key = old_order[table.cursor_row] if table.cursor_row < len(old_order) else None
        offset = table.scroll_offset
        for key in old.keys() - current.keys():
            self.records.pop((name, key), None)
        reorder = old_order != new_order
        if reorder:
            table.clear()
        for key, cells, detail in rows:
            key = str(key)
            self.records[(name, key)] = detail
            values = [Text(safe_text(str(c)).replace("needs_review", "Needs review").replace("needs_revision", "Needs revision").replace("needs_project", "Choose project").replace("source_revoked", "Source revoked")) for c in cells]
            if reorder:
                table.add_row(*values, key=key)
            elif old[key] != cells:
                for column, value in zip(table.columns, values):
                    table.update_cell(key, column, value)
        if reorder and cursor_key in current:
            table.move_cursor(row=new_order.index(cursor_key), scroll=False)
            table.scroll_to(x=offset.x, y=offset.y, animate=False)
        self.previous[name] = current

    def _refresh_projects(self, projects):
        options = [(p["name"], p["id"]) for p in projects]
        if options == self._project_options:
            return
        self._project_options = options
        send = self.query_one("#project", Select)
        view = self.query_one("#project-filter", Select)
        send_value, view_value = send.value, view.value
        if self._pending_view:
            send_value = self._pending_view.pop("send", None) or send_value
            view_value = self._pending_view.pop("view", None) or view_value
        send.set_options([("Send: " + name, identity) for name, identity in options])
        view.set_options([("View: all", "*"), *(("View: " + name, identity) for name, identity in options)])
        identities = {identity for _, identity in options}
        send.value = send_value if send_value in identities else Select.NULL
        view.value = view_value if view_value in identities else "*"

    def _rows_for_scope(self, table, project, query=""):
        # Query before limiting so older project records can still be found.
        if table == "requests":
            sql = "SELECT * FROM requests WHERE (?='*' OR project_id=?) AND (instr(lower(body),lower(?))>0 OR instr(id,?)>0) ORDER BY updated DESC LIMIT 200"
            return self.work.store.rows(sql, (project, project, query, query))
        sql = "SELECT o.* FROM outcomes o JOIN requests r ON r.id=o.request_id WHERE (?='*' OR r.project_id=?) AND instr(lower(o.body),lower(?))>0 ORDER BY o.pinned DESC,o.created DESC LIMIT 200"
        return self.work.store.rows(sql, (project, project, query))

    def action_refresh(self):
        if not self._tables_ready:
            return
        try:
            self._refresh_records()
        except (WorkbenchError, OSError, sqlite3.Error) as exc:
            self.notice("View could not refresh: " + str(exc))

    def _diagnostic(self, filename, default=None):
        try:
            value = read_json(self.work.home / filename, default or {})
            if not isinstance(value, dict):
                raise WorkbenchError("Expected an object in " + filename)
            return value
        except WorkbenchError as exc:
            self._diagnostic_errors[filename] = str(exc)
            return default or {}

    def _refresh_records(self):
        self._diagnostic_errors = {}
        store = self.work.store
        snapshot = store.snapshot()
        self._refresh_projects(snapshot["projects"])
        projects = {p["id"]: p["name"] for p in snapshot["projects"]}
        scope = self.query_one("#project-filter", Select).value
        if scope is Select.NULL:
            scope = "*"
        requests = self._rows_for_scope("requests", scope)
        filtered_requests = self._rows_for_scope("requests", scope, self.query_one("#request-search", Input).value)
        request_map = {r["id"]: r for r in [*requests, *filtered_requests]}
        placeholders = ",".join("?" for _ in request_map)
        obligations = store.rows(
            "SELECT o.* FROM obligations o JOIN requests r ON r.id=o.request_id "
            "WHERE o.revision=r.revision AND o.request_id IN (" + placeholders + ")",
            tuple(request_map),
        ) if request_map else []
        by_request = {}
        for item in obligations:
            by_request.setdefault((item["request_id"], item["revision"]), []).append(item)
        jobs = store.rows("SELECT * FROM outbox WHERE status IN ('blocked','uncertain','retry') ORDER BY rowid")
        blocked = {j["request_id"]: j for j in store.execution_attention() + jobs if j["request_id"] in request_map and j["revision"] == request_map[j["request_id"]]["revision"]}
        paused_projects = {p["project_id"] for p in store.rows("SELECT project_id FROM project_policy WHERE paused=1")}
        request_rows = []
        for r in filtered_requests:
            items = by_request.get((r["id"], r["revision"]), [])
            done = sum(item["status"] == "accepted" for item in items)
            block = blocked.get(r["id"])
            state = block["status"] if block else r["status"]
            if state == "queued" and r["project_id"] in paused_projects:
                state = "Paused (queued)"
            detail = {**r, "obligations": items, "delivery": block, "display_state": state}
            request_rows.append((r["id"], [self.ticket(r["id"]), r["title"], projects.get(r["project_id"], "Unassigned"), state, r["revision"], f"{done}/{len(items)}" if items else "—"], detail))
        self.table("requests", request_rows)
        attention = [(r["id"], [self.ticket(r["id"]), r["status"], r["title"]], r) for r in requests if r["status"] in {"needs_project", "needs_review", "needs_revision"}]
        attention += [(j["id"], [self.ticket(j["request_id"]), j["status"], j["error"] or ""], j) for j in blocked.values()]
        self.table("inbox", attention)
        outcomes = self._rows_for_scope("outcomes", scope, self.query_one("#search", Input).value)
        self.table("outcomes", [(o["id"], [o["body"].splitlines()[0][:120], o["status"], "No" if o["is_read"] else "Yes", "Yes" if o["pinned"] else "No", self.ticket(o["request_id"])], o) for o in outcomes])
        observed_all = self._diagnostic("observations.json")
        observed_health = self._diagnostic("observation-health.json")
        observed_at = observed_health.get("at")
        stale = (
            observed_health.get("status") == "unavailable"
            or bool(self._diagnostic_errors)
            or (bool(observed_all) and (
                not isinstance(observed_at, (int, float))
                or not 0 <= now() - observed_at < 30
            ))
        )
        teams = []
        for r in requests:
            if not r["plan"]:
                continue
            try:
                plan = json.loads(r["plan"])
                nodes = plan["nodes"]
            except (ValueError, KeyError, TypeError):
                teams.append((r["id"] + ":invalid-plan", [self.ticket(r["id"]), "Unknown", "Unknown", "Invalid saved plan"], {"request": r["id"], "error": "Saved plan cannot be displayed; inspect the plan before dispatch."}))
                continue
            observed = observed_all.get(r["id"], {})
            if not isinstance(observed, dict):
                observed = {}
            for node in nodes:
                obs = observed.get(node["id"], {})
                if not isinstance(obs, dict):
                    obs = {}
                state = obs.get("status", "Planned; not observed")
                if obs and stale:
                    state += " (stale)"
                fixture = plan.get("model", {}).get("fixture_only", False)
                if fixture:
                    state = "Fixture / " + state
                detail = {"request": r["id"], "node": node, "model": plan.get("model", {}), "selection": plan.get("selection", {}), "observation": obs, "fixture_only": fixture, "observation_stale": stale}
                teams.append((r["id"] + ":" + node["id"], [self.ticket(r["id"]) + " / " + node["role"], node["model_profile"], ", ".join(node["depends_on"]) or "Independent", state], detail))
        self.table("teams", teams)
        sources = store.rows(
            "SELECT s.*,c.state AS coverage_state FROM sources s LEFT JOIN coverage c "
            "ON c.source_id=s.id WHERE (?='*' OR s.project_id=?) ORDER BY s.created DESC LIMIT 200",
            (scope, scope),
        )
        self.table("context", [(s["id"], [projects.get(s["project_id"], s["project_id"]), s["authority"], "Revoked" if s["revoked"] else s["coverage_state"] or "Archived", s["locator"]], s) for s in sources])
        health = self._diagnostic("bridge-health.json", {"status": "not observed", "reason": "Start awb serve; run awb doctor for an explicit probe"})
        if isinstance(health.get("at"), (float, int)) and now() - health["at"] > 30:
            health = {**health, "status": "stale", "reason": "Last bridge heartbeat is older than 30 seconds"}
        fleet = Fleet(store).status()
        fleet_rows = []
        for host in fleet["hosts"]:
            state = "Disabled" if not host["enabled"] else "Heartbeat current" if host["alive"] else "Not observed"
            detail = {**host, "notice": "Registration and host heartbeat do not establish model authentication or a running agent."}
            fleet_rows.append(("host:" + host["id"], [host["name"], state, f"{host['occupied']}/{host['capacity']} occupied; {host['interactive_reserve']} reserved for interactive work"], detail))
        for lease in fleet["leases"]:
            if lease["state"] in {"leased", "running", "cancel_requested", "uncertain"}:
                detail = {**lease, "notice": "Uncertain leases continue to occupy capacity until a worker stop receipt reconciles them."}
                fleet_rows.append(("lease:" + lease["id"], ["Lease " + self.ticket(lease["request_id"]), lease["state"], lease["host_id"] + " / " + lease["task_key"]], detail))
        self.table("health", [
            ("bridge", ["Bridge", health.get("status"), health.get("reason", "")], health),
            ("observations", ["Agent state", "unavailable" if stale else "See Teams", observed_health.get("reason", "Only coordinator observations count as live state")], observed_health),
            ("release", ["Release", "Experimental", "Live harness and real GUI qualification required"], {"notice": "Headless TUI tests do not qualify WezTerm or a model."}),
        ] + fleet_rows + [(name, [name, "Unreadable", error], {"error": error}) for name, error in self._diagnostic_errors.items()])
        self.table("logs", [(e["seq"], [e["seq"], e["kind"], e["entity"]], e) for e in snapshot["events"]])
        restored = self._pending_view.pop("selected", None)
        if restored in self.records:
            self.selected = restored
            self.query_one("#" + restored[0] + "-table", DataTable).move_cursor(
                row=list(self.previous[restored[0]]).index(restored[1]))
            self._show_selected(reset_scroll=True)
        if self.selected and self.selected not in self.records:
            self.selected = None
            self._selected_detail = None
            self.query_one("#details", Static).update("Selection is no longer in this view. Select another row.")
        elif self.selected:
            self._show_selected(reset_scroll=False)
        total = store.one("SELECT COUNT(*) AS n FROM requests WHERE (?='*' OR project_id=?)", (scope, scope))["n"]
        self.sub_title = f"{len(snapshot['projects'])} projects • {len(filtered_requests)}/{total} requests shown • {len(blocked)} blocked/retrying"
        self._save_view()

    def _show_selected(self, reset_scroll=False):
        record = self.records.get(self.selected)
        if not record:
            return
        name, _ = self.selected
        text = ""
        if name == "outcomes":
            text = record["body"] + f"\n\nStatus: {record['status']} | Revision: {record['revision']}\nTicket: {record['request_id']}\nChecks: {record['checks']}"
        elif name == "requests":
            text = record["body"] + f"\n\nState: {record.get('display_state', record['status'])} | Revision: {record['revision']} | Ticket: {record['id']}"
            if record.get("delivery"):
                text += "\nBlocked: " + (record["delivery"].get("error") or "No detail")
            items = record.get("obligations", [])
            if items:
                text += "\n\nDeliverables:\n" + "\n".join(f"• {item['status']}: {item['description']} — {item['acceptance']}" for item in items)
        elif name == "teams" and record.get("node"):
            node = record["node"]
            obs = record.get("observation", {})
            text = f"Ticket: {record['request']}\nRole: {node['role']} | Node: {node['id']}\nOwnership: {'child of ' + node['parent'] if node.get('parent') else 'peer'}\nDepends on: {', '.join(node['depends_on']) or 'independent'}\nModel: {record['model'].get('model', 'unspecified')} | Effort: {record['model'].get('effort', 'unspecified')}\nState: {obs.get('status', 'planned; no live observation')}"
            if record.get("fixture_only"):
                text += "\nDeterministic fixture only; this is not a live model agent."
            if record.get("observation_stale"):
                text += "\nLast observation is stale: the coordinator cannot currently be reached."
            if obs.get("executionRunId"):
                text += "\nObserved run: " + str(obs["executionRunId"])
            text += "\nSelection: " + record.get("selection", {}).get("reason", "No recorded reason")
        else:
            text = json.dumps(record, indent=2, ensure_ascii=False)
        text = safe_text(text)
        if text != self._selected_detail:
            scroll = self.query_one("#details-scroll", VerticalScroll)
            offset = scroll.scroll_offset
            self.query_one("#details", Static).update(text)
            self._selected_detail = text
            if not reset_scroll:
                scroll.scroll_to(x=offset.x, y=offset.y, animate=False)
        if reset_scroll:
            self.query_one("#details-scroll", VerticalScroll).scroll_home(animate=False)

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        name = event.data_table.id.removesuffix("-table")
        selected = (name, str(event.row_key.value))
        if selected not in self.records:
            return
        self.selected = selected
        self._show_selected(reset_scroll=True)
        self._save_view()

    def on_select_changed(self, event: Select.Changed):
        if event.select.id == "project-filter" and self._tables_ready:
            self.action_refresh()

    def on_input_submitted(self, event: Input.Submitted):
        if event.input.id != "prompt":
            return
        try:
            selected = self.query_one("#project", Select).value
            receipt = self.work.store.submit(event.value, uid("tui_"), None if selected is Select.NULL else str(selected))
            event.input.value = ""
            self.notice("Saved " + receipt["request_id"] + " • " + receipt.get("routing", "saved"))
            self.action_refresh()
        except (WorkbenchError, OSError, sqlite3.Error) as exc:
            self.notice(str(exc))

    def on_input_changed(self, event: Input.Changed):
        if event.input.id in {"search", "request-search"} and self._tables_ready:
            self.action_refresh()

    def action_compose(self):
        self.query_one("#prompt", Input).focus()

    def _selected_request(self):
        record = self.records.get(self.selected, {})
        ident = record.get("request_id") or record.get("request")
        if self.selected and self.selected[0] == "requests":
            ident = record.get("id")
        if self.selected and self.selected[0] == "inbox" and record.get("body"):
            ident = record.get("id")
        if not ident:
            raise WorkbenchError("Select a request, outcome or team row first")
        return self.work.dispatch("request.get", {"request_id": ident})

    def mark(self, field):
        if not self.selected or self.selected[0] != "outcomes":
            self.notice("Select an outcome first")
            return
        record = self.records.get(self.selected)
        if not record:
            self.notice("That outcome is no longer in this view")
            return
        value = 1 if field == "is_read" else not record[field]
        try:
            self.work.dispatch("outcome.mark", {"outcome_id": record["id"], "field": field, "value": value})
            self.action_refresh()
        except (WorkbenchError, OSError, sqlite3.Error) as exc:
            self.notice(str(exc))

    def action_pin(self):
        self.mark("pinned")

    def action_read(self):
        self.mark("is_read")

    def action_reopen(self):
        try:
            req = self._selected_request()
            receipt = self.work.store.reopen(req["id"])
            self.notice("Reopened as " + receipt["request_id"])
            self.action_refresh()
        except WorkbenchError as exc:
            self.notice(str(exc))

    def action_new_project(self):
        def create(values):
            if values is None:
                return
            try:
                project = self.work.dispatch("project.add", {"name": values["project-name"], "repository": values["project-repository"].strip() or None})
                self.action_refresh()
                self.query_one("#project", Select).value = project["id"]
                self.notice("Created " + project["name"] + ". New prompts will use this project.")
            except (WorkbenchError, OSError, sqlite3.Error) as exc:
                self.notice(str(exc))
        self.push_screen(EntryDialog("Create project", [("project-name", "Project name", ""), ("project-repository", "Existing repository directory (optional)", "")], confirm="Create"), create)

    def action_amend(self):
        try:
            req = self._selected_request()
        except WorkbenchError as exc:
            self.notice(str(exc))
            return
        def amend(values):
            if values is None:
                return
            try:
                receipt = self.work.store.submit(values["amendment"], uid("tui-amend_"), reply_to=req["id"], expected_revision=req["revision"])
                self.notice(f"Saved amendment to {req['id']}; revision {receipt['revision']}")
                self.action_refresh()
            except WorkbenchError as exc:
                self.notice(str(exc))
        self.push_screen(EntryDialog("Amend " + self.ticket(req["id"]), multiline=("amendment", "Add or correct requirements; original wording remains saved", ""), confirm="Amend", message=f"Current revision {req['revision']}. A concurrent change will require rereading."), amend)

    def action_cancel_request(self):
        try:
            req = self._selected_request()
        except WorkbenchError as exc:
            self.notice(str(exc))
            return
        def cancel(values):
            if values is None:
                return
            try:
                self.work.store.cancel(req["id"])
                self.notice("Cancellation saved for " + req["id"] + ". Running workers still require a coordinator stop acknowledgement.")
                self.action_refresh()
            except WorkbenchError as exc:
                self.notice(str(exc))
        self.push_screen(EntryDialog("Cancel " + self.ticket(req["id"]), confirm="Cancel task", message="Cancel this request? Other projects continue. History and saved outcomes remain available."), cancel)

    def action_pause_project(self):
        try:
            project = self.query_one("#project-filter", Select).value
            if project in {"*", Select.NULL}:
                project = self._selected_request()["project_id"]
            if not project:
                raise WorkbenchError("Choose a project view or a request assigned to a project")
            rows = self.work.store.rows("SELECT * FROM project_policy WHERE project_id=?", (project,))
            policy = rows[0] if rows else {"paused": False, "priority": 50}
            paused = not bool(policy["paused"])
            self.work.dispatch("workflow.project_policy", {"project": project, "priority": policy["priority"], "paused": paused})
            self.notice(("Paused" if paused else "Resumed") + " new dispatch for this project. Already-running workers remain under coordinator control.")
            self.action_refresh()
        except WorkbenchError as exc:
            self.notice(str(exc))

    def action_export(self):
        try:
            req = self._selected_request()
        except WorkbenchError as exc:
            self.notice(str(exc))
            return
        destination = self.work.home / "exports" / (self.ticket(req["id"]) + "-" + uid(""))
        def export(values):
            if values is None:
                return
            try:
                from .reports import export_report
                path = values["export-directory"].strip()
                if not path:
                    raise WorkbenchError("Choose an export directory")
                result = export_report(self.work.store, req["id"], Path(path).expanduser())
                self.notice("Exported HTML, Markdown and JSON: " + str(Path(result["html"]).parent))
            except (WorkbenchError, OSError) as exc:
                self.notice(str(exc))
        self.push_screen(EntryDialog("Export " + self.ticket(req["id"]), [("export-directory", "New export directory; existing exports will not be overwritten", str(destination))], confirm="Export"), export)

    def action_help(self):
        text = "Send selects the destination of NEW prompts. View filters project work; Health and Logs remain global diagnostics.\n\nF2 Create project • F3 Amend selected request • F4 Cancel selected request\nF5 Pause/resume new dispatch for viewed project • F6 Export selected result\nCtrl+N Compose • Ctrl+B Pin outcome • Ctrl+D Mark outcome read\nCtrl+O Reopen selected request • Ctrl+R Refresh • Ctrl+Q Quit\n\nSelect a table row with Enter to inspect it. Tables scroll horizontally on narrow terminals.\nViews show up to 200 rows; search and project filters are applied before the limit.\nA planned team row is not evidence that an agent ran. Fixture rows and stale observations are labelled."
        self.push_screen(EntryDialog("Workbench actions", message=text, confirm="Close"))

"""Headless operator journeys. These do not qualify real WezTerm or an LLM."""

import json

import pytest
from textual.widgets import DataTable, Input, Select, Static, TabbedContent, TextArea

from assistant_workbench.quality import Outcomes
from assistant_workbench.runtime_backends import Fleet
from assistant_workbench.tui import WorkbenchApp


async def choose_row(ui, pilot, tab, identity=None):
    ui.query_one(TabbedContent).active = tab
    await pilot.pause()
    table = ui.query_one(f"#{tab}-table", DataTable)
    if identity is not None:
        keys = list(ui.previous[tab])
        table.move_cursor(row=keys.index(identity))
    table.focus()
    await pilot.press("enter")
    await pilot.pause()


@pytest.mark.parametrize("size", [(80, 24), (120, 36), (160, 48)])
async def test_tui_project_amend_cancel_and_export_journey(app, tmp_path, size):
    ui = WorkbenchApp(app.home)
    async with ui.run_test(size=size) as pilot:
        await pilot.pause()
        await pilot.press("f2")
        await pilot.pause()
        ui.screen.query_one("#project-name", Input).value = "Website"
        await pilot.click("#dialog-submit")
        await pilot.pause()
        project = app.store.project("Website")
        assert ui.query_one("#project", Select).value == project["id"]
        ui.action_compose()
        ui.query_one("#prompt", Input).value = "Build a website with an accessible contact form"
        await pilot.press("enter")
        await pilot.pause()
        request = app.store.one("SELECT * FROM requests")
        assert request["project_id"] == project["id"]
        await choose_row(ui, pilot, "requests", request["id"])
        await pilot.press("f3")
        await pilot.pause()
        ui.screen.query_one("#amendment", TextArea).text = "Add offline form drafts; do not add analytics."
        await pilot.click("#dialog-submit")
        await pilot.pause()
        updated = app.store.one("SELECT * FROM requests WHERE id=?", (request["id"],))
        assert updated["revision"] == 2 and "do not add analytics" in updated["body"]
        assert len(app.store.rows("SELECT * FROM prompts")) == 2
        outcome = Outcomes(app.store).propose(request["id"], "Operator test outcome. Website generation was not run.", [], "fixture-author", 2)
        ui.action_refresh()
        await choose_row(ui, pilot, "outcomes", outcome["id"])
        await pilot.press("f6")
        await pilot.pause()
        destination = tmp_path / "export with spaces"
        ui.screen.query_one("#export-directory", Input).value = str(destination)
        await pilot.click("#dialog-submit")
        await pilot.pause()
        assert (destination / "outcome.html").is_file()
        assert "Website generation was not run" in (destination / "outcome.md").read_text()
        await pilot.press("f4")
        await pilot.pause()
        # Merely opening the dialog must not cancel anything.
        assert app.store.one("SELECT * FROM requests WHERE id=?", (request["id"],))["status"] == "needs_review"
        await pilot.click("#dialog-submit")
        await pilot.pause()
        assert app.store.one("SELECT * FROM requests WHERE id=?", (request["id"],))["status"] == "cancelled"
        assert "stop acknowledgement" in str(ui.query_one("#notice", Static).content)


async def test_view_filter_does_not_change_prompt_destination(app):
    alpha = app.store.add_project("Alpha")
    beta = app.store.add_project("Beta")
    a = app.store.submit("Alpha task", "a", alpha["id"])["request_id"]
    b = app.store.submit("Beta task", "b", beta["id"])["request_id"]
    oa = Outcomes(app.store).propose(a, "Alpha saved outcome", [], "fixture-a", 1)
    Outcomes(app.store).propose(b, "Beta saved outcome", [], "fixture-b", 1)
    ui = WorkbenchApp(app.home)
    async with ui.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        ui.query_one("#project-filter", Select).value = alpha["id"]
        ui.query_one("#project", Select).value = beta["id"]
        await pilot.pause()
        assert list(ui.previous["requests"]) == [a]
        assert list(ui.previous["outcomes"]) == [oa["id"]]
        await choose_row(ui, pilot, "outcomes", oa["id"])
        ui.action_compose()
        ui.query_one("#prompt", Input).value = "A new task for the selected Send destination"
        await pilot.press("enter")
        await pilot.pause()
        latest = app.store.one("SELECT * FROM requests ORDER BY created DESC")
        assert latest["project_id"] == beta["id"]
        assert list(ui.previous["requests"]) == [a]
        ui.query_one("#project-filter", Select).value = beta["id"]
        await pilot.pause()
        assert ui.selected is None
        ui.action_pin()
        assert "Select an outcome" in str(ui.query_one("#notice", Static).content)


async def test_reopening_the_ui_returns_to_the_same_place(app):
    alpha = app.store.add_project("Alpha")
    beta = app.store.add_project("Beta")
    first = app.store.submit("Alpha first task", "a1", alpha["id"])["request_id"]
    second = app.store.submit("Alpha second task", "a2", alpha["id"])["request_id"]
    app.store.submit("Beta task", "b", beta["id"])
    ui = WorkbenchApp(app.home)
    async with ui.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        ui.query_one("#project-filter", Select).value = alpha["id"]
        ui.query_one("#project", Select).value = beta["id"]
        ui.query_one("#request-search", Input).value = "task"
        await pilot.pause()
        await choose_row(ui, pilot, "requests", first)
        assert ui.selected == ("requests", first)
    # The window closed. Work done meanwhile is shown, and the view is where it was left.
    app.store.submit("Alpha third task", "a3", alpha["id"])
    reopened = WorkbenchApp(app.home)
    async with reopened.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        assert reopened.query_one(TabbedContent).active == "requests"
        assert reopened.query_one("#project-filter", Select).value == alpha["id"]
        assert reopened.query_one("#project", Select).value == beta["id"]
        assert reopened.query_one("#request-search", Input).value == "task"
        assert reopened.selected == ("requests", first)
        table = reopened.query_one("#requests-table", DataTable)
        assert list(reopened.previous["requests"])[table.cursor_row] == first
        assert len(reopened.previous["requests"]) == 3 and second in reopened.previous["requests"]
        assert "Alpha first task" in str(reopened.query_one("#details", Static).content)


async def test_an_unreadable_or_outdated_saved_view_starts_from_the_inbox(app):
    (app.home / "tui-state.json").write_text("{broken")
    ui = WorkbenchApp(app.home)
    async with ui.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        assert ui.query_one(TabbedContent).active == "inbox"
        assert "saved view could not be read" in str(ui.query_one("#notice", Static).content)
    # A project or request that no longer exists is not restored.
    (app.home / "tui-state.json").write_text(json.dumps({
        "tab": "secret-tab", "view": "p_gone", "send": "p_gone", "selected": ["requests", "r_gone"]}))
    ui = WorkbenchApp(app.home)
    async with ui.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        assert ui.query_one(TabbedContent).active == "inbox"
        assert ui.query_one("#project-filter", Select).value == "*"
        assert ui.query_one("#project", Select).value is Select.NULL
        assert ui.selected is None


async def test_tui_pin_reorders_preserves_selection_and_log_flood(app, project):
    requests = [app.store.submit(f"Report {n}", str(n), project["id"])["request_id"] for n in range(2)]
    outcomes = [Outcomes(app.store).propose(r, f"Saved result {n}", [], f"fixture-{n}", 1) for n, r in enumerate(requests)]
    ui = WorkbenchApp(app.home)
    async with ui.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await choose_row(ui, pilot, "outcomes", outcomes[0]["id"])
        await pilot.press("ctrl+b")
        await pilot.pause()
        assert list(ui.previous["outcomes"])[0] == outcomes[0]["id"]
        assert ui.selected == ("outcomes", outcomes[0]["id"])
        before = str(ui.query_one("#details", Static).content)
        with app.store.transaction() as db:
            for number in range(300):
                app.store.event(db, "test.log", str(number), {})
        ui.action_refresh()
        await pilot.pause()
        assert str(ui.query_one("#details", Static).content) == before
        ui.query_one("#search", Input).value = "no matching outcome"
        await pilot.pause()
        assert ui.selected is None and not ui.previous["outcomes"]
        await pilot.press("ctrl+d")
        assert "Select an outcome" in str(ui.query_one("#notice", Static).content)


async def test_tui_stale_amendment_is_rejected_without_clobbering(app, project):
    request = app.store.submit("Initial plan", "initial", project["id"])["request_id"]
    ui = WorkbenchApp(app.home)
    async with ui.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        await choose_row(ui, pilot, "requests", request)
        await pilot.press("f3")
        await pilot.pause()
        app.store.submit("Concurrent authoritative change", "concurrent", reply_to=request, expected_revision=1)
        ui.screen.query_one("#amendment", TextArea).text = "Stale change"
        await pilot.click("#dialog-submit")
        await pilot.pause()
        saved = app.store.one("SELECT * FROM requests WHERE id=?", (request,))
        assert saved["revision"] == 2 and "Stale change" not in saved["body"]
        assert "Revision changed" in str(ui.query_one("#notice", Static).content)


async def test_tui_planned_and_fixture_agents_are_not_claimed_live(app, project, configured):
    request = app.store.submit("Build a small app", "plan", project["id"])["request_id"]
    plan = app.dispatch("request.plan", {"request_id": request})
    app.dispatch("workflow.save_plan", {"request_id": request, "revision": 1, "plan": plan})
    ui = WorkbenchApp(app.home)
    async with ui.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        await choose_row(ui, pilot, "teams", request + ":implement")
        details = str(ui.query_one("#details", Static).content)
        assert "no live observation" in details
        assert "not a live model agent" in details
        (app.home / "observations.json").write_text(json.dumps({request: {"implement": {"status": "running", "executionRunId": "fixture-run"}}}))
        (app.home / "observation-health.json").write_text(json.dumps({"status": "unavailable", "reason": "test disconnect"}))
        ui.action_refresh()
        await pilot.pause()
        details = str(ui.query_one("#details", Static).content)
        assert "stale" in details and "fixture-run" in details
        assert "Fixture / running (stale)" in ui.previous["teams"][request + ":implement"]


async def test_tui_deliverables_blockers_and_scoped_pause(app, project):
    request = app.store.submit("Produce report and chart", "multi", project["id"])["request_id"]
    app.dispatch("workflow.obligations", {"request_id": request, "revision": 1, "items": [{"description": "Report", "acceptance": "Cite original sources"}, {"description": "Chart", "acceptance": "Preserve original units"}]})
    with app.store.transaction() as db:
        db.execute("UPDATE outbox SET status='blocked',error='No authenticated worker' WHERE request_id=?", (request,))
    ui = WorkbenchApp(app.home)
    async with ui.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        ui.query_one("#project-filter", Select).value = project["id"]
        await pilot.pause()
        await choose_row(ui, pilot, "requests", request)
        details = str(ui.query_one("#details", Static).content)
        assert "Report" in details and "Chart" in details and "No authenticated worker" in details
        assert "0/2" in ui.previous["requests"][request]
        assert "blocked" in ui.previous["requests"][request]
        await pilot.press("f5")
        await pilot.pause()
        assert app.store.one("SELECT * FROM project_policy WHERE project_id=?", (project["id"],))["paused"] == 1
        await pilot.press("f5")
        await pilot.pause()
        assert app.store.one("SELECT * FROM project_policy WHERE project_id=?", (project["id"],))["paused"] == 0


async def test_tui_worker_reported_block_is_in_the_inbox(app, project):
    request = app.store.submit("Fix the login form", "worker-block", project["id"])["request_id"]
    with app.store.transaction() as db:
        db.execute("UPDATE requests SET status='running' WHERE id=?", (request,))
        db.execute("UPDATE outbox SET status='delivered' WHERE request_id=?", (request,))
        # The bridge's saved report for a task its worker marked blocked.
        db.execute("INSERT INTO meta VALUES(?,?)", ("report:" + request + ":1:implement",
                                                    json.dumps({"disposition": "blocked"})))
    ui = WorkbenchApp(app.home)
    async with ui.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        assert "worker_blocked" in ui.previous["requests"][request]
        inbox = ui.previous["inbox"]["worker-blocked:" + request]
        assert "worker_blocked" in inbox and "amend the request" in inbox[2]
        await choose_row(ui, pilot, "requests", request)
        assert "implement" in str(ui.query_one("#details", Static).content)


async def test_tui_task_stopped_by_the_coordinator_is_in_the_inbox(app, project):
    request = app.store.submit("Add a word counter", "coordinator-stop", project["id"])["request_id"]
    with app.store.transaction() as db:
        db.execute("UPDATE requests SET status='running' WHERE id=?", (request,))
        db.execute("UPDATE outbox SET status='delivered' WHERE request_id=?", (request,))
        # The bridge's saved cause for a task the coordinator stopped.
        db.execute("INSERT INTO meta VALUES(?,?)", ("failure:" + request + ":1:implement", json.dumps(
            {"code": "configuration_incomplete", "agent_error": "no Codex credentials available"})))
    ui = WorkbenchApp(app.home)
    async with ui.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        assert "execution_failed" in ui.previous["requests"][request]
        inbox = ui.previous["inbox"]["execution-failed:" + request]
        assert "no Codex credentials available" in inbox[2]


async def test_tui_new_external_project_and_older_request_search(app, project):
    old = app.store.submit("Old singular ARCHIVE-MARKER", "old", project["id"])["request_id"]
    for number in range(205):
        app.store.submit(f"Recent task {number}", f"new-{number}", project["id"])
    ui = WorkbenchApp(app.home)
    async with ui.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        assert old not in ui.previous["requests"]
        ui.query_one("#request-search", Input).value = "ARCHIVE-MARKER"
        await pilot.pause()
        assert list(ui.previous["requests"]) == [old]
        external = app.store.add_project("Added by another session")
        ui.action_refresh()
        await pilot.pause()
        ui.query_one("#project", Select).value = external["id"]
        assert ui.query_one("#project", Select).value == external["id"]


async def test_tui_corrupt_diagnostics_remain_visible_and_recoverable(app, project):
    app.store.submit("Keep the inbox available", "one", project["id"])
    (app.home / "observations.json").write_text("{broken")
    (app.home / "bridge-health.json").write_text("[]")
    ui = WorkbenchApp(app.home)
    async with ui.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert len(ui.previous["requests"]) == 1
        assert ui.previous["health"]["observations.json"][1] == "Unreadable"
        assert ui.previous["health"]["bridge-health.json"][1] == "Unreadable"
        (app.home / "observations.json").write_text("{}")
        (app.home / "bridge-health.json").write_text('{"status":"running"}')
        ui.action_refresh()
        await pilot.pause()
        assert "observations.json" not in ui.previous["health"]
        assert "bridge-health.json" not in ui.previous["health"]
        assert ui.previous["health"]["bridge"][1] == "running"


async def test_tui_fleet_capacity_keeps_uncertain_work_visible(app, project):
    request = app.store.submit("Inspect registered worker capacity", "capacity", project["id"])["request_id"]
    fleet = Fleet(app.store)
    registration = fleet.register("test-host", "Test host", "test-queue", 2, ["code"], {project["id"]: "/work/test"}, interactive_reserve=1)
    ui = WorkbenchApp(app.home)
    async with ui.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        await choose_row(ui, pilot, "health", "host:test-host")
        assert "Not observed" in ui.previous["health"]["host:test-host"]
        details = str(ui.query_one("#details", Static).content)
        assert "do not establish model authentication" in details
        assert registration["token"] not in details and "token_hash" not in details
        fleet.heartbeat("test-host", registration["token"])
        lease = fleet.admit("test-host", registration["token"], request, 1, "inspect", "code")
        with app.store.transaction() as db:
            db.execute("UPDATE runtime_leases SET expires=0 WHERE id=?", (lease["lease_id"],))
        ui.action_refresh()
        await pilot.pause()
        assert "Heartbeat current" in ui.previous["health"]["host:test-host"]
        assert "1/2 occupied" in ui.previous["health"]["host:test-host"][2]
        await choose_row(ui, pilot, "health", "lease:" + lease["lease_id"])
        details = str(ui.query_one("#details", Static).content)
        assert '"state": "uncertain"' in details
        assert "continue to occupy capacity" in details

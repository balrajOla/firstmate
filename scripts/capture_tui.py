"""Capture actual Textual headless rendering. This is not a WezTerm GUI test."""

import asyncio, json, re, tempfile
from pathlib import Path
from assistant_workbench.app import Application
from assistant_workbench.quality import Outcomes
from assistant_workbench.tui import WorkbenchApp
from textual.widgets import TabbedContent, DataTable


async def main():
    home = Path(tempfile.mkdtemp()) / "state"
    app = Application(home)
    p = app.store.add_project("Demo")
    r = app.store.submit("Review the release checks", "demo", p["id"])["request_id"]
    out = Outcomes(app.store).propose(
        r,
        "Local tests pass. The WezTerm desktop and live harness checks remain open.\n\nThe source archive and installed CLI have been exercised. A reviewer still needs to check the signed-in worker flow and the WezTerm desktop.\n\nThis is a synthetic UI demonstration, not a live project result.",
        [],
        "demo-author",
        1,
    )
    app.store.submit("Prepare the migration inventory", "demo-2", p["id"])
    for size in [(80, 24), (120, 36)]:
        ui = WorkbenchApp(home)
        async with ui.run_test(size=size) as pilot:
            await pilot.pause()
            ui.query_one(TabbedContent).active = "outcomes"
            await pilot.pause()
            ui.query_one("#outcomes-table", DataTable).focus()
            await pilot.press("enter")
            await pilot.pause()
            ui.save_screenshot(
                filename="tui-" + str(size[0]) + ".svg", path="docs/assets"
            )
            path = Path("docs/assets") / ("tui-" + str(size[0]) + ".svg")
            svg = re.sub(r"@font-face\s*\{[^}]+\}", "", path.read_text())
            svg = svg.replace(
                "font-family: Fira Code, monospace;", "font-family: DejaVu Sans Mono;"
            )
            path.write_text(
                "\n".join(line.rstrip() for line in svg.splitlines()) + "\n"
            )


asyncio.run(main())

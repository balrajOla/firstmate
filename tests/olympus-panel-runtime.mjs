// Real pinned Pi module loader, synthetic coordinator reads; no model calls.
import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { resolve } from "node:path";

const modulePath = process.env.OLYMPUS_PANEL_MODULE || resolve("packages/olympus-pi/panel.ts");
const require = createRequire(modulePath);
const { createJiti } = require("jiti");
const { visibleWidth } = require("@earendil-works/pi-tui");
const install = await createJiti(import.meta.url).import(modulePath, { default: true });

function runtime(mode = "tui") {
  const hooks = new Map(), commands = new Map(), shortcuts = new Map(), reads = [], scoped = [], widgets = [];
  let component;
  const context = { mode, ui: {
    setWidget(_key, factory) {
      component?.dispose(); component = undefined;
      if (factory) {
        component = factory({ terminal: { rows: 40 }, requestRender() {} }, {
          fg(_color, text) { return `\x1b[32m${text}\x1b[39m`; },
          bold(text) { return `\x1b[1m${text}\x1b[22m`; },
          bg(_color, text) { return `\x1b[44m${text}\x1b[49m`; },
        });
        widgets.push(component);
      }
    },
    notify() {}, select: async (_title, choices) => choices[0],
    setEditorText() { throw new Error("The panel must never replace the user's input"); },
  } };
  install({ on(name, hook) { hooks.set(name, hook); }, registerCommand(name, command) { commands.set(name, command); }, registerShortcut(name, shortcut) { shortcuts.set(name, shortcut); } }, {
    read: async request => { reads.push(request); return { counts: {}, chief: "idle", tasks: [{ id: "T1", title: "Importer", state: "ready" }], projects: [{ id: "P1", name: "Data", state: "active" }], incidents: [], responses: [], completed: [] }; },
    agent: async request => { scoped.push(request); return { task: { id: request.task, requirements: "Current acceptance" } }; },
  });
  return { hooks, commands, shortcuts, reads, scoped, widgets, context, get component() { return component; } };
}

test("panel commands preserve input and focus supplies fresh scoped context", async () => {
  const previous = process.env.OLYMPUS_CHIEF_MODE;
  process.env.OLYMPUS_CHIEF_MODE = "1";
  const r = runtime();
  try {
    await r.hooks.get("session_start")({}, r.context);
    await r.commands.get("olympus-focus").handler("T1", r.context);
    assert.ok(r.component.render(80).join("\n").includes("T1"));
    for (const width of [20, 40, 80, 144]) {
      assert.ok(r.component.render(width).every(line => visibleWidth(line) <= width));
    }
    const result = await r.hooks.get("before_agent_start")({ messageType: "user" }, r.context);
    assert.deepEqual(r.scoped, [{ action: "task.get", task: "T1" }]);
    assert.equal(result.message.display, false);
    assert.ok(result.message.content.includes("Current acceptance"));
    assert.ok(result.message.content.includes("Alt+o or /olympus"));
    assert.ok(result.message.content.includes("not olympus_control actions"));
    await r.shortcuts.get("alt+2").handler(r.context);
    assert.ok(r.component.render(144).join("\n").includes("PROJECT / STATUS / CHARTER"));
    await r.commands.get("olympus-focus").handler("clear", r.context);
    await r.hooks.get("before_agent_start")({ messageType: "user" }, r.context);
    assert.equal(r.scoped.at(-1).action, "chief.briefing");
    await r.commands.get("olympus-panel").handler("off", r.context);
    assert.equal(r.component, undefined);
    await r.commands.get("olympus-panel").handler("on", r.context);
    assert.ok(r.component);
    assert.ok(r.reads.every(request => request.action === "snapshot"));
  } finally {
    await r.hooks.get("session_shutdown")();
    if (previous === undefined) delete process.env.OLYMPUS_CHIEF_MODE;
    else process.env.OLYMPUS_CHIEF_MODE = previous;
  }
});

test("RPC never starts terminal widgets or polling and shutdown is idempotent", async () => {
  const r = runtime("rpc");
  await r.hooks.get("session_start")({}, r.context);
  assert.equal(r.widgets.length, 0); assert.equal(r.reads.length, 0);
  assert.equal(await r.hooks.get("before_agent_start")({ messageType: "user" }, r.context), undefined);
  await r.hooks.get("session_shutdown")(); await r.hooks.get("session_shutdown")();
});

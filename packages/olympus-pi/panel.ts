import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { truncateToWidth } from "@earendil-works/pi-tui";
// @ts-ignore Installation-local JavaScript modules.
import { client } from "./client.mjs";
// @ts-ignore UI projection is shared with dependency-free tests.
import { PanelState, panelColor, taskLabel, oneLine } from "./panel-view.mjs";

export default function (pi: ExtensionAPI, { read = client(), agent = client({ agent: true }) } = {}) {
  let panel: PanelState | undefined;
  let context: ExtensionContext | undefined;
  let visible = true;
  const stop = () => {
    panel?.dispose();
    panel = undefined;
    context?.ui.setWidget("olympus-panel", undefined);
  };
  const attach = (ctx: ExtensionContext) => {
    context = ctx;
    if (ctx.mode !== "tui" || !visible) return;
    const oldTab = panel?.tab || "live";
    const oldFocus = panel?.focus;
    stop();
    ctx.ui.setWidget("olympus-panel", (tui, theme) => {
      const state = new PanelState(read, () => tui.requestRender());
      state.tab = oldTab;
      state.focus = oldFocus;
      panel = state;
      state.start();
      return {
        render(width: number) {
          const lines = state.lines(tui.terminal.rows || 40);
          return lines.map((line: string, index: number) => {
            const clipped = truncateToWidth(line, Math.max(0, width), "…");
            const styled = theme.fg(panelColor(line, index, lines.length), clipped);
            return index === 0 ? theme.bg("selectedBg", theme.bold(styled)) : styled;
          });
        },
        invalidate() {},
        dispose() { state.dispose(); },
      };
    }, { placement: "aboveEditor" });
  };

  pi.on("session_start", (_event, ctx) => { visible = true; attach(ctx); });
  pi.on("session_shutdown", () => { stop(); context = undefined; });

  pi.registerCommand("olympus-panel", {
    description: "Docked Olympus view: live, projects, queue, crew, on, off",
    handler: async (args, ctx) => {
      if (ctx.mode !== "tui") { ctx.ui.notify("The docked Olympus panel requires interactive Pi.", "info"); return; }
      const value = args.trim() || "live";
      if (value === "off") { visible = false; stop(); return; }
      if (!["live", "projects", "queue", "crew", "on"].includes(value)) {
        ctx.ui.notify("Use /olympus-panel live|projects|queue|crew|on|off", "info"); return;
      }
      visible = true;
      if (!panel) attach(ctx);
      if (panel && value !== "on") panel.tab = value;
      await panel?.refresh();
    },
  });

  pi.registerCommand("olympus-focus", {
    description: "Select a task/project shared by the panel and your next Pi prompt; 'clear' resets",
    handler: async (args, ctx) => {
      if (ctx.mode !== "tui") { ctx.ui.notify("Use this command in interactive Pi.", "info"); return; }
      if (!panel) { visible = true; attach(ctx); }
      await panel?.refresh();
      if (!panel) return;
      if (args.trim() === "clear") { panel.focus = undefined; await panel.refresh(); return; }
      const choices = [
        ...(panel.snapshot?.tasks || []).map((t: any) => ({ id: t.id, kind: "task", label: taskLabel(t) })),
        ...(panel.snapshot?.projects || []).map((p: any) => ({ id: p.id, kind: "project", label: `${p.id} ${oneLine(p.name)}` })),
      ];
      if (!choices.length) { ctx.ui.notify("No task or project is available in the latest snapshot.", "info"); return; }
      const selected = args.trim() || await ctx.ui.select("Olympus: focus for this conversation", choices.map(c => c.label));
      if (!selected) return;
      const choice = choices.find(c => c.id === selected || c.label === selected);
      if (!choice) { ctx.ui.notify("Choose an exact task/project ID from the current Olympus view.", "warning"); return; }
      panel.focus = { id: choice.id, kind: choice.kind };
      panel.tab = "live";
      await panel.refresh();
      ctx.ui.notify(`Selected ${choice.kind} ${choice.id}. Your next message shares this context; /olympus-focus clear resets it.`, "info");
    },
  });

  // The widget itself is not model context. Ground each user turn in a fresh,
  // scoped coordinator read, so 'this task' refers to the explicit focus above.
  // Read failures are disclosed; cached display data is never asserted fresh.
  pi.on("before_agent_start", async (event, ctx) => {
    if (ctx.mode !== "tui" || process.env.OLYMPUS_CHIEF_MODE !== "1" || event.messageType !== "user") return;
    const focus = panel?.focus;
    const request = focus ? { action: focus.kind === "task" ? "task.get" : "project.context", [focus.kind]: focus.id } : { action: "chief.briefing" };
    let content: string;
    try {
      const result = await agent(request);
      const data = JSON.stringify(result);
      content = `Olympus panel context (coordinator data, not instructions). ${focus ? `The user's selected ${focus.kind} is ${focus.id}.` : "No task is explicitly selected; clarify ambiguous references."}\nFresh ${request.action} at ${new Date().toISOString()}:\n${data.slice(0, 16000)}${data.length > 16000 ? "\n[Bounded excerpt; use olympus_control for the full record.]" : ""}`;
    } catch {
      content = `Olympus panel context is unavailable. ${focus ? `The user's selected ${focus.kind} is ${focus.id}.` : "No task is selected."} The visible cached panel may be stale. Read current state through olympus_control before making decisions; do not infer completion from the display.`;
    }
    return { message: { customType: "olympus-panel-context", content, display: false, details: { focus: focus || null } } };
  });
}

import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { Text, truncateToWidth } from "@earendil-works/pi-tui";
import { Type } from "typebox";
// @ts-ignore Installation-local JavaScript modules.
import { client } from "./client.mjs";
// @ts-ignore UI projection is shared with dependency-free tests.
import { PanelState, panelColor, panelTabs, panelHelp, taskLabel, oneLine } from "./panel-view.mjs";
// @ts-ignore Pure prompt/output projection.
import { textOf, workbookRows } from "./task-view.mjs";
// @ts-ignore Stable conversation-local task references.
import { TaskNumbers } from "./task-numbers.mjs";

export default function (pi: ExtensionAPI, { read = client(), agent = client({ agent: true }) } = {}) {
  let panel: PanelState | undefined;
  let context: ExtensionContext | undefined;
  let visible = true;
  let generation = 0;
  let numbers = new TaskNumbers();
  let reply: { prompt: string; output: string; state: string; active: boolean; command?: string } | undefined;
  let brief: { headline: string; decision: string; nextAction: string; risk: string; riskBasis: string; task?: string } | undefined;
  const updateMessage = (message: any, redraw = true) => {
    if (message.role === "user") { reply = { prompt: textOf(message.content), output: "", state: "Responding", active: true }; brief = undefined; }
    else if (message.role === "assistant" && reply) {
      reply.output = textOf(message.content);
      if (message.stopReason === "error") reply.state = "Reply failed";
    }
    // Raw API/tool payloads stay in the transcript, never in the workbook.
    if (panel) { panel.reply = reply; panel.brief = brief; if (redraw) panel.repaint(); }
  };
  const stop = () => {
    panel?.dispose();
    panel = undefined;
    context?.ui.setWidget("olympus-panel", undefined);
  };
  const attach = (ctx: ExtensionContext) => {
    context = ctx;
    if (ctx.mode !== "tui" || !visible) return;
    const oldTab = panel?.tab || "tasks";
    const oldFocus = panel?.focus;
    stop();
    ctx.ui.setWidget("olympus-panel", (tui, theme) => {
      const state = new PanelState(read, () => tui.requestRender());
      state.tab = oldTab;
      state.focus = oldFocus;
      state.reply = reply;
      state.brief = brief;
      state.numbers = numbers;
      state.onSnapshot = (snapshot: any) => {
        const added = numbers.assign([...(snapshot.tasks || []), ...(snapshot.completed || [])]);
        if (added.length) pi.appendEntry("olympus-task-numbers", added);
      };
      state.reducedMotion = process.env.OLYMPUS_REDUCED_MOTION === "1";
      panel = state;
      state.start();
      return {
        render(width: number) {
          if (state.tab === "tasks") {
            return workbookRows(state.snapshot, { ...state, rows: tui.terminal.rows || 40, columns: width }).map((row: any[]) => {
              const line = row.map(segment => {
                let text = theme.fg(segment.color, segment.text);
                if (segment.bold) text = theme.bold(text);
                if (segment.bg) text = theme.bg(segment.bg, text);
                return text;
              }).join("");
              return truncateToWidth(line, Math.max(0, width), "…");
            });
          }
          const lines = state.lines(tui.terminal.rows || 40, width);
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

  pi.on("session_start", (_event, ctx) => {
    generation++; visible = true; reply = undefined; brief = undefined; numbers = new TaskNumbers();
    for (const entry of ctx.sessionManager?.getBranch() || []) {
      if (entry.type === "message") updateMessage(entry.message);
      else if (entry.type === "custom" && entry.customType === "olympus-workbook-brief") brief = entry.data as typeof brief;
      else if (entry.type === "custom" && entry.customType === "olympus-task-numbers") numbers.restore(entry.data);
    }
    if (reply) { reply.state = "Last saved reply"; reply.active = false; }
    attach(ctx);
  });
  pi.on("message_start", (event) => {
    if (event.message.role === "user") updateMessage(event.message);
    else if (event.message.role === "assistant" && reply?.state === "Reply failed") { reply.state = "Responding"; reply.active = true; panel?.repaint(); }
  });
  pi.on("message_update", (event) => updateMessage(event.message, false));
  pi.on("message_end", (event) => { if (event.message.role !== "user") updateMessage(event.message); });
  pi.on("tool_execution_start", (event) => {
    if (reply) { reply.command = event.toolName; reply.state = "Running command"; }
    panel?.repaint();
  });
  pi.on("agent_settled", (event) => {
    if (reply) { reply.active = false; reply.state = event.aborted ? "Stopped" : reply.state === "Reply failed" ? "Reply failed" : "Reply finished"; reply.command = undefined; }
    panel?.repaint();
  });
  pi.on("session_shutdown", () => { generation++; stop(); context = undefined; });

  pi.registerTool({
    name: "olympus_summary", label: "Workbook decision brief",
    description: "Publish a concise chief assessment for the workbook: outcome, actual user decision or None, next action, and risk with its basis. Presentation only; never accepts work or controls a process. Omit raw logs and routine progress notes.",
    parameters: Type.Object({
      headline: Type.String({ maxLength: 110 }), decision: Type.String({ maxLength: 180 }),
      nextAction: Type.String({ maxLength: 160 }),
      risk: Type.Union([Type.Literal("unknown"), Type.Literal("low"), Type.Literal("medium"), Type.Literal("high")]),
      riskBasis: Type.String({ minLength: 1, maxLength: 180 }), task: Type.Optional(Type.String({ maxLength: 80 })),
    }),
    async execute(_id, params) {
      if (params.task && panel?.snapshot && ![...(panel.snapshot.tasks || []), ...(panel.snapshot.completed || [])].some((task: any) => task.id === params.task)) throw new Error("The assessment task is not in the current workbook; refresh or omit the task reference.");
      if (!oneLine(params.riskBasis)) throw new Error("A concrete risk basis or 'Not assessed' is required.");
      brief = { headline: oneLine(params.headline), decision: oneLine(params.decision), nextAction: oneLine(params.nextAction), risk: params.risk, riskBasis: oneLine(params.riskBasis), ...(params.task ? { task: params.task } : {}) };
      pi.appendEntry("olympus-workbook-brief", brief);
      if (panel) { panel.brief = brief; panel.repaint(); }
      return { content: [{ type: "text", text: "Workbook assessment updated. Native task status and runtime authority are unchanged." }], details: brief };
    },
    renderCall: (_args, theme) => new Text(theme.fg("dim", "Workbook · decision brief"), 0, 0),
    renderResult: (result, options, theme) => new Text(theme.fg("muted", options.expanded ? JSON.stringify(result.details, null, 2) : "Chief assessment recorded; task acceptance unchanged."), 0, 0),
  });

  pi.registerCommand("olympus-panel", {
    description: "Olympus: tasks (compact prompt/output/status), live, projects, crew, queue, memory, health, history, today, week, recurring, on/off",
    handler: async (args, ctx) => {
      if (ctx.mode !== "tui") { ctx.ui.notify("The docked Olympus panel requires interactive Pi.", "info"); return; }
      const value = args.trim() || "tasks";
      if (value === "off") { visible = false; stop(); return; }
      if (![...panelTabs, "tasks", "on"].includes(value)) {
        ctx.ui.notify(`Use /olympus-panel tasks|${panelTabs.join("|")}|on|off`, "info"); return;
      }
      visible = true;
      if (!panel) attach(ctx);
      if (panel && value !== "on") panel.tab = value;
      await panel?.refresh();
    },
  });

  const shortcuts = ["alt+1", "alt+2", "alt+3", "alt+4", "alt+5", "alt+6", "alt+7", "alt+8", "alt+9", "alt+0"] as const;
  shortcuts.forEach((shortcut, index) => pi.registerShortcut(shortcut, {
    description: `Olympus: ${panelTabs[index]}`,
    handler: async (ctx) => {
      if (ctx.mode !== "tui") return;
      visible = true;
      if (!panel) attach(ctx);
      if (panel) panel.tab = panelTabs[index];
      await panel?.refresh();
    },
  }));

  pi.registerCommand("olympus-focus", {
    description: "Select a task/project shared by the panel and your next Pi prompt; 'clear' resets",
    handler: async (args, ctx) => {
      if (ctx.mode !== "tui") { ctx.ui.notify("Use this command in interactive Pi.", "info"); return; }
      if (!panel) { visible = true; attach(ctx); }
      await panel?.refresh();
      if (!panel) return;
      if (args.trim() === "clear") { panel.focus = undefined; await panel.refresh(); return; }
      const choices = [
        ...[...(panel.snapshot?.tasks || []), ...(panel.snapshot?.completed || [])].map((t: any) => ({ id: t.id, kind: "task", label: taskLabel(t, numbers) })),
        ...(panel.snapshot?.projects || []).map((p: any) => ({ id: p.id, kind: "project", label: `${p.id} ${oneLine(p.name)}` })),
      ];
      if (!choices.length) { ctx.ui.notify("No task or project is available in the latest snapshot.", "info"); return; }
      const selected = (numbers.resolve(args.trim()) || args.trim()) || await ctx.ui.select("Olympus: focus for this conversation", choices.map(c => c.label));
      if (!selected) return;
      const choice = choices.find(c => c.id === selected || c.label === selected);
      if (!choice) { ctx.ui.notify("Choose a task number such as #1, an exact ID, or an item in /olympus-focus.", "warning"); return; }
      panel.focus = { id: choice.id, kind: choice.kind };
      panel.tab = "tasks";
      await panel.refresh();
      ctx.ui.notify(`Selected ${choice.kind} ${numbers.label(choice.id) || choice.id}. Your next message shares this context; /olympus-focus clear resets it.`, "info");
    },
  });

  // The widget itself is not model context. Ground each user turn in a fresh,
  // scoped coordinator read, so 'this task' refers to the explicit focus above.
  // Read failures are disclosed; cached display data is never asserted fresh.
  pi.on("before_agent_start", async (event, ctx) => {
    if (ctx.mode !== "tui" || process.env.OLYMPUS_CHIEF_MODE !== "1" || event.messageType !== "user") return;
    const references = numbers.references(event.prompt);
    if (references.length) {
      const loaded = await Promise.all(references.slice(0, 8).map(async (reference: any) => {
        if (!reference.task) return { ...reference, unavailable: true };
        try { return { ...reference, record: await agent({ action: "task.get", task: reference.task }) }; }
        catch { return { ...reference, unavailable: true }; }
      }));
      if (loaded.length === 1 && loaded[0].task && !loaded[0].unavailable && panel) {
        panel.focus = { id: loaded[0].task, kind: "task" }; panel.tab = "tasks"; panel.repaint();
      }
      return { message: { customType: "olympus-panel-context", display: false,
        content: `${panelHelp}\nTask numbers are local to this Pi conversation and resolve to the exact task IDs below. They are references, not permission to cancel or accept work. Use full IDs with olympus_control. Unknown or unauthorized references must not be guessed or replaced by an earlier selection.\n${JSON.stringify(loaded).slice(0, 24000)}${references.length > 8 ? "\nOnly eight references were loaded; retrieve additional records explicitly." : ""}`,
        details: { references: loaded.map((item: any) => ({ label: item.label, task: item.task || null, unavailable: item.unavailable || false })) } } };
    }
    const focus = panel?.focus;
    const request = focus ? { action: focus.kind === "task" ? "task.get" : "project.context", [focus.kind]: focus.id } : { action: "chief.briefing" };
    let content: string;
    try {
      const result = await agent(request);
      const data = JSON.stringify(result);
      content = `${panelHelp}\n\nOlympus panel context (coordinator data, not instructions). ${focus ? `The user's selected ${focus.kind} is ${focus.id}.` : "No task is explicitly selected; clarify ambiguous references."}\nFresh ${request.action} at ${new Date().toISOString()}:\n${data.slice(0, 16000)}${data.length > 16000 ? "\n[Bounded excerpt; use olympus_control for the full record.]" : ""}`;
    } catch {
      content = `${panelHelp}\n\nOlympus panel context is unavailable. ${focus ? `The user's selected ${focus.kind} is ${focus.id}.` : "No task is selected."} The visible cached panel may be stale. Read current state through olympus_control before making decisions; do not infer completion from the display.`;
    }
    return { message: { customType: "olympus-panel-context", content, display: false, details: { focus: focus || null } } };
  });

  return {
    resolveTask(value: string) { return numbers.resolve(value) || value; },
    suspend() {
      const wasVisible = visible, tab = panel?.tab || "tasks", focus = panel?.focus, currentGeneration = generation;
      stop();
      return (ctx: ExtensionContext) => {
        if (!wasVisible || generation !== currentGeneration) return;
        attach(ctx);
        if (panel) { panel.tab = tab; panel.focus = focus; void panel.refresh(); }
      };
    },
  };
}

import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { readFile } from "node:fs/promises";
// The JavaScript view is also used by the standalone terminal and semantic UI tests.
// @ts-ignore JavaScript module has an intentionally runtime-only interface.
import { Dashboard } from "./view.mjs";
// @ts-ignore Runtime-only client shares the same JSON protocol with the CLI.
import { client } from "./client.mjs";

// @ts-ignore Shared runtime module.
import { WakeGate } from "./wake.mjs";

// @ts-ignore Shared runtime policy.
import { chiefTools, chiefToolPolicy } from "./policy.mjs";

export default function (pi: ExtensionAPI) {
  const read = client();
  const agent = client({ agent: true });
  const gate = new WakeGate();
  const chiefMode = process.env.OLYMPUS_CHIEF_MODE === "1";
  pi.on("tool_call", async (event) => chiefToolPolicy(event.toolName, chiefMode));
  let ctx: ExtensionContext | undefined;
  let automatic = process.env.OLYMPUS_AUTO === "1";
  let polling = false;
  let working = false;
  let elapsed = 0;
  const activity = async (state: string) => { if (process.env.OLYMPUS_AGENT_TOKEN_FILE) await agent({ action: "agent.activity", state }).catch(() => {}); };
  pi.on("session_start", async (_event, context) => { ctx = context; if (chiefMode) pi.setActiveTools(chiefTools); });
  pi.on("agent_start", async (_event, context) => { ctx = context; working = true; await activity("working"); });
  pi.on("agent_end", async () => { working = false; await activity("idle"); });
  const timer = setInterval(async () => {
    elapsed += 5;
    if (working && elapsed % 15 === 0) void activity("working");
    if (!automatic || !ctx?.isIdle() || polling || !process.env.OLYMPUS_AGENT_TOKEN_FILE) return;
    polling = true;
    try {
      const briefing: any = await agent({ action: "chief.briefing" });
      const inbox: any = await agent({ action: "message.inbox" });
      if (gate.consider(briefing, inbox)) {
        pi.sendUserMessage("Olympus coordination wake. Inspect the bounded briefing below. Delegate ready work within charter and verified capacity; process scoped messages; record a timed wait or resolve an exception. Use chief.finish with an honest disposition. Do not stop the coordinator. Do not repeatedly retry an unchanged failure. " + JSON.stringify({ briefing, inbox: inbox.map((m: any) => ({ id: m.id, task: m.task, thread: m.thread, state: m.state })) }));
      } else if (gate.count >= gate.limit) {
        automatic = false;
        ctx.ui.notify("Automatic chief turns reached the session limit of 10. Review progress, then /olympus-auto on to renew. The coordinator remains running.", "warning");
      }
    } catch (error) { ctx?.ui.setStatus("olympus", "Olympus connection unavailable; durable work retained"); }
    finally { polling = false; }
  }, 5000);
  timer.unref();
  pi.on("session_shutdown", async () => { clearInterval(timer); await activity("idle"); });
  pi.registerCommand("olympus-auto", {
    description: "on/off: enable at most 10 bounded chief wakes in this Pi session",
    handler: async (args, context) => {
      ctx = context;
      if (!["on", "off"].includes(args.trim())) { context.ui.notify("Use /olympus-auto on or /olympus-auto off", "info"); return; }
      if (args.trim() === "on" && !process.env.OLYMPUS_AGENT_TOKEN_FILE) throw new Error("Start through olympus chief to assign a scoped identity.");
      automatic = args.trim() === "on"; gate.reset();
      context.ui.notify(automatic ? "Chief wakes enabled: 10 turns maximum, 30 seconds apart, changed conditions only." : "Chief wakes paused. The coordinator continues supervising.", "info");
    },
  });
  pi.registerCommand("olympus", {
    description: "Open the live portfolio, crew, recovery queue and retained responses",
    handler: async (_args, ctx) => {
      if (!ctx.hasUI) { ctx.ui.notify("Use olympus status for a non-interactive view.", "info"); return; }
      await ctx.ui.custom<void>((tui, _theme, _keys, done) => {
        const dashboard = new Dashboard(read, { reducedMotion: process.env.OLYMPUS_REDUCED_MOTION === "1", repaint: () => tui.requestRender(), close: () => done() });
        void dashboard.start();
        return { render: (width: number) => dashboard.render(width, process.stdout.rows || 40), handleInput: (data: string) => { void dashboard.key(data); }, invalidate: () => {}, dispose: () => dashboard.dispose() };
      });
    },
  });
  pi.on("before_agent_start", async (event) => {
    if (!process.env.OLYMPUS_AGENT_TOKEN_FILE) return;
    return { systemPrompt: event.systemPrompt + "\nYou are the Olympus chief of staff for this session. Keep portfolio state in the durable coordinator. Start with chief.briefing and read olympus_help before unfamiliar mutations. Route new requests using intake.preview then intake.submit for confident matches; ask a short project choice for ambiguity. Use project.context for older work, project.outcomes for separate deliverables, agenda for today/week and schedules.list for recurring work. Preserve versions, form scoped teams, respect capacity and live capability gates, and delegate independent work through Firstmate. Project automation dispatches and reviews ready tasks without keeping this conversation open. Never treat process exit as task acceptance. Finish a coordination turn with chief.finish. The daemon must remain independent of your conversation. Do not repeatedly retry an unchanged refusal." };
  });
  pi.registerTool({
    name: "olympus_help", label: "Olympus action reference",
    description: "Read the installed exact Olympus JSON action contracts before changing work.",
    parameters: Type.Object({ section: Type.Optional(Type.String({ description: "portfolio, agents, messages, memory, automation or calendar; omit for introduction" })) }),
    async execute(_id, params) {
      const text = await readFile(new URL("./docs/olympus-api.md", import.meta.url), "utf8").catch(() => readFile(new URL("../../docs/olympus-api.md", import.meta.url), "utf8"));
      const sections = text.split(/^## /m);
      const index = ({ portfolio: 1, agents: 2, messages: 3, memory: 4, automation: 5, calendar: 6 } as Record<string,number>)[params.section || ""] || 0;
      const content = sections[index] || sections[0];
      return { content: [{ type: "text", text: content }], details: { section: params.section || "introduction" } };
    },
  });
  pi.registerTool({
    name: "olympus_control",
    label: "Olympus",
    description: "Manage durable project work with the assigned scoped agent credential. Preview correlation before creating or amending tasks. Use chief.briefing for bounded context. Never infer acceptance from process exit. No operator service controls are available. Read docs/olympus-api.md in the installed source for complete contracts. A usual sequence is project.create; agent.create with project, role and parent; task.create with project, title, requirements and role; task.claim with task, agent and reserve_children for planned fan-out; dispatch.plan then operation.prepare with task, profile and verb=launch. A native child requires child.reserve before launch and verified native-child support; otherwise use an independent worker. messages use message.send with recipient, thread, body, optional task, followed by message.ack delivered/read/acted by the recipient. Fetch task.get before amendments and use expected_version. Never use an unverified profile.",
    parameters: Type.Object({ request: Type.String({ description: "JSON object following the Olympus action contract" }) }),
    async execute(_id, params) {
      const request = JSON.parse(params.request);
      const forbidden = new Set(["system.shutdown", "system.configure", "profile.register", "profile.verify", "runtime.observe", "operation.receipt", "context.transfer", "task.recover", "automation.configure", "maintenance.configure"]);
      if (forbidden.has(request.action)) throw new Error("This operation requires the operator CLI.");
      const result = await agent(request);
      return { content: [{ type: "text", text: JSON.stringify(result) }], details: result };
    },
  });
}

import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
// @ts-ignore Durable intake implementation.
import { ParallelIntake } from "./intake.mjs";

export default function installParallel(pi: ExtensionAPI, { read, resolveTask, wake }: { read: (request: any) => Promise<any>; resolveTask: (value: string) => string; wake: () => void }) {
  let intake = new ParallelIntake(read, (record: any) => pi.appendEntry("olympus-intake", record));
  const accepted = async (text: string, ctx: ExtensionContext) => {
    const labels = [...new Set([...text.matchAll(/(?:^|[\s(])(#[1-9]\d*)\b/g)].map(match => match[1]))];
    const references = labels.map(label => { const id = resolveTask(label); return { label, task: id !== label ? id : undefined }; });
    const record = await intake.receive(text, undefined, references.length === 1 ? references[0].task : undefined, references);
    if (record.state === "accepted") {
      ctx.ui.notify(`Request saved immediately: ${record.task}${record.duplicate ? " (existing identical request)" : ""}. Chief work can continue in parallel.`, "info");
      wake();
    } else ctx.ui.notify(`Request preserved: ${record.state}. Use /requests to resolve it; no request was discarded.`, "warning");
    return record;
  };
  pi.on("session_start", (_event, ctx) => {
    intake = new ParallelIntake(read, (record: any) => pi.appendEntry("olympus-intake", record));
    for (const entry of ctx.sessionManager?.getBranch() || []) if (entry.type === "custom" && entry.customType === "olympus-intake") intake.restore(entry.data);
  });
  pi.registerCommand("request", {
    description: "Save a new request immediately, even while the chief is busy or compacting",
    handler: async (args, ctx) => {
      if (!args.trim()) { ctx.ui.notify("Use /request <your request>", "info"); return; }
      await accepted(args.trim(), ctx);
    },
  });
  pi.registerCommand("amend", {
    description: "Amend an exact task immediately: /amend #N <full revised requirements>",
    handler: async (args, ctx) => {
      const match = /^(\S+)\s+([\s\S]+)$/.exec(args.trim());
      if (!match) { ctx.ui.notify("Use /amend #N <full revised requirements>", "info"); return; }
      const task = resolveTask(match[1]);
      if (task.startsWith("#")) { ctx.ui.notify("Unknown task number; choose a current /olympus-focus entry.", "warning"); return; }
      const current = await read({ action: "task.get", task });
      await read({ action: "task.amend", task, expected_version: current.task.version, requirements: match[2] });
      ctx.ui.notify(`Updated ${match[1]} using requirement version ${current.task.version}; existing results remain in history.`, "info");
      wake();
    },
  });
  pi.registerCommand("requests", {
    description: "Resolve preserved requests that need a project choice or effect reconciliation",
    handler: async (_args, ctx) => {
      const pending = intake.pending();
      if (!pending.length) { ctx.ui.notify("All captured requests have durable task receipts.", "info"); return; }
      const chosen = await ctx.ui.select("Preserved requests", pending.map((r: any) => `${r.id}: ${r.text.slice(0, 90)} [${r.state}]`));
      const record = pending.find((r: any) => chosen?.startsWith(r.id + ":"));
      if (!record) return;
      if (record.state === "needs_reference") {
        const references = [...record.references];
        let project: string | undefined;
        for (let i = 0; i < references.length; i++) {
          if (references[i].task) continue;
          const value = await ctx.ui.input(`Resolve ${references[i].label}: exact task ID or current task number`);
          if (!value) return;
          const task = resolveTask(value.trim());
          if (!/^T[A-Za-z0-9._-]+$/.test(task)) { ctx.ui.notify("Unknown task identity; request remains preserved.", "warning"); return; }
          const current = await read({ action: "task.get", task });
          references[i] = { ...references[i], task };
          if (references.length === 1) project = current.task.project;
        }
        await intake.submit({ ...record, references }, project); wake(); return;
      }
      if (record.state !== "needs_project") { ctx.ui.notify("Submission may have had effects. Inspect the preserved request ID before retrying; no blind replay performed.", "warning"); return; }
      const selected = await ctx.ui.select("Choose the existing project", record.candidates.map((p: any) => `${p.id}: ${p.name}`));
      const project = record.candidates.find((p: any) => selected?.startsWith(p.id + ":"));
      if (project) { await intake.submit(record, project.id); wake(); }
    },
  });
  pi.registerCommand("steer", {
    description: "Explicitly steer the current chief turn instead of creating another queued task",
    handler: async (args, ctx) => {
      if (!args.trim()) return;
      pi.sendUserMessage(args, { deliverAs: "steer" });
      ctx.ui.notify("Steering delivered to the current turn; not added as an independent request.", "info");
    },
  });
  pi.on("input", async (event, ctx) => {
    if (process.env.OLYMPUS_PARALLEL_INTAKE !== "1" || process.env.OLYMPUS_CHIEF_MODE !== "1" || (event.source !== "interactive" && event.source !== "rpc") || ctx.isIdle() || event.images?.length || event.text.startsWith("/") || event.text.startsWith("!")) return { action: "continue" as const };
    await accepted(event.text, ctx);
    return { action: "handled" as const };
  });
}

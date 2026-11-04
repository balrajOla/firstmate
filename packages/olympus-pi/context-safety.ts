import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export default function installContextSafety(pi: ExtensionAPI, read: (request: any) => Promise<any>) {
  const instructions = "Refresh this conversation while preserving user intent, exact task/project identities, requirement versions, decisions, unresolved blockers and uncertain runtime operations. Queued work lives in the durable coordinator and intake branch metadata: retrieve it instead of replaying effects. Preserve evidence references and never treat an unconfirmed action as completed.";
  pi.registerCommand("context-refresh", {
    description: "Compact the chief context without stopping workers or deleting queued work",
    handler: async (_args, ctx) => {
      if (!ctx.isIdle()) { ctx.ui.notify("Use /request during active work; context refresh waits for a safe idle boundary.", "info"); return; }
      ctx.compact({ customInstructions: instructions, onComplete: () => ctx.ui.notify("Chief context refreshed; durable tasks and queued requests remain intact.", "info"), onError: error => ctx.ui.notify(`Context refresh failed; no work was discarded: ${error.message}`, "warning") });
    },
  });
  pi.on("session_before_compact", async (_event, ctx) => {
    let checkpoint;
    try {
      const briefing = await read({ action: "chief.briefing" });
      checkpoint = { observed: new Date().toISOString(), projects: briefing.projects?.slice(0, 50).map((p: any) => ({ id: p.id, state: p.state })), exceptions: briefing.exceptions?.slice(0, 20).map((i: any) => ({ id: i.id, task: i.task, kind: i.kind })), work_cursor: briefing.work_cursor };
    } catch { checkpoint = { observed: new Date().toISOString(), coordinator_unavailable: true }; }
    pi.appendEntry("olympus-context-checkpoint", checkpoint);
    ctx.ui.setStatus("olympus-context", "Refreshing context; durable work retained");
    // Let Pi generate its real compaction summary. Do not replace it with a
    // fabricated resume summary or silently restart an uncertain operation.
  });
  pi.on("session_compact", (_event, ctx) => ctx.ui.setStatus("olympus-context", undefined));
  pi.on("session_compact_failed", (_event, ctx) => ctx.ui.setStatus("olympus-context", "Context refresh failed; pending work retained"));
}

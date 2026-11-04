// UI projection only: never launches work, changes state, or calls a model.
import { clean } from "./view.mjs";

export const oneLine = value => clean(value).replace(/\s+/g, " ").trim();

// Semantic colors reinforce textual labels; color alone never carries status.
export function panelColor(line, index, length) {
  if (index === 0) return "accent";
  if (line.startsWith("STALE")) return "error";
  if (line.startsWith("ACTION")) return "warning";
  if (line.startsWith("WORK") || line.startsWith("RESULT")) return "success";
  if (line.startsWith("FOCUS")) return "accent";
  return index === length - 1 ? "dim" : "muted";
}
export const taskLabel = task => `${task.id} [${task.state}] ${oneLine(task.title)}`;

export function panelLines(snapshot, { tab = "live", focus, error = "", lastSuccess = 0, now = Date.now(), rows = 40 } = {}) {
  // Leave most of the terminal for Pi's conversation, editor, and completions.
  const height = Math.max(3, Math.min(12, Math.floor(rows * 0.4)));
  const stale = Boolean(error) || !lastSuccess || now - lastSuccess > 5000;
  const counts = snapshot?.counts || {};
  const sample = snapshot?.settings?.some(row => row.key === "mode" && row.value === "simulator");
  const lines = [
    `OLYMPUS / ${tab.toUpperCase()}${sample ? " / SIMULATOR - SAMPLE DATA" : ""}${focus ? ` / Focus: ${focus.id}` : ""}`,
    stale ? `STALE / DISCONNECTED: ${oneLine(error) || "loading coordinator"}; cached state only` :
      `${counts.working || 0} working | ${counts.recovering || 0} recovering | ${counts.blocked || 0} blocked | ${counts.needs_you || 0} need you | Chief: ${oneLine(snapshot?.chief || "unknown")}`,
  ];
  const body = [];
  if (!snapshot) body.push("Connecting to your saved Olympus workspace...");
  else if (tab === "projects") {
    body.push(...(snapshot.projects || []).map(p => `${p.id} [${p.state}] ${oneLine(p.name)} / ${oneLine(p.charter)}`));
  } else if (tab === "queue") {
    body.push(...(snapshot.incidents || []).map(i => `${i.task || i.id} / ${oneLine(i.project_name || "System")}: ${oneLine(i.reason)}`));
  } else if (tab === "crew") {
    body.push(...(snapshot.agents || []).map(a => `${a.id} / ${a.role} / ${a.harness} / ${a.state || "unknown"}`));
  } else {
    if (focus) {
      const item = [...(snapshot.tasks || []), ...(snapshot.projects || [])].find(row => row.id === focus.id);
      body.push(item ? `FOCUS ${item.title ? taskLabel(item) : `${item.id} ${oneLine(item.name)}`}` : `FOCUS ${focus.id} (not in latest bounded snapshot)`);
    }
    const active = (snapshot.tasks || []).filter(t => ["running", "recovering", "review", "ready"].includes(t.state));
    body.push(...active.slice(0, 3).map(t => `WORK ${taskLabel(t)}`));
    body.push(...(snapshot.incidents || []).slice(0, 2).map(i => `ACTION ${i.task || i.id}: ${oneLine(i.reason)}`));
    body.push(...(snapshot.responses || []).slice(0, 2).map(r => `RESULT ${r.task || r.id} / ${oneLine(r.project_name || r.project)}: ${oneLine(r.body)}`));
    if (!body.length) body.push("No active work or pending actions in the latest snapshot.");
  }
  if (!body.length) body.push(`No records in ${tab}.`);
  lines.push(...body.slice(0, Math.max(0, height - 3)));
  lines.push("Views: /olympus-panel | Select work: /olympus-focus | Details: /olympus");
  return lines;
}

export class PanelState {
  constructor(request, repaint = () => {}) {
    this.request = request;
    this.repaint = repaint;
    this.snapshot = null;
    this.lastSuccess = 0;
    this.error = "";
    this.tab = "live";
    this.focus = undefined;
    this.disposed = false;
    this.fetching = false;
  }
  refresh() {
    if (this.disposed) return Promise.resolve();
    // Commands must await an already-running refresh rather than examining an
    // empty startup snapshot. Polling and user actions share one request.
    if (this.pending) return this.pending;
    this.fetching = true;
    this.pending = (async () => {
      try {
        const snapshot = await this.request({ action: "snapshot" });
        if (!this.disposed) { this.snapshot = snapshot; this.lastSuccess = Date.now(); this.error = ""; }
      } catch (error) {
        if (!this.disposed) this.error = oneLine(error.message);
      } finally {
        this.fetching = false;
        if (!this.disposed) this.repaint();
      }
    })().finally(() => { this.pending = undefined; });
    return this.pending;
  }
  start() {
    if (this.disposed || this.timer) return;
    void this.refresh();
    this.timer = setInterval(() => void this.refresh(), 1000);
    this.timer.unref?.();
  }
  lines(rows) { return panelLines(this.snapshot, { ...this, rows }); }
  dispose() { this.disposed = true; clearInterval(this.timer); }
}

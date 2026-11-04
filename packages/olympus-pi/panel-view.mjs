// The embedded workstation uses the same Dashboard renderer as the standalone
// terminal and the GitHub screenshots. Only its available height is different.
import { Dashboard, clean } from "./view.mjs";

export const oneLine = value => clean(value).replace(/\s+/g, " ").trim();
export const taskLabel = task => `${task.id} [${task.state}] ${oneLine(task.title)}`;
export const panelTabs = ["live", "projects", "crew", "queue", "memory", "health", "history", "today", "week", "recurring"];
export const panelHelp = "Olympus UI is embedded above your typing area in this same Pi terminal. /olympus-panel on shows it; /olympus-panel live|projects|crew|queue|memory|health|history|today|week|recurring changes its view. Alt+1 through Alt+9 and Alt+0 switch dashboard tabs without replacing your typed text. /olympus-focus selects a task/project for the conversation; /olympus-focus clear resets it. Alt+o or /olympus opens full interactive details; Escape/q returns to chat. These are user UI commands, not olympus_control actions. Do not send the user to a separate window to find this dashboard. GitHub screenshots show synthetic sample data; this panel shows the user's real saved work.";

export function panelColor(line, index, length) {
  const text = line.trim();
  if (index === 0) return "accent";
  if (text.startsWith("STALE")) return "error";
  if (/RECOVERING|BLOCKED|NEXT ACTION|\bACTION\b|\[unknown\]/.test(text)) return "warning";
  if (/^(LAST |IN PROGRESS|PROJECT |ROLE |KNOWLEDGE |PROFILE |TODAY |THIS WEEK |RECURRING |RESPONSES |TASKS |PROJECTS )/.test(text)) return "accent";
  if (/^WORK|^RESULT|\[WORK\]|\[o\.\.|\.o\.|\.\.o\]|\baccepted\b/.test(text)) return "success";
  return index >= length - 2 ? "dim" : "muted";
}
export const panelHeight = rows => Math.max(6, Math.min(32, Math.floor(rows * 0.65)));

export function panelLines(snapshot, { tab = "live", focus, error = "", lastSuccess = 0, now = Date.now(), rows = 40, columns = 144, dashboard } = {}) {
  const view = dashboard || new Dashboard(() => Promise.resolve(snapshot));
  view.snapshot = snapshot;
  view.lastSuccess = lastSuccess;
  view.error = error;
  view.tab = Math.max(0, panelTabs.indexOf(tab));
  // Rendering is read-only. Interactive mutations remain in the full view,
  // which explicitly takes keyboard focus; ordinary typing is never captured.
  const height = panelHeight(rows);
  const full = view.render(columns, Math.max(12, height), now);
  const lines = full.slice(0, height - 2);
  if (focus) lines[0] = lines[0].trimEnd() + ` | Focus: ${focus.id}`;
  lines.push("Alt+1..9/0 tabs | Alt+o full details | /olympus-focus select work | Type below to talk to the chief");
  lines.push(snapshot?.settings?.some(row => row.key === "mode" && row.value === "simulator") ? "Synthetic fixtures. No model calls. No live reliability claims." : "Live saved workspace | /olympus-panel off hides this dashboard | Cyan sections / Amber attention / Red disconnected");
  return lines;
}

export class PanelState {
  constructor(request, repaint = () => {}) {
    this.request = request;
    this.repaint = repaint;
    this.dashboard = new Dashboard(request);
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
    if (this.pending) return this.pending;
    this.fetching = true;
    this.pending = (async () => {
      try {
        const snapshot = await this.request({ action: "snapshot" });
        const extra = {};
        if (this.tab === "memory") extra.knowledge = await this.request({ action: "memory.search", query: "" });
        if (this.tab === "health") {
          extra.profiles = await this.request({ action: "profiles.list" });
          extra.automation = await this.request({ action: "automation.status" });
          extra.backups = await this.request({ action: "backups.list" }).catch(() => []);
        }
        if (this.tab === "history") extra.history = await this.request({ action: "history", kind: "responses", limit: 20 });
        if (this.tab === "today" || this.tab === "week") extra.agenda = await this.request({ action: "agenda", view: this.tab === "today" ? "today" : "week" });
        if (this.tab === "recurring") extra.schedules = await this.request({ action: "schedules.list" });
        if (!this.disposed) {
          this.snapshot = snapshot;
          Object.assign(this.dashboard, extra);
          this.lastSuccess = Date.now(); this.error = "";
        }
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
    this.motion = setInterval(() => { if (!this.disposed) this.repaint(); }, 250);
    this.timer.unref?.(); this.motion.unref?.();
  }
  lines(rows, columns) { return panelLines(this.snapshot, { ...this, rows, columns }); }
  dispose() { this.disposed = true; clearInterval(this.timer); clearInterval(this.motion); }
}

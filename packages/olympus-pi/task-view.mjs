// Decision-first presentation only. Native task acceptance, runtime identity and
// operator controls remain in the coordinator; chief assessments cannot change them.
import { clean, clip, width } from "./view.mjs";
export const textOf = content => typeof content === "string" ? content : (content || []).filter(c => c.type === "text").map(c => c.text).join("\n");
export const compactHeight = rows => Math.max(6, Math.min(14, Math.floor(rows * .35)));
const plain = value => clean(value).replace(/\s+/g, " ").trim();
export function decisionDigest(value) {
  const text = clean(String(value ?? "").slice(0, 6000)).trim();
  if (!text) return "";
  try {
    const data = JSON.parse(text);
    for (const key of ["decision", "summary", "result"]) if (typeof data?.[key] === "string") return clip(plain(data[key]), 180);
    return "Result available in details";
  } catch { /* ordinary narrative, not an API payload */ }
  if (text.startsWith("{") || text.startsWith("[")) return "Result available in details";
  let code = false;
  const lines = text.split("\n").filter(line => {
    if (line.trim().startsWith("```")) { code = !code; return false; }
    return !code && line.trim() && !/^\s*(#|\$|olympus_control|request_id|\{|\[)/.test(line);
  });
  const directive = lines.find(line => /^(decision|next action|recommendation|result):/i.test(line.trim().replace(/\*\*/g, "")));
  return clip(plain(directive || lines[0] || "Result available in details").replace(/\*\*|`/g, "").replace(/^[-*]\s+/, "").replace(/^Captain,\s*/i, ""), 180);
}
export function taskStatus(task, incidents = []) {
  if (task.state === "accepted") return "Completed";
  if (task.state === "blocked" && incidents.some(i => i.task === task.id && i.owner === "operator" && i.state !== "resolved")) return "Awaiting feedback";
  return ({ ready: "Queued", running: "In progress", review: "In review", blocked: "Blocked", unknown: "Unknown - verify", paused: "Paused", recovering: "Recovering", cancelled: "Cancelled" })[task.state] || task.state || "Unknown";
}
const rank = task => ({ blocked: 0, unknown: 1, recovering: 2, running: 3, review: 4, ready: 5, paused: 6, accepted: 7 }[task.state] ?? 8);
export function workbookTasks(snapshot, focus) {
  const seen = new Set();
  return [...(snapshot?.tasks || []), ...(snapshot?.completed || [])]
    .filter(t => t.state !== "cancelled" && !seen.has(t.id) && seen.add(t.id) && (focus?.kind !== "project" || t.project === focus.id))
    .sort((a, b) => {
      if (a.id === b.id) return 0;
      if (a.id === focus?.id) return -1;
      if (b.id === focus?.id) return 1;
      const priority = t => riskSignal(t, snapshot?.incidents).weight === 3 ? -2 : rank(t);
      return priority(a) - priority(b);
    });
}
export function riskSignal(task, incidents = [], stale = false) {
  const open = incidents.filter(i => i.task === task.id && i.state !== "resolved");
  const conflict = open.find(i => ["runtime_generation", "runtime_stop_conflict", "uncertain_effect"].includes(i.kind));
  if (conflict) return { label: "HIGH", color: "error", basis: "Runtime identity/effect needs reconciliation", weight: 3 };
  if (stale || task.state === "unknown") return { label: "UNVERIFIED", color: "warning", basis: "Runtime observations are not verified", weight: 2 };
  if (open.length || ["blocked", "recovering"].includes(task.state)) return { label: "ATTENTION", color: "warning", basis: task.state === "recovering" ? "Recovery needs verification" : "Unresolved blocker or user action", weight: 2 };
  return { label: "UNRATED", color: "dim", basis: "No risk assessment provided", weight: 0 };
}
function assessedRisk(native, brief) {
  if (!brief || !brief.riskBasis) return native;
  const weight = { unknown: 0, low: 1, medium: 2, high: 3 }[brief.risk] ?? 0;
  if (weight === 0 && native.weight === 0) return { ...native, basis: "Chief assessment: " + plain(brief.riskBasis) };
  if (native.weight >= weight) return native;
  return { label: brief.risk.toUpperCase() + "*", color: weight === 3 ? "error" : weight === 2 ? "warning" : "success", basis: "Chief assessment: " + plain(brief.riskBasis), weight };
}
function nextAction(task, incidents) {
  const open = incidents.filter(i => i.task === task.id && i.state !== "resolved");
  if (open.some(i => ["runtime_generation", "runtime_stop_conflict", "uncertain_effect"].includes(i.kind))) return "Verify ownership and reconcile runtime effects";
  if (task.state === "unknown") return "Verify the recorded session before stopping or replacing it";
  const human = open.find(i => i.owner === "operator");
  if (human) return decisionDigest(human.reason) || "Review the requested operator decision";
  return ({ blocked: "Resolve the blocker", recovering: "Verify recovery and preserved work", review: "Wait for independent review", ready: "No worker assigned; check dispatch", paused: "Resume only when its waiting condition clears", accepted: "Review the accepted result", running: "No action required; follow progress" })[task.state] || "Inspect task details";
}
const part = (text, color = "muted", bold = false, bg) => ({ text, color, bold, bg });
const padded = (text, columns) => {
  const normalized = plain(text);
  const value = width(normalized) > columns && columns > 1 ? clip(normalized, columns - 1) + "…" : clip(normalized, columns);
  return value + " ".repeat(Math.max(0, columns - width(value)));
};
const spin = now => ["◐", "◓", "◑", "◒"][Math.floor(now / 250) % 4];
export function workbookRows(snapshot, { record, focus, reply, brief, error = "", lastSuccess = 0, now = Date.now(), rows = 40, columns = 144, reducedMotion = false } = {}) {
  const height = compactHeight(rows), stale = Boolean(error) || !lastSuccess || now - lastSuccess > 5000;
  const tasks = workbookTasks(snapshot, focus), ids = new Set(tasks.map(t => t.id));
  if (focus?.kind === "task" && brief?.task !== focus.id) brief = undefined;
  if (focus?.kind === "project" && brief?.task && !ids.has(brief.task)) brief = undefined;
  const incidents = (snapshot?.incidents || []).filter(i => ids.has(i.task) && i.state !== "resolved");
  const attention = tasks.find(t => incidents.some(i => i.task === t.id && i.owner === "operator") || t.state === "unknown" || riskSignal(t, incidents).weight === 3);
  const localActive = reply?.active === true;
  const active = !stale && (snapshot?.counts?.fresh_active || 0) > 0;
  const pulse = (localActive || active) && !reducedMotion ? spin(now) : localActive || active ? "●" : "○";
  const sample = snapshot?.settings?.some(s => s.key === "mode" && s.value === "simulator");
  const output = [[part("OLYMPUS / Workbook", "accent", true, "selectedBg"), part(sample ? "  SIMULATOR - SAMPLE DATA" : stale ? "  STALE / DISCONNECTED" : "  LIVE", sample || stale ? "warning" : "success", true)]];
  const needs = new Set(incidents.filter(i => i.owner === "operator").map(i => i.task)).size;
  output.push([part(pulse + (localActive ? " CHIEF ACTIVE" : active ? " FLEET ACTIVE" : " CHIEF IDLE"), localActive ? "accent" : "dim"), part(`  RUN ${tasks.filter(t => t.state === "running").length}`, "accent"), part(`  YOUR ACTION ${needs}`, needs ? "warning" : "dim", Boolean(needs)), part(`  BLOCKED ${tasks.filter(t => t.state === "blocked").length}`, attention ? "warning" : "dim"), part(`  RECENT DONE ${tasks.filter(t => t.state === "accepted").length}`, "success")]);
  const suggested = !attention && brief?.decision && !/^none\b/i.test(brief.decision);
  const move = attention ? nextAction(attention, incidents) : suggested ? plain(brief.decision) : "No operator decision currently identified";
  const attentionMark = attention && !stale && !reducedMotion && Math.floor(now / 500) % 2 === 0 ? "!" : " ";
  output.push([part(attention ? `YOUR MOVE${attentionMark} ` : suggested ? "CHIEF VIEW " : "NEXT MOVE  ", attention ? "warning" : "accent", true), part(clip(move, Math.max(0, columns - 11)), attention ? "warning" : "muted", true)]);
  const display = tasks.map(task => {
    const exact = record?.task?.id === task.id ? record : undefined;
    const attempt = exact?.attempts?.find(a => a.generation === task.generation);
    const artifacts = (exact?.artifacts || []).filter(a => (a.version === undefined || a.version === task.version) && (!attempt || a.attempt === undefined || a.attempt === attempt.id));
    const assessment = brief?.task === task.id ? brief : undefined;
    const artifact = artifacts.at(-1);
    const response = !exact && (snapshot?.responses || []).find(r => r.task === task.id);
    const result = decisionDigest(artifact?.body || response?.body);
    const state = taskStatus(task, incidents);
    const live = !stale && task.state === "running" && typeof task.heartbeat === "number" && task.heartbeat >= now / 1000 - 30;
    const label = { Completed: "DONE", "In progress": "RUNNING", "Awaiting feedback": "NEEDS YOU", "Unknown - verify": "VERIFY", "In review": "REVIEW", Queued: "QUEUED", Blocked: "BLOCKED", Recovering: "RECOVER", Paused: "WAITING" }[state] || state;
    return { id: task.id, title: plain(task.title), status: label, statusColor: state === "Completed" ? "success" : state === "In progress" ? "accent" : ["Awaiting feedback", "Blocked", "Unknown - verify"].includes(state) ? "warning" : "muted", risk: assessedRisk(riskSignal(task, incidents, stale), assessment), outcome: (assessment?.nextAction ? "Chief: " + assessment.nextAction : "") || (result ? (task.state === "accepted" ? "Result: " : "Candidate: ") + result : nextAction(task, incidents)), icon: task.id === focus?.id ? ">" : live && !reducedMotion ? spin(now) : task.state === "accepted" ? "✓" : task.state === "blocked" ? "!" : task.state === "unknown" ? "?" : "·" };
  });
  if (reply?.prompt && focus?.kind !== "task") display.unshift({ id: "reply", title: decisionDigest(reply.prompt), status: localActive ? "Reply running" : reply.state === "Reply failed" ? "Reply failed" : reply.state === "Stopped" ? "Stopped" : "Reply ready", statusColor: reply.state === "Reply failed" ? "error" : localActive ? "accent" : "muted", risk: assessedRisk({ label: "UNRATED", color: "dim", basis: "Chief risk not assessed", weight: 0 }, brief?.task ? undefined : brief), outcome: localActive ? "Working; routine logs hidden" : brief?.nextAction && !/^none\b|^no action\b/i.test(brief.nextAction) ? plain(brief.nextAction) : brief?.headline || decisionDigest(reply.output) || "No substantive reply yet", icon: localActive && !reducedMotion ? spin(now) : "·" });
  const wide = columns >= 100;
  const statusWidth = wide ? 18 : 13, riskWidth = wide ? 12 : 0;
  const titleWidth = Math.max(8, Math.floor(columns * (wide ? .28 : .34)));
  const outcomeWidth = Math.max(0, columns - titleWidth - statusWidth - riskWidth - (wide ? 9 : 6));
  const cellRow = (title, status, risk, outcome, colors = {}, selected = false) => {
    const cells = [part(padded(title, titleWidth), colors.title || "muted", selected), part(" │ ", "dim"), part(padded(status, statusWidth), colors.status || "muted", true)];
    if (wide) cells.push(part(" │ ", "dim"), part(padded(risk, riskWidth), colors.risk || "dim", true));
    cells.push(part(" │ ", "dim"), part(padded(outcome, outcomeWidth), colors.outcome || "muted"));
    return cells.map(cell => selected ? { ...cell, bg: "selectedBg" } : cell);
  };
  output.push(cellRow("TASK / REQUEST", "STATUS", "RISK SIGNAL", "DECISION / RESULT / NEXT ACTION", { title: "accent", status: "accent", risk: "accent", outcome: "accent" }));
  if (height >= 9) output.push([part("─".repeat(Math.max(0, columns)), "dim")]);
  const room = Math.max(1, height - (height < 9 ? 5 : 7));
  for (const row of display.slice(0, room)) output.push(cellRow(row.icon + " " + row.title, row.status, row.risk.label, row.outcome, { title: "text", status: row.statusColor, risk: row.risk.color }, row.id === focus?.id));
  if (!display.length) output.push([part("No tasks or decisions yet. Type a request below.", "muted")]);
  if (output.length < height - 2 && display[0]) output.push([part("RISK BASIS  ", "dim"), part(clip(display[0].risk.basis, Math.max(0, columns - 12)), display[0].risk.color)]);
  const unlisted = (snapshot?.incidents || []).filter(i => !ids.has(i.task) && i.state !== "resolved").length;
  const footer = `${Math.min(display.length, room)}/${display.length} shown${unlisted ? `  +${unlisted} other alerts in queue` : ""}  /olympus-focus select  Alt+o details`;
  output.push([part(clip(footer, columns), "dim")]);
  if (output.length < height) output.push([part("Risk* = chief assessment, not verified acceptance. Full logs remain in the transcript/details.", "dim")]);
  return output.slice(0, height);
}
export function taskLines(snapshot, options) { return workbookRows(snapshot, options).map(row => clip(row.map(segment => segment.text).join(""), options?.columns ?? 144)); }
export function coordinatorResultSummary(data) {
  if (Array.isArray(data)) return `${data.length} records loaded`;
  if (data?.task && typeof data.task === "object") return `${plain(data.task.title)} - ${taskStatus(data.task, data.incidents)}`;
  if (data?.state === "pending") return "Runtime action pending - not confirmed";
  if (data?.state === "confirmed") return "Runtime effect confirmed - not task acceptance";
  if (data?.state === "refused" || data?.state === "uncertain") return `Runtime effect ${data.state}; reconciliation required`;
  if (data?.state === "cancelled") return data.work_preserved === true ? "Task cancelled; work preserved" : "Cancellation recorded; inspect details";
  if (data?.state === "review") return "Candidate result submitted for independent review";
  if (data?.configured === true) return "Configuration recorded";
  if (data?.task && typeof data.task === "string") return "Task recorded; inspect its status for progress";
  return "Coordinator response received; expand for details";
}
export const workbookGuidelines = "Finish substantive user turns with olympus_summary: a concise outcome headline, the decision the user actually needs to make (or 'None'), the next action, and risk with a concrete basis. This is a labelled chief assessment, not proof of task acceptance or runtime liveness. Do not copy raw tool logs, JSON payloads, paths or routine working notes into the brief. Never invent progress percentages, severity, completion or a required user decision.";

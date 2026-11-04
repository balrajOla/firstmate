// Compact prompt/output/status projection. A finished chat reply is deliberately
// distinct from independently accepted task work.
import { clean, wrap, clip } from "./view.mjs";
export const textOf = content => typeof content === "string" ? content : (content || []).filter(c => c.type === "text").map(c => c.text).join("\n");
export const compactHeight = rows => Math.max(6, Math.min(14, Math.floor(rows * .35)));
export function taskStatus(task, incidents = []) {
  if (task.state === "accepted") return "Completed";
  if (task.state === "blocked" && incidents.some(i => i.task === task.id && i.owner === "operator" && i.state !== "resolved" && /decision|feedback|approval|human|choose|confirm/i.test(`${i.kind || ""} ${i.reason || ""}`))) return "Awaiting feedback";
  return ({ ready: "Queued", running: "In progress", review: "In review", blocked: "Blocked", unknown: "Unknown - verify", paused: "Paused", recovering: "Recovering", cancelled: "Cancelled" })[task.state] || task.state || "Unknown";
}
export function taskLines(snapshot, { record, focus, reply, error = "", lastSuccess = 0, now = Date.now(), rows = 40, columns = 144 } = {}) {
  const height = compactHeight(rows), tasks = [...(snapshot?.tasks || []), ...(snapshot?.completed || [])].filter(t => focus?.kind !== "project" || t.project === focus.id);
  const allowedRecord = focus?.kind !== "project" || record?.task?.project === focus.id ? record : undefined;
  const chosen = tasks.find(t => t.id === focus?.id) || allowedRecord?.task || tasks[0];
  const exact = record?.task?.id === chosen?.id ? record : undefined;
  const stale = error || !lastSuccess || now - lastSuccess > 5000;
  const lines = ["OLYMPUS / Prompt · Output · Status"];
  const counts = snapshot?.counts || {};
  lines.push(stale ? "STALE / DISCONNECTED - saved observations only" : `${counts.working || 0} running · ${counts.blocked || 0} blocked · ${counts.needs_you || 0} need feedback`);
  const showReply = reply?.prompt && focus?.kind !== "task";
  if (showReply) {
    lines.push(`PI REPLY [${reply.state}]${reply.command ? ` / ${reply.command}` : ""}`);
    lines.push(...wrap("PROMPT " + clean(reply.prompt), columns).slice(0, 2));
    lines.push(...wrap("OUTPUT " + clean(reply.output || "Waiting for output..."), columns).slice(0, 3));
  } else if (chosen) {
    const task = exact?.task || chosen;
    const currentAttempt = exact?.attempts?.find(a => a.generation === task.generation);
    const artifacts = (exact?.artifacts || []).filter(a => (a.version === undefined || a.version === task.version) && (a.attempt === undefined || !currentAttempt || a.attempt === currentAttempt.id));
    const artifact = artifacts.at(-1);
    const response = !exact && (snapshot?.responses || []).find(r => r.task === task.id);
    lines.push(`TASK ${task.id} [${taskStatus(task, snapshot?.incidents)}] ${clean(task.title)}`);
    lines.push(...wrap("PROMPT " + clean(task.requirements || task.title), columns).slice(0, 2));
    lines.push(...wrap(`${task.state === "accepted" ? "OUTPUT" : "CANDIDATE OUTPUT"} ` + clean(artifact?.body || response?.body || "No output yet"), columns).slice(0, 3));
    if (reply?.output) lines.push("PI REPLY " + clean(reply.output).replace(/\s+/g, " "));
  } else lines.push("No saved task selected. Type your request below.");
  if (tasks.length && lines.length < height - 2) {
    lines.push("TASKS / choose with /olympus-focus");
    lines.push(...tasks.slice(0, 3).map(t => `${t.id} [${taskStatus(t, snapshot?.incidents)}] ${clean(t.title)}`));
  }
  const result = lines.slice(0, height - (height < 9 ? 1 : 2));
  result.push("/olympus-focus: select task · /olympus-panel live: full layout · /olympus-panel off");
  if (height >= 9) result.push("Alt+o: scroll full dashboard/detail · Pi transcript and typing remain below");
  return result.map(line => clip(line, columns));
}

// Shared by real Pi and the standalone terminal; no model calls or durable animation events.
export function clean(value) {
  return String(value ?? "").replace(/\x1b\][^\x07]*(?:\x07|\x1b\\)/g, "").replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, "").replace(/[\x00-\x08\x0b-\x1f\x7f-\x9f]/g, "");
}
const segmenter = new Intl.Segmenter(undefined, { granularity: "grapheme" });
const cells = (s) => /[\p{Extended_Pictographic}\u1100-\u115f\u2e80-\ua4cf\uac00-\ud7a3\uf900-\ufaff\uff01-\uff60]/u.test(s) ? 2 : 1;
export function width(text) { return [...segmenter.segment(clean(text))].reduce((n, s) => n + cells(s.segment), 0); }
export function clip(text, columns) {
  let out = "", n = 0;
  for (const { segment } of segmenter.segment(clean(text).replace(/\n/g, " "))) { const w = cells(segment); if (n + w > columns) break; out += segment; n += w; }
  return out;
}
const pad = (s, n) => { const c = clip(s, n); return c + " ".repeat(Math.max(0, n - width(c))); };
export function wrap(text, columns) {
  const lines = [];
  for (const raw of clean(text).split("\n")) {
    let line = "", n = 0;
    for (const { segment } of segmenter.segment(raw)) { const w = cells(segment); if (n + w > columns && line) { lines.push(line); line = ""; n = 0; } line += segment; n += w; }
    lines.push(line);
  }
  return lines;
}
const tabs = ["Live", "Projects", "Crew", "Queue", "Memory", "Health", "History", "Today", "Week", "Recurring"];
const badge = (s) => ({ running: "WORK", recovering: "REPAIR", blocked: "BLOCK", unknown: "UNKNOWN", ready: "QUEUED", review: "REVIEW", accepted: "DONE", paused: "PAUSED" }[s] || s);
const age = (time, now) => time ? `${Math.max(0, now - time)}s ago` : "unobserved";

export class Dashboard {
  constructor(request, { reducedMotion = false, repaint = () => {}, close = () => {} } = {}) {
    this.request = request; this.reducedMotion = reducedMotion; this.repaint = repaint; this.close = close;
    this.tab = 0; this.pane = 0; this.selected = 0; this.offset = 0; this.snapshot = null; this.lastSuccess = 0; this.error = "";
    this.projectFilter = ""; this.agenda = {jobs: []}; this.schedules = []; this.automation = []; this.backups = [];
    this.history = []; this.historyKind = "responses"; this.historyAnchor = null; this.detail = null; this.form = null; this.input = ""; this.notice = ""; this.newCount = 0; this.filter = "";
  }
  async start() { await this.refresh(); this.poll = setInterval(() => void this.refresh(), 1000); this.motion = setInterval(() => this.repaint(), 250); }
  dispose() { clearInterval(this.poll); clearInterval(this.motion); }
  invalidate() {}
  async refresh() {
    if (this.fetching) return; this.fetching = true;
    const identity = this.items()[this.selected]?.id;
    try {
      const next = await this.request({ action: "snapshot" });
      if (this.tab === 6 && this.snapshot?.responses?.[0]) this.newCount += Math.max(0, (next.responses[0]?.seq || 0) - this.snapshot.responses[0].seq);
      this.snapshot = next;
      if (this.tab === 7 || this.tab === 8) this.agenda = await this.request({action: "agenda", view: this.tab === 7 ? "today" : "week", ...(this.projectFilter ? {project: this.projectFilter} : {})});
      if (this.tab === 9) this.schedules = await this.request({action: "schedules.list", ...(this.projectFilter ? {project: this.projectFilter} : {})});
      if (this.tab === 5) { this.automation = await this.request({action: "automation.status"}); this.backups = await this.request({action: "backups.list"}).catch(() => []); }
      this.lastSuccess = Date.now(); this.error = "";
      if (identity && this.tab !== 6) { const at = this.items().findIndex((r) => r.id === identity); if (at >= 0) this.selected = at; }
      this.selected = Math.min(this.selected, Math.max(0, this.items().length - 1));
    } catch (e) { this.error = clean(e.message); }
    finally { this.fetching = false; this.repaint(); }
  }
  items() {
    const s = this.snapshot; if (!s) return [];
    let list = this.tab === 0 ? [s.tasks, s.responses, this.historyKind === "projects" ? s.projects.filter((p) => p.state === "completed") : s.completed][this.pane] :
      this.tab === 1 ? s.projects : this.tab === 2 ? s.agents : this.tab === 3 ? s.incidents : this.tab === 4 ? this.knowledge || [] : this.tab === 5 ? [...(this.profiles || []), ...this.automation, ...this.backups] : this.tab === 7 || this.tab === 8 ? this.agenda.jobs || [] : this.tab === 9 ? this.schedules : this.history;
    if (this.projectFilter && this.tab !== 5) list = list.filter(r => r.project === this.projectFilter || r.id === this.projectFilter);
    if (this.filter) list = list.filter((r) => JSON.stringify(r).toLowerCase().includes(this.filter.toLowerCase()));
    return list;
  }
  async setTab(tab) {
    this.tab = tab; this.selected = 0; this.offset = 0; this.detail = null; this.filter = "";
    if (tab === 4) this.knowledge = await this.request({ action: "memory.search", query: "" });
    if (tab === 5) this.profiles = await this.request({ action: "profiles.list" });
    if (tab === 6) await this.loadHistory(true);
    if (tab >= 7 || tab === 5) await this.refresh();
    this.repaint();
  }
  async loadHistory(reset = false) {
    const oldLength = this.history.length;
    const before = reset ? undefined : this.history.at(-1)?.cursor;
    const page = await this.request({ action: "history", kind: this.historyKind, limit: 20, ...(before ? { before } : {}), search: this.filter, ...(this.projectFilter ? {project: this.projectFilter} : {}) });
    this.history = reset ? page : [...this.history, ...page.filter((x) => !this.history.some((y) => y.id === x.id))];
    if (reset) { this.historyAnchor = page[0]?.cursor; this.newCount = 0; this.selected = 0; }
    else if (page.length) this.selected = oldLength;
  }
  async open() {
    const item = this.items()[this.selected]; if (!item) return;
    if (this.tab === 0 && this.pane === 1 || this.tab === 6 && this.historyKind === "responses") this.detail = await this.request({ action: "response.get", response: item.id });
    else if (this.tab === 1 || this.tab === 6 && this.historyKind === "projects") this.detail = await this.request({ action: "project.get", project: item.id });
    else if ((this.tab === 0 && this.pane !== 1 && item.requirements) || this.tab === 6 && this.historyKind === "tasks" || this.tab === 7 || this.tab === 8) this.detail = await this.request({ action: "task.get", task: item.id });
    else if (this.tab === 3 && item.task) this.detail = await this.request({ action: "task.get", task: item.task });
    else this.detail = item;
    this.offset = 0;
  }
  begin(title, fields, submit) { this.form = { title, fields, values: {}, index: 0, submit }; this.input = ""; }
  async submitForm() {
    const form = this.form; const field = form.fields[form.index];
    if (!this.input.trim()) { this.notice = "A value is required. Escape cancels."; return; }
    form.values[field.key] = this.input.trim(); this.input = ""; form.index++;
    if (form.index < form.fields.length) return;
    this.form = null; await form.submit(form.values); await this.refresh();
  }
  newProject() {
    this.begin("NEW PROJECT", [{ key: "name", label: "Project name" }, { key: "charter", label: "Outcome, acceptance and authority" }], async (v) => {
      const result = await this.request({ action: "project.create", ...v }); this.notice = `Created ${v.name} (${result.project}). Add a task with n.`;
    });
  }
  newTask() {
    const project = this.tab === 1 ? this.items()[this.selected]?.id : this.items()[this.selected]?.project;
    this.begin("NEW TASK", [...(!project ? [{ key: "project", label: "Exact project ID (from Projects)" }] : []), { key: "title", label: "Task title" }, { key: "requirements", label: "Requirements and acceptance" }], async (v) => {
      const result = await this.request({ action: "task.create", role: "engineer", ...v, ...(project ? { project } : {}) }); this.notice = `Task ${result.task} saved and queued.`;
    });
  }
  async key(data) {
    try {
      if (this.form) {
        if (data === "\x1b") { this.form = null; this.input = ""; }
        else if (data === "\r" || data === "\n") await this.submitForm();
        else if (data === "\x7f" || data === "\b") this.input = [...this.input].slice(0, -1).join("");
        else if (!data.startsWith("\x1b") && data !== "\x03") this.input += clean(data).slice(0, 16000 - this.input.length);
        this.repaint(); return;
      }
      if (this.detail) {
        if (["\x1b", "q", "\b", "\x7f"].includes(data)) { this.detail = null; this.offset = 0; }
        else if (["j", "\x1b[B", "\x1b[6~", " "].includes(data)) this.offset += data === "j" || data === "\x1b[B" ? 1 : 12;
        else if (["k", "\x1b[A", "\x1b[5~"].includes(data)) this.offset = Math.max(0, this.offset - (data === "k" || data === "\x1b[A" ? 1 : 12));
        else if (data === "\x1b[H") this.offset = 0;
        this.repaint(); return;
      }
      if (data === "q" || data === "\x03" || data === "\x1b") { this.dispose(); this.close(); return; }
      if (/^[0-9]$/.test(data)) await this.setTab(data === "0" ? 9 : Number(data) - 1);
      else if (data === "\t") { this.pane = (this.pane + 1) % 3; this.selected = 0; }
      else if (data === "j" || data === "\x1b[B") this.selected = Math.min(this.items().length - 1, this.selected + 1);
      else if (data === "k" || data === "\x1b[A") this.selected = Math.max(0, this.selected - 1);
      else if (data === "\r" || data === "\n") await this.open();
      else if (data === "\x1b[5~") { if (this.tab !== 6) await this.setTab(6); else await this.loadHistory(); }
      else if (data === "\x1b[6~") this.selected = Math.min(this.items().length - 1, this.selected + 12);
      else if (data === "\x1b[F" || data === "\x1b[4~") { await this.setTab(0); this.newCount = 0; }
      else if (data === "t") { this.historyKind = this.historyKind === "tasks" ? "projects" : this.historyKind === "projects" ? "responses" : "tasks"; if (this.tab === 6) await this.loadHistory(true); }
      else if (data === "m") this.reducedMotion = !this.reducedMotion;
      else if (data === "N") this.newProject();
      else if (data === "n") this.newTask();
      else if (data === "/") this.begin("SEARCH", [{ key: "query", label: "Search retained records" }], async ({ query }) => { this.filter = query; this.selected = 0; if (this.tab === 6) await this.loadHistory(true); });
      else if (data === ":") this.begin("DESCRIBE WORK", [{ key: "text", label: "Your request" }], async ({ text }) => {
        const preview = await this.request({action: "intake.preview", text, ...(this.projectFilter ? {project: this.projectFilter} : {})});
        if (["existing_project", "new_project"].includes(preview.route)) { const out = await this.request({action: "intake.submit", text, ...(preview.route === "existing_project" ? {project: preview.candidates[0].id} : {})}); this.notice = `${out.project_created ? "New project created; connect it to enable execution" : out.duplicate ? "Existing request" : "Queued with retained project context"}: ${out.task}`; }
        else { this.notice = (preview.candidates || []).map(p => `${p.name}: ${p.id}`).join(" | ") || "No confident match. N creates a new project."; this.begin("CHOOSE PROJECT", [{key: "project", label: "Project ID from candidates or Projects"}], async ({project}) => { const out = await this.request({action: "intake.submit", text, project}); this.notice = `Queued ${out.task}`; }); }
      });
      else if (data === "f") { const item = this.items()[this.selected]; this.projectFilter = this.projectFilter ? "" : this.tab === 1 ? item?.id || "" : item?.project || ""; if(this.tab === 6) await this.loadHistory(true); await this.refresh(); this.notice = this.projectFilter ? `Project filter: ${this.projectFilter}. f clears.` : "All projects"; }
      else if (data === "o") { const item = this.items()[this.selected]; const project = this.tab === 1 ? item?.id : item?.project || this.projectFilter; if(!project) throw new Error("Select a project or task first"); const report = await this.request({action: "project.outcomes", project}); this.detail = {body: `${report.project.name} / PROJECT OUTCOMES\n\n${report.counts.map(c => `${c.count} ${c.state}`).join(" | ")}\n\nBLOCKERS\n${report.blockers.map(b => b.reason).join("\n") || "None"}\n\nRESPONSES (latest page; project-filtered History retains older pages)\n${report.outputs.map(o => `[${o.task_state}] ${o.title}\n${o.body}`).join("\n\n")}\n\nNEXT ACTIONS\n${report.next_actions.map(t => `${t.state}: ${t.title}`).join("\n")}`}; this.offset = 0; }
      else if (data === "l") { const item = this.items()[this.selected]; const project = this.tab === 1 ? item?.id : item?.project || this.projectFilter; if(!project) throw new Error("Select a project or task first"); const report = await this.request({action: "learning.status", project}); this.detail = {body: `LEARNING / ${project}\n\n${report.policy}\n\nREVIEWED OUTCOMES\n${report.outcomes.map(o => `${o.profile} / ${o.role} / ${o.complexity}: ${o.outcome} (${o.failures} failures)`).join("\n") || "No bound profile outcomes yet"}\n\nRETAINED EVIDENCE\n${report.lessons.map(l => `[${l.kind}] ${l.task}\n${l.evidence}\nSource: ${l.source}`).join("\n\n") || "No recorded lessons yet"}`}; this.offset = 0; }
      else if (data === "i") { const item = this.items()[this.selected]; if(this.tab !== 2 || !item) throw new Error("Select an agent in Crew first"); const messages = await this.request({action: "message.inbox", recipient: item.id}); this.detail = {body: `MAILBOX / ${item.role} / ${item.id}\n\n${messages.map(m => `[${m.state}] ${m.thread} / from ${m.sender}\n${m.body}${m.projection_truncated ? "\nFull message available through message.get" : ""}`).join("\n\n") || "No pending messages"}`}; this.offset=0; }
      else if (data === "s") { const item=this.items()[this.selected]; const project=this.tab===1 ? item?.id : item?.project || this.projectFilter; this.begin("RECURRING JOB", [...(!project ? [{key:"project",label:"Project ID"}] : []), {key:"title",label:"Job title"},{key:"requirements",label:"Requirements and acceptance"},{key:"cadence",label:"daily / weekdays / weekly"},{key:"time",label:"Local time HH:MM"},{key:"weekday",label:"Monday=0 .. Sunday=6 (weekly only; otherwise 0)"}], async v => { const out=await this.request({action:"schedule.create",...v,...(project ? {project} : {}),weekday:Number(v.weekday)});this.notice=`Scheduled ${out.schedule} in ${out.timezone}`; }); }
      else if (data === "d") { const item=this.items()[this.selected]; if(!item?.requirements || this.tab===9) throw new Error("Select a task first"); this.begin("SET DUE DATE",[{key:"date",label:"ISO date and time with timezone (e.g. 2026-11-03T09:00:00+00:00)"}],async ({date})=>{const due=Math.floor(Date.parse(date)/1000);if(!Number.isFinite(due)||!/(Z|[+-]\d\d:\d\d)$/.test(date))throw new Error("A valid date with explicit timezone is required");await this.request({action:"task.due",task:item.id,due});this.notice="Due date saved";}); }
      else if (data === "A") { const item=this.items()[this.selected]; if(this.tab!==1||!item) throw new Error("Select a project first"); const states=await this.request({action:"automation.status"});const enabled=!states.some(p=>p.project===item.id&&p.enabled===1);await this.request({action:"automation.configure",project:item.id,enabled});this.notice=enabled?"Project automation enabled; real dispatch still requires verified profiles and an execution-enabled connection.":"Project automation paused"; }
      else if (["p","r"].includes(data) && this.tab===9) { const item=this.items()[this.selected]; if(item) await this.request({action:"schedule.state",schedule:item.id,state:data==="p"?"paused":"active"});await this.refresh(); }
      else if (["a", "p", "r"].includes(data)) {
        const item = this.items()[this.selected]; if (!item?.requirements) throw new Error("Select a task in the Live view first.");
        if (data === "a") this.begin("AMEND REQUIREMENTS", [{ key: "requirements", label: "Full revised requirements" }], async (v) => { await this.request({ action: "task.amend", task: item.id, expected_version: item.version, ...v }); this.notice = "New version saved. Current owner must acknowledge it at a checkpoint."; });
        else this.begin(data === "p" ? "PAUSE TASK" : "RESUME TASK", [{ key: "reason", label: "Reason and next action" }], async (v) => { const out = await this.request({ action: data === "p" ? "task.pause" : "task.resume", task: item.id, ...v }); this.notice = `Task ${out.state}. Runtime effects must be reconciled separately.`; });
      }
      else if (data === "?") this.detail = { title: "Olympus keyboard guide", body: "1-9 and 0 select views. 8 Today, 9 Week, 0 Recurring. Tab changes Live focus. j/k select; Enter opens full records.\nPgUp loads older response history. t switches responses/tasks/projects.\nEnd returns to live. / searches. : routes a request with retained context; ambiguous matches require a project choice.\nN creates a project; n adds a task; a amends selected task requirements.\np pauses selected work; r resumes after a recorded reason.\nf filters a selected project; o opens outcomes; l opens learning; i opens a Crew mailbox.\ns creates a recurring job; d sets a task due date; A toggles project automation.\nm toggles reduced motion. Escape closes detail; q leaves the view.\n\nUse olympus request with the documented action schema for crew formation, messages, acceptance, context transfer and recovery.\nNo terminal action changes provider credentials or accepts a result without evidence." };
      this.selected = Math.max(0, this.selected); this.repaint();
    } catch (e) { this.notice = clean(e.message); this.repaint(); }
  }
  render(columns = 144, height = 40, time = Date.now()) {
    const w = Math.max(24, columns), h = Math.max(12, height);
    const raw=this.snapshot; const scoped=rows=>(rows||[]).filter(r=>r.project===this.projectFilter || r.id===this.projectFilter);
    const s=raw && this.projectFilter ? {...raw, projects:scoped(raw.projects),tasks:scoped(raw.tasks),incidents:scoped(raw.incidents),responses:scoped(raw.responses),completed:scoped(raw.completed)} : raw;
    const stale = !!this.error || !this.lastSuccess || time - this.lastSuccess > 5000;
    const simulator = s?.settings?.some((x) => x.key === "mode" && x.value === "simulator");
    const c = s?.counts || {}; const now = Math.floor(time / 1000);
    const motion = !stale && c.fresh_active > 0;
    const pulse = motion ? this.reducedMotion ? "[WORK]" : ["[o..]", "[.o.]", "[..o]"][Math.floor(time / 250) % 3] : "[---]";
    const recoveryPulse = !stale && c.recovering > 0 && !this.reducedMotion ? ["[~..]", "[.~.]", "[..~]"][Math.floor(time / 500) % 3] : "[REPAIR]";
    const lines = [pad(`OLYMPUS / ${this.detail ? "Full record" : tabs[this.tab]}${simulator ? "  |  SIMULATOR - SAMPLE DATA" : ""}`, w), pad(tabs.slice(0,7).map((x,i)=>`${i+1} ${i===this.tab?"["+x+"]":x}`).join("  "),w), pad(tabs.slice(7).map((x,i)=>`${(i+8)%10} ${i+7===this.tab?"["+x+"]":x}`).join("  ") + (this.projectFilter?` | Project ${this.projectFilter}`:" | All projects"),w),
      clip(w < 110 ? `${pulse} ${c.working || 0} work ${c.recovering || 0} repair ${c.blocked || 0} blocked ${c.needs_you || 0} need you | Chief: ${s?.chief || "unknown"}` : `${pulse} ${c.working || 0} working  ${c.recovering || 0} recovering  ${c.blocked || 0} blocked  ${c.needs_you || 0} need you  Chief: ${s?.chief || "unknown"}`, w),
      clip(stale ? `STALE / DISCONNECTED: ${this.error || "loading observations"}. Last verified state retained.` : `Verified ${age(s.generated_at, now)} | History retained | ${this.newCount ? "+" + this.newCount + " new responses; End returns live" : "Motion indicates activity; checkpoints establish progress"}`, w), ""];
    const available = h - 10;
    if (this.form) {
      const f = this.form; lines.push(f.title, "", `${f.index + 1}/${f.fields.length}  ${f.fields[f.index].label}`, ...wrap("> " + this.input, w), "", "Enter continues. Escape cancels. Nothing is saved until all fields are complete.");
    } else if (this.detail) {
      const body = this.detail.body || JSON.stringify(this.detail, null, 2); const full = wrap(body, w);
      this.offset = Math.min(this.offset, Math.max(0, full.length - available));
      lines.push(...full.slice(this.offset, this.offset + available));
    } else if (!s) lines.push("Connecting to the durable coordinator...", "Run olympus start if this is your first visit.");
    else if (this.tab === 0) {
      const left = ["IN PROGRESS", ...s.tasks.filter((t) => t.state === "running").slice(0, 5).map((t) => `${pulse} ${t.title} | ${t.role}`), "", "RECOVERING", ...s.tasks.filter((t) => t.state === "recovering").slice(0, 2).map((t) => `${recoveryPulse} ${t.title}`), "", "BLOCKED / WAITING ON ACTION", ...s.incidents.slice(0, 5).map((i, n) => `${n + 1} ${i.project_name || "System"}: ${i.reason}`), "", "NEXT ACTION FOR YOU", ...(s.incidents.filter((i) => i.owner === "operator").slice(0, 2).map((i) => i.reason)), "", "Other eligible work continues within its charter."];
      const right = ["LAST 5 RESPONSES", ...s.responses.map((r, n) => `${this.pane === 1 && this.selected === n ? ">" : " "} ${r.project_name || r.project}: ${r.body.split("\n")[0]}`), "Enter opens the full response. PgUp loads older records.", "", "LAST 10 COMPLETED TASKS / PROJECTS", ...(this.historyKind === "projects" ? [...s.projects].filter(p => p.state === "completed").sort((a,b) => b.completed_seq - a.completed_seq).slice(0,10) : s.completed).map((t, n) => `${this.pane === 2 && this.selected === n ? ">" : " "} ${t.project_name || t.name}: ${t.title || t.state}`), "", "History is retained. New entries never move older scrollback."];
      if (w >= 110) {const lw = Math.floor(w * .48); for (let i = 0; i < Math.min(available, Math.max(left.length, right.length)); i++) lines.push(pad(left[i] || "", lw) + "  " + clip(right[i] || "", w - lw - 2));}
      else lines.push(...(this.pane === 0 ? left : right).slice(0, available));
      if (this.pane === 0 && this.items()[this.selected]) lines.push(clip(`Selected: ${this.items()[this.selected].title} (${this.items()[this.selected].state})`, w));
    } else {
      const items = this.items(); const pageSize = Math.max(3, available - 3); const start = Math.floor(this.selected / pageSize) * pageSize;
      const headings = ["", "PROJECT / STATUS / CHARTER", "ROLE / HARNESS / PARENT / DEPTH", "# / PROJECT / REASON / OWNER / NEXT CHECK", "KNOWLEDGE / STATUS / PROVENANCE", "PROFILE / HARNESS / VERSION / VERIFIED", `${this.historyKind.toUpperCase()} / ${this.history.length} loaded; PgUp loads older; t changes type`, `TODAY / ${this.agenda.timezone || "local"} / due, overdue, active and completed`, `THIS WEEK / ${this.agenda.timezone || "local"}`, "RECURRING / CADENCE / NEXT RUN / LAST OUTCOME"];
      lines.push(headings[this.tab], "");
      if (this.tab === 5) lines.push("Coordinator and agent sessions are independent. Unknown is not confirmed dead.", "Use olympus doctor for installed components; profiles need live evidence.", "");
      for (const [n, item] of items.slice(start, start + pageSize).entries()) {
        const text = this.tab === 1 ? `${item.name}  ${item.state}  ${item.charter}` : this.tab === 2 ? `${"  ".repeat(item.depth)}${item.role}  ${item.harness}  parent=${item.parent || "root"}  ${item.id}` : this.tab === 3 ? `${start + n + 1} ${item.project_name || "System"}  ${item.reason}  owner=${item.owner}  check=${new Date(item.next_check * 1000).toISOString()}` : this.tab === 4 ? `${item.title}  ${item.status}  ${item.sources}` : this.tab === 5 ? item.harness ? `${item.id}  ${item.harness} ${item.version}  ${item.verified ? "VERIFIED" : "UNTESTED"}` : item.destination ? `Backup ${item.state}: ${item.detail}` : `${item.project_name} / ${item.enabled ? "AUTO" : "MANUAL"} / ${item.status}` : this.tab === 7 || this.tab === 8 ? `${item.state === "running" && motion ? pulse : badge(item.state)} ${item.project_name} / ${item.title} / ${item.bucket} / ${item.due ? new Date(item.due*1000).toLocaleString("en-GB",{timeZone:this.agenda.timezone || "Europe/London"}) : "no due date"}` : this.tab === 9 ? `${item.project_name} / ${item.title} / ${item.state} / ${item.cadence} ${item.local_time} ${item.timezone} / next ${new Date(item.next_run*1000).toLocaleString("en-GB",{timeZone:item.timezone})} / last ${item.last_state || "not run"}` : `${item.project_name || item.name || ""}  ${item.body?.split("\n")[0] || item.title || item.charter}`;
        lines.push(clip(`${this.selected === start + n ? ">" : " "} ${text}`, w));
      }
      if (!items.length) lines.push(this.tab === 1 ? "No projects yet. Press N to create your first charter." : "No records in this view.");
    }
    const body = lines.slice(0, h - 3); while (body.length < h - 3) body.push("");
    body.push(clip(this.notice, w), clip(this.detail ? "j/k scroll   PgUp/PgDn page   Esc back   live status stays visible above" : "Tab pane  j/k select  Enter detail  PgUp older  End live  : request  N project  n task  ? help  q exit", w), clip(simulator ? "Synthetic fixtures. No model calls. No live reliability claims." : "Closing this view leaves the coordinator and durable work intact.", w));
    return body.map((l) => clip(l, w));
  }
}

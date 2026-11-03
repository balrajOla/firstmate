import test from "node:test";
import assert from "node:assert/strict";
import { Dashboard, clean, clip, width, wrap } from "../packages/olympus-pi/view.mjs";

function fixture() {
  return { schema: "olympus.v1", generated_at: 100, chief: "idle", settings: [{ key: "mode", value: "simulator" }], counts: { working: 12, recovering: 1, blocked: 3, needs_you: 3, fresh_active: 12 },
    projects: Array.from({ length: 20 }, (_, i) => ({ id: `P${i}`, name: `Project ${i}`, charter: "Independent acceptance", state: "active" })),
    agents: [{ id: "chief", role: "chief", depth: 0, state: "idle", harness: "pi" }, { id: "worker", role: "rust", depth: 2, state: "working", parent: "captain", harness: "codex" }],
    tasks: Array.from({ length: 14 }, (_, i) => ({ id: `T${i}`, project: "P0", title: `Deliverable ${i}`, requirements: "Acceptance", state: i === 0 ? "recovering" : i === 1 ? "blocked" : "running", role: "rust", version: 1 })),
    incidents: [{ id: "I1", project_name: "Project 1", task: "T1", reason: "Choose the acceptance threshold", owner: "operator", next_check: 200 }],
    responses: Array.from({ length: 5 }, (_, i) => ({ id: `R${20 - i}`, seq: 20 - i, body: `Response ${20 - i}\nFull retained body`, project: "P0" })),
    completed: Array.from({ length: 10 }, (_, i) => ({ id: `D${i}`, title: `Completed ${i}`, requirements: "Done", project_name: "Project 0", state: "accepted" })) };
}
function view(snapshot = fixture()) {
  const calls = []; let source = snapshot;
  const request = async (r) => { calls.push(r); if (r.action === "snapshot") return structuredClone(source); if (r.action === "history") { const max = r.before || 30; return Array.from({ length: 10 }, (_, i) => ({ id: `R${max - 1 - i}`, cursor: max - 1 - i, body: `Old response ${max - 1 - i}` })); } if (r.action === "response.get") return { id: r.response, body: Array.from({ length: 80 }, (_, i) => `Line ${i} of the complete output`).join("\n") }; if (r.action === "projects.list") return source.projects; if (r.action === "project.create") return { project: "Pnew" }; if (r.action === "task.create") return { task: "Tnew" }; return []; };
  const d = new Dashboard(request); d.snapshot = snapshot; d.lastSuccess = 100000;
  return { d, calls, set: (s) => source = s };
}
test("live view includes all required categories and 5/10 histories", () => { const { d } = view(); const out = d.render(144, 40, 100000).join("\n"); for (const text of ["12 working", "1 recovering", "3 blocked", "Chief: idle", "LAST 5 RESPONSES", "LAST 10 COMPLETED", "RECOVERING", "NEXT ACTION FOR YOU", "SIMULATOR"]) assert.ok(out.includes(text), text); assert.ok(out.includes("Completed 9")); });
test("fleet animation moves with idle chief and does not issue requests", () => { const { d, calls } = view(); assert.notEqual(d.render(144, 40, 100000)[3], d.render(144, 40, 100250)[3]); assert.equal(calls.length, 0); });
test("stale observations freeze motion and disclose age", () => { const { d } = view(); const a = d.render(144, 40, 110000); const b = d.render(144, 40, 110250); assert.equal(a[3], b[3]); assert.ok(a.join("\n").includes("STALE / DISCONNECTED")); });
test("reduced motion retains textual working status", () => { const { d } = view(); d.reducedMotion = true; assert.equal(d.render(144, 40, 100000)[3], d.render(144, 40, 100250)[3]); assert.ok(d.render(144, 40, 100000)[3].includes("[WORK]")); });
test("narrow terminal keeps urgent counts and every line within bounds", () => { const { d } = view(); const lines = d.render(80, 24, 100000); assert.equal(lines.length, 24); assert.ok(lines.every((l) => width(l) <= 80)); assert.ok(lines[3].includes("3 blocked")); assert.ok(lines[3].includes("Chief: idle")); });
test("terminal control sequences cannot escape rendered records", () => { assert.equal(clean("\x1b[2Jhello\x1b]52;c;secret\x07world"), "helloworld"); assert.equal(clip("a界b", 3), "a界"); assert.ok(wrap("界界界", 4).every((s) => width(s) <= 4)); });
test("PgUp loads older pages and selects the newly loaded records", async () => { const { d, calls } = view(); await d.key("7"); const last = d.history.at(-1).cursor; await d.key("\x1b[5~"); assert.equal(calls.at(-1).before, last); assert.equal(d.history.length, 20); assert.equal(d.selected, 10); });
test("new responses preserve historical selection and show a badge", async () => { const { d, set } = view(); await d.key("7"); d.selected = 4; const selected = d.items()[4].id; const next = fixture(); next.responses[0].seq = 23; set(next); await d.refresh(); assert.equal(d.items()[d.selected].id, selected); assert.equal(d.newCount, 3); await d.key("\x1b[F"); assert.equal(d.tab, 0); assert.equal(d.newCount, 0); });
test("full response opens and scrolls beyond the summary", async () => { const { d } = view(); d.pane = 1; await d.key("\r"); const a = d.render(80, 24, 100000).join("\n"); await d.key("\x1b[6~"); const b = d.render(80, 24, 100000).join("\n"); assert.notEqual(a, b); assert.ok(d.detail.body.includes("Line 79")); await d.key("\x1b"); assert.equal(d.detail, null); });
test("new project wizard saves only on completion", async () => { const { d, calls } = view(); await d.key("N"); await d.key("New Project"); await d.key("\r"); assert.ok(!calls.some((c) => c.action === "project.create")); await d.key("Deliver with independent acceptance"); await d.key("\r"); assert.ok(calls.some((c) => c.action === "project.create" && c.name === "New Project")); });
test("cancelling an input form performs no write", async () => { const { d, calls } = view(); await d.key("N"); await d.key("Draft"); await d.key("\x1b"); assert.equal(d.form, null); assert.equal(calls.length, 0); });
test("task amendments send expected version instead of silently overwriting", async () => { const { d, calls } = view(); await d.key("a"); await d.key("Updated acceptance requirements"); await d.key("\r"); const req = calls.find((c) => c.action === "task.amend"); assert.equal(req.expected_version, 1); assert.equal(req.task, "T0"); });
test("disconnection retains cached work but labels it stale", async () => { const { d } = view(); d.request = async () => { throw new Error("coordinator unavailable"); }; await d.refresh(); const text = d.render(144, 40).join("\n"); assert.ok(text.includes("STALE / DISCONNECTED")); assert.ok(text.includes("Deliverable")); });

import { WakeGate } from "../packages/olympus-pi/wake.mjs";
test("chief wakes coalesce unchanged work, respect time and stop at the turn budget", () => {
  const gate = new WakeGate({ limit: 2, interval: 30000 });
  const first = { projects: [{ ready: 1 }], exceptions: [] };
  assert.equal(gate.consider(first, [], 0), true);
  assert.equal(gate.consider(first, [], 60000), false);
  assert.equal(gate.consider({ projects: [{ ready: 2 }], exceptions: [] }, [], 1000), false);
  assert.equal(gate.consider({ projects: [{ ready: 2 }], exceptions: [] }, [], 31000), true);
  assert.equal(gate.consider({ projects: [{ ready: 3 }], exceptions: [] }, [], 62000), false);
  gate.reset(); assert.equal(gate.consider(first, [], 62000), true);
});
test("an empty portfolio does not wake an idle model", () => {
  assert.equal(new WakeGate().consider({ projects: [], exceptions: [] }, []), false);
});

import { chiefToolPolicy } from "../packages/olympus-pi/policy.mjs";
test("chief blocks direct shell and file tools but permits broker coordination", () => {
  for (const tool of ["bash", "write", "exec", "nested-shell", "read"]) assert.equal(chiefToolPolicy(tool, true).block, true);
  assert.equal(chiefToolPolicy("olympus_control", true), undefined);
  assert.equal(chiefToolPolicy("olympus_help", true), undefined);
  assert.equal(chiefToolPolicy("bash", false), undefined);
});

test("calendar tabs refresh daily and weekly work and render recurring jobs", async () => {
  const {d,calls}=view(); const base=d.request;
  d.request=async r=>{
    if(r.action==="agenda") {calls.push(r);return {timezone:"Europe/London",jobs:[{id:"J1",project:"P0",project_name:"WebCraft",title:"Morning check",state:"running",bucket:"due",requirements:"Verify",due:1793610000}]};}
    if(r.action==="schedules.list") return [{id:"S1",project:"P0",project_name:"WebCraft",title:"Morning check",state:"active",cadence:"daily",local_time:"09:00",timezone:"Europe/London",next_run:1793696400,last_state:"accepted"}];
    return base(r);
  };
  await d.key("8");assert.ok(d.render(144,40).join("\n").includes("Morning check"));assert.equal(calls.at(-1).view,"today");
  await d.key("9");assert.equal(calls.at(-1).view,"week");
  await d.key("0");const out=d.render(180,40).join("\n");assert.ok(out.includes("RECURRING"));assert.ok(out.includes("daily 09:00 Europe/London"));assert.ok(out.includes("last accepted"));
});

test("project filtering is sent to history and cleared explicitly",async()=>{
  const {d,calls}=view();await d.key("2");await d.key("f");assert.equal(d.projectFilter,"P0");await d.key("7");assert.equal(calls.findLast(c=>c.action==="history").project,"P0");await d.key("f");assert.equal(d.projectFilter,"");assert.equal(calls.findLast(c=>c.action==="history").project,undefined);
});

test("natural language intake uses selected routing without rewriting requirements",async()=>{
  const {d,calls}=view();const base=d.request;d.request=async r=>{calls.push(r);if(r.action==="intake.preview")return {route:"existing_project",candidates:[{id:"P0"}]};if(r.action==="intake.submit")return {task:"new-followup"};return base(r);};
  await d.key(":");await d.key("Continue the WebCraft importer");await d.key("\r");assert.ok(calls.some(c=>c.action==="intake.submit"&&c.project==="P0"));assert.ok(!calls.some(c=>c.action==="task.amend"));assert.ok(d.notice.includes("new-followup"));
});

test("project outcome view keeps full responses and scrolls",async()=>{
  const {d}=view();const base=d.request;d.request=async r=>r.action==="project.outcomes"?{project:{name:"WebCraft"},counts:[{state:"accepted",count:1}],blockers:[],outputs:[{task_state:"accepted",title:"Importer",body:"Exact retained result\n".repeat(60)}],next_actions:[]}:base(r);
  await d.key("2");await d.key("o");assert.ok(d.detail.body.includes("WebCraft / PROJECT OUTCOMES"));const before=d.render(80,24).join("\n");await d.key("\x1b[6~");assert.notEqual(d.render(80,24).join("\n"),before);
});

test("calendar forms preserve timezone and pause only the selected schedule",async()=>{
  const {d,calls}=view();d.tab=9;d.schedules=[{id:"S1",project:"P0",state:"active"}];await d.key("p");assert.ok(calls.some(c=>c.action==="schedule.state"&&c.schedule==="S1"&&c.state==="paused"));assert.ok(!calls.some(c=>c.action==="task.pause"));
});

test("unmatched request creates a project without forcing a project-choice form",async()=>{
  const {d,calls}=view();const base=d.request;d.request=async r=>{calls.push(r);if(r.action==="intake.preview")return {route:"new_project",candidates:[]};if(r.action==="intake.submit")return {task:"new-task",project_created:true};return base(r);};
  await d.key(":");await d.key("Map lunar observations");await d.key("\r");
  assert.ok(calls.some(c=>c.action==="intake.submit"&&!c.project));assert.equal(d.form,null);
  assert.ok(d.notice.includes("connect it to enable execution"));
});

test("project learning displays evidence and does not imply model training",async()=>{
  const {d}=view();const base=d.request;d.request=async r=>r.action==="learning.status"?{policy:"Recorded evidence; not model fine-tuning",outcomes:[{profile:"reviewed-profile",role:"rust",complexity:"complex",outcome:"accepted_after_rework",failures:1}],lessons:[{kind:"review_rejected",task:"T1",evidence:"Fixture lost row 3",source:"artifact-1"}]}:base(r);
  await d.key("2");await d.key("l");
  assert.ok(d.detail.body.includes("not model fine-tuning"));assert.ok(d.detail.body.includes("accepted_after_rework"));assert.ok(d.detail.body.includes("artifact-1"));
});

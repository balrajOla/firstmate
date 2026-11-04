import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { controlTask } from "../packages/olympus-pi/task-control.mjs";
import { TaskNumbers } from "../packages/olympus-pi/task-numbers.mjs";

test("task numbers remain stable across sorting, additions, cancellation and branch restoration", () => {
  const n = new TaskNumbers(); const first = n.assign([{ id: "T2", created: 2 }, { id: "T1", created: 1 }]);
  assert.equal(n.label("T1"), "#1"); assert.equal(n.label("T2"), "#2");
  n.assign([{ id: "T2" }, { id: "T3" }]); assert.equal(n.label("T3"), "#3");
  assert.equal(n.resolve("#2"), "T2");
  const restored = new TaskNumbers(); restored.restore(first); restored.restore([["T3", 3]]);
  assert.equal(restored.resolve("1"), "T1");
  assert.deepEqual(restored.references("Compare #1 and #3. URL https://example.com/#2"), [{ label: "#1", task: "T1" }, { label: "#3", task: "T3" }]);
});
test("queued cancellation needs no invented worker stop", async () => {
  const calls = [];
  const read = async request => { calls.push(request); if (request.action === "task.get") return { task: { id: "T1", generation: 0, state: "ready" }, attempts: [] }; if (request.action === "operations.list") return []; return { state: "cancelled" }; };
  await controlTask({ task: "T1", command: "cancel" }, { read });
  assert.equal(calls.at(-1).action, "task.cancel"); assert.equal(calls.at(-1).confirmed_stopped, undefined);
});
test("uncertain unbound launches cannot be cancelled by claiming no worker exists", async () => {
  const read = async request => request.action === "task.get" ? { task: { id: "T1", generation: 1, state: "unknown" }, attempts: [{ id: "A1", generation: 1, runtime_id: "" }] } : [{ attempt: "A1", verb: "launch", state: "uncertain" }];
  await assert.rejects(controlTask({ task: "T1", command: "cancel" }, { read }), /not proved refused/);
});
test("bound worker cancel requires supported exact-generation exit and a fresh dead observation", async () => {
  const workspace = await mkdtemp(join(tmpdir(), "olympus-control-")); const calls = [], commands = [];
  let stopped = false;
  const attempt = { id: "A1", generation: 1, runtime_id: "native-1", runtime_generation: "G1", workspace };
  const read = async request => { calls.push(request); if (request.action === "task.get") return { task: { id: "T1", generation: 1, state: "unknown" }, attempts: [attempt] }; if (request.action === "operations.list") return []; return { state: "cancelled" }; };
  const run = async (_config, script, args, expected) => {
    commands.push({ script, args, expected });
    if (script === "fm-control.sh") { stopped = true; return "stopped native-1"; }
    return JSON.stringify({ schema: "fm-fleet-snapshot.v1", tasks: [{ id: "native-1", spawn_gen: "G1", remote: null, current_state: { freshness: "fresh" }, endpoint: { agent_alive: stopped ? "dead" : "alive", target: "S:P", freshness: "fresh", observed_at: new Date().toISOString() }, paths: { meta: { present: true, path: "/home/state/native-1.meta" }, worktree: { present: true, path: workspace } } }] });
  };
  try {
    await controlTask({ task: "T1", command: "cancel" }, { read, run, connection: async () => ({ root: "/root", home: "/home" }) });
    assert.deepEqual(commands.find(c => c.script === "fm-control.sh").expected, { FM_CONTROL_EXPECTED_SPAWN_GEN: "G1", FM_CONTROL_EXPECTED_ENDPOINT: "S:P" });
    assert.ok(calls.some(c => c.action === "runtime.observe" && c.snapshot.tasks[0].endpoint.agent_alive === "dead"));
    assert.equal(calls.at(-1).action, "task.cancel");
    assert.ok(!calls.some(c => c.action === "operation.prepare"));
  } finally { await rm(workspace, { recursive: true, force: true }); }
});

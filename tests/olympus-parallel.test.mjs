import test from "node:test";
import assert from "node:assert/strict";
import { ParallelIntake } from "../packages/olympus-pi/intake.mjs";

test("multiple requests are journaled and processed concurrently without waiting for the previous receipt", async () => {
  const pending = [], journal = [], writes = [];
  const intake = new ParallelIntake(async request => {
    if (request.action === "intake.preview") return new Promise(resolve => pending.push(() => resolve({ route: "existing_project", candidates: [{ id: "P1" }] })));
    writes.push(request); return { task: request.text === "first" ? "T1" : "T2", project: "P1" };
  }, record => journal.push(record));
  const first = intake.receive("first"), second = intake.receive("second");
  assert.equal(pending.length, 2); assert.equal(journal.filter(r => r.state === "received").length, 2);
  pending[1](); const acceptedSecond = await second;
  assert.equal(acceptedSecond.task, "T2");
  assert.equal(writes.length, 1);
  pending[0](); await first;
  assert.equal(writes.length, 2); assert.notEqual(writes[0].request_id, writes[1].request_id);
});
test("ambiguity is preserved instead of silently merging or amending existing work", async () => {
  const intake = new ParallelIntake(async () => ({ route: "needs_clarification", candidates: [{ id: "P1" }, { id: "P2" }] }));
  const record = await intake.receive("ambiguous request");
  assert.equal(record.state, "needs_project"); assert.equal(intake.pending().length, 1);
  const restored = new ParallelIntake(() => {}); restored.restore(record);
  assert.equal(restored.pending()[0].text, "ambiguous request");
});
test("numbered context is preserved as canonical IDs without changing the original user text", async () => {
  let submitted;
  const intake = new ParallelIntake(async request => {
    if (request.action === "task.get") return { task: { project: "P1" } };
    if (request.action === "intake.preview") return { route: "existing_project", candidates: [{ id: "P1" }] };
    submitted = request; return { task: "T2", project: "P1" };
  });
  const record = await intake.receive("Add tests for #1", undefined, "T1", [{ label: "#1", task: "T1" }]);
  assert.equal(record.text, "Add tests for #1"); assert.equal(submitted.project, "P1"); assert.match(submitted.text, /#1 = T1/);
});
test("unknown references remain preserved and never become guessed task mutations", async () => {
  let calls = 0;
  const intake = new ParallelIntake(async () => { calls++; });
  const record = await intake.receive("Update #99", undefined, undefined, [{ label: "#99" }]);
  assert.equal(record.state, "needs_reference"); assert.equal(calls, 0);
});
test("failed submissions preserve exact request identity for reconciliation, never auto-replay", async () => {
  let writes = 0;
  const intake = new ParallelIntake(async request => {
    if (request.action === "intake.preview") return { route: "new_project", candidates: [] };
    writes++; throw new Error("connection lost");
  });
  const record = await intake.receive("new work");
  assert.equal(record.state, "needs_reconciliation"); assert.equal(record.request.request_id, record.id); assert.equal(writes, 1);
});

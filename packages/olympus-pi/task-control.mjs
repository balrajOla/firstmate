// Operator-backed, task-scoped lifecycle. Never raw-kill a PID or invent a receipt.
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { readFile, lstat } from "node:fs/promises";
import { join } from "node:path";
const execute = promisify(execFile);

export async function firstmateCommand(config, script, args, expected = {}) {
  const env = { ...process.env, FM_HOME: config.home, ...expected };
  for (const key of ["FM_STATE_OVERRIDE", "FM_DATA_OVERRIDE", "FM_CONFIG_OVERRIDE", "FM_PROJECTS_OVERRIDE"]) delete env[key];
  const result = await execute("bash", [join(config.root, "bin", script), ...args], { env, timeout: 100000, maxBuffer: 8 * 1024 * 1024 });
  return result.stdout;
}
export async function loadConnection(home = process.env.OLYMPUS_HOME) {
  if (!home) throw new Error("OLYMPUS_HOME is required for task control");
  const config = JSON.parse(await readFile(join(home, "firstmate.json"), "utf8"));
  if (!config.root || !config.home) throw new Error("An explicit Firstmate connection is required");
  return config;
}
function currentAttempt(record) { return record.attempts?.find(a => a.generation === record.task.generation); }
function stoppedObservation(snapshot, attempt, mustBeDead = false) {
  if (snapshot.schema !== "fm-fleet-snapshot.v1" || snapshot.tasks?.length !== 1) throw new Error("Canonical exact-runtime observation required");
  const observed = snapshot.tasks[0], endpoint = observed.endpoint;
  const stamp = Date.parse(endpoint?.observed_at);
  if (observed.id !== attempt.runtime_id || observed.spawn_gen !== attempt.runtime_generation || observed.remote !== null || endpoint?.freshness !== "fresh" || observed.current_state?.freshness !== "fresh" || !Number.isFinite(stamp) || Date.now() - stamp > 30000 || stamp - Date.now() > 5000 || !["alive", "dead"].includes(endpoint.agent_alive)) throw new Error("Runtime identity, generation or observation freshness is not verified");
  if (attempt.workspace && observed.paths?.worktree?.path !== attempt.workspace) throw new Error("Observed workspace differs from the current attempt");
  if (mustBeDead && endpoint.agent_alive !== "dead") throw new Error("Worker stop is not confirmed; no cancellation or recovery performed");
  return observed;
}

export async function controlTask({ task, command, reason }, { read, connection = loadConnection, run = firstmateCommand } = {}) {
  const record = await read({ action: "task.get", task });
  const attempt = currentAttempt(record);
  const operations = (await read({ action: "operations.list" })).filter(op => op.attempt === attempt?.id);
  if (command === "inspect") return { task: record.task, attempt, operations, work_preserved: true };
  if (!["stop", "cancel", "recover"].includes(command)) throw new Error("Unsupported task control command");
  if (command === "cancel" && ["accepted", "cancelled"].includes(record.task.state)) throw new Error("Task is already terminal; use stop for an accepted worker's cleanup");
  let evidence;
  if (!attempt || record.task.generation === 0) evidence = { not_started: true, basis: "No attempt was created" };
  else if (!attempt.runtime_id) {
    const launches = operations.filter(op => op.verb === "launch");
    if (!launches.length || !launches.every(op => op.state === "refused") || operations.some(op => ["pending", "uncertain"].includes(op.state))) throw new Error("Unbound launch is not proved refused before execution; reconcile it before cancellation or recovery");
    evidence = { not_started: true, basis: "All current-attempt launches were refused before execution; no runtime was bound", operations: launches.map(op => ({ id: op.id, state: op.state, receipt: op.receipt })) };
  } else {
    if (!attempt.runtime_generation) throw new Error("Native generation is missing; observe and reconcile the runtime before control");
    const config = await connection();
    const observe = async () => JSON.parse(await run(config, "fm-fleet-snapshot.sh", ["--json", "--observe-only", "--observe-task", attempt.runtime_id]));
    const before = await observe(), binding = stoppedObservation(before, attempt);
    const expectedMeta = join(config.home, "state", attempt.runtime_id + ".meta");
    if (binding.paths?.meta?.path !== expectedMeta || !binding.paths?.meta?.present || !binding.paths?.worktree?.present) throw new Error("Exact Firstmate metadata/workspace binding is missing");
    const metadata = await lstat(binding.paths.worktree.path);
    if (!metadata.isDirectory() || metadata.isSymbolicLink()) throw new Error("Workspace must be a preserved real directory");
    let controlReceipt = "Already observed stopped";
    if (binding.endpoint.agent_alive === "alive") controlReceipt = await run(config, "fm-control.sh", [attempt.runtime_id, "exit"], { FM_CONTROL_EXPECTED_SPAWN_GEN: attempt.runtime_generation, FM_CONTROL_EXPECTED_ENDPOINT: binding.endpoint.target });
    const after = await observe();
    stoppedObservation(after, attempt, true);
    await read({ action: "runtime.observe", snapshot: after });
    evidence = { stopped: true, runtime: attempt.runtime_id, generation: attempt.runtime_generation, observation: after.tasks[0], controlReceipt };
  }
  if (command === "stop") return { ...evidence, work_preserved: true };
  const mutation = command === "cancel" ? { action: "task.cancel", task, reason: reason || "Task stopped/cancelled by the authorized workstation chief; workspace preserved", ...(attempt ? { confirmed_stopped: true } : {}) } : { action: "task.recover", task, confirmed_stopped: true, evidence: JSON.stringify(evidence) };
  const result = await read(mutation);
  return { ...result, task, evidence, work_preserved: true };
}

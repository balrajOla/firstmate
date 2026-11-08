# Local operator API

Use `awb request '{"action":"NAME","args":{...}}'`, or pass `-` and send JSON on stdin. `POST /request` accepts the same envelope on the loopback bridge with its private operator bearer token. This API can read sources and run reviewed local commands; it is not a hostile-worker sandbox.

## Operator actions

| Actions | Purpose |
|---|---|
| `project.add`, `project.list`, `project.grant` | Declare projects and explicit cross-project read grants |
| `prompt.submit` | Persist `text`, `client_key`, optional `project`, `reply_to`, `expected_revision` |
| `request.get`, `request.assign`, `request.cancel`, `request.reopen`, `request.retry` | Inspect durable intent, assign unresolved work, cancel, reopen or retry blocked delivery |
| `request.plan`, `workflow.save_plan` | Compile a plan with optional `kind` / `model_override`; save a reviewed plan for the current queued revision |
| `workflow.suggestions`, `workflow.group` | Suggest or explicitly group compatible undispatched requests; originals remain saved |
| `workflow.obligations`, `workflow.fulfill` | Add explicit acceptance criteria and link their submitted outcomes |
| `workflow.project_policy` | Priority and pause of new dispatch; running work remains coordinator-owned |
| `coordinator.tick`, `coordinator.observe`, `coordinator.health` | Dispatch, observe or check the **selected** backend; health is not model qualification |
| `request.route`, `routing.pending` | With `routing.semantic` configured: record (or, with `force`, redo) one request's semantic routing decision, or route the oldest unresolved request; only confident decisions assign a project |
| `evaluation.semantic_routing` | Run the configured semantic router over a held-out suite in a throwaway store; records expected and actual workflow and project, confidence, latency and misses beside the lexical policy |
| `coordinator.retire_agents` | Paperclip only: terminate named (`agent_ids`) or all `idle` Workbench-managed agents that are not running and hold no open task, and forget cached identities of agents that are no longer active |
| `context.inventory`, `context.ingest`, `context.mark`, `context.coverage` | Account for declared corpus processing |
| `context.search`, `context.source`, `context.packet`, `context.history` | Retrieve evidence and permitted saved prompts |
| `context.assertion`, `context.revoke`, `context.purge` | Retain contradictions and withdraw active source access |
| `backend.ingest`, `backend.recall`, `backend.read`, `backend.delete`, `backend.reconcile`, `backend.status` | Operate optional Hindsight/OpenViking managed copies with explicit project bindings |
| `outcome.propose`, `outcome.review`, `outcome.list`, `outcome.search`, `outcome.mark` | Version, review and find results; read/pin are separate from acceptance |
| `mail.send`, `mail.list`, `mail.ack`, `mail.receipt` | Durable messages with ordered delivered/read/acted receipts |
| `continuation.checkpoint`, `continuation.recover`, `continuation.effect` | Bounded handoff and uncertain-effect accounting |
| `lifecycle.request` | Request a fresh session for a saved plan node through the selected coordinator |
| `fleet.register`, `fleet.status`, `fleet.usage`, `fleet.disable`, `fleet.expire`, `fleet.cancel` | Register queues/workspaces, inspect capacity/reported usage, stop admission, expire leases or request cancellation |
| `execution.create_workspace`, `execution.list_workspaces`, `execution.inspect` | Create and inspect private copies of registered repository sources |
| `execution.plan`, `execution.run`, `execution.cancel` | Plan an explicit language check, execute its expected digest, or request process cancellation |
| `execution.list_runs`, `execution.get_run` | Inspect bounded status lists or retained output for one process |
| `execution.artifacts`, `execution.verify_artifacts` | Record and recheck actual files; mechanical checks never accept their meaning |
| `integration.basic_memory`, `.qmd`, `.history`, `.docling`, `.notebook`, `.researcher`, `.writing` | Execute configured named adapters |
| `capabilities.list`, `capabilities.activate` | Inspect the registry and activate pinned procedure bundles |
| `profile.add`, `profile.probe` | Configure and probe explicit official CLI model/effort/auth combinations |
| `writing.lint` | Advisory local word-budget and phrase checks |
| `evaluation.routing`, `.judges`, `.propose_policy`, `.apply_policy` | Measured decisions, human calibration and reviewed policy changes |
| `schedule.add`, `schedule.list`, `schedule.pause` | Explicit UTC interval schedules; missed runs coalesce |
| `qualification.record`, `qualification.cutover`, `evidence.list` | Retain target-machine gate evidence; no automatic promotion of fixture results |
| `doctor`, `snapshot` | Distinguish configuration, availability, execution and local state |

## Lifecycle receipts

`prompt.submit` uses the client key as retry identity. Reusing it with different input fails. Amendments require the current `expected_revision`. A grouped child stays grouped, and a successful amendment receipt adds `group: {request_id, revision}` identifying the updated parent. The child's original ID remains valid.

`request.reopen` creates a new request, copies current criteria as pending, and returns `reopened_from`; it does not overwrite the old request. `request.cancel` saves a cancellation receipt, not evidence that a remote process stopped. `request.retry` resets blocked jobs only; uncertain external mutations require reconciliation.

`outcome.review` returns the outcome's `status`, the separate `request_status`, and `remaining_deliverables`. Independent deliverables can be accepted separately; the request closes only after every current criterion is accepted. Stale outcomes, competing closure, self-review labels and invalidated accepted evidence are rejected. Names identify reviewers for audit but do not authenticate separate people or processes.

## Examples

Uppercase IDs and absolute paths below must be replaced with values returned by your installation.

```json
{"action":"workflow.obligations","args":{"request_id":"REQUEST_ID","revision":1,"items":[{"description":"Write the report","acceptance":"Include source-backed findings and unresolved limitations"}]}}
```

```json
{"action":"context.ingest","args":{"project":"PROJECT_ID","path":"/absolute/path/to/source.md","authority":"source"}}
```

```json
{"action":"backend.ingest","args":{"backend":"hindsight","project":"PROJECT_ID","source_id":"SOURCE_ID"}}
```

```json
{"action":"backend.recall","args":{"backend":"hindsight","project":"PROJECT_ID","query":"Which assumptions changed?","limit":8}}
```

Backend writes require an enabled, authorized destination. Recall is derived material linked to originals, not automatically accepted evidence. Use `backend.reconcile` for uncertain operations and `backend.delete` for managed remote copies; local purge is separate. See [backend arguments and bounds](CONTEXT_BACKENDS.md).

```json
{"action":"execution.create_workspace","args":{"project":"PROJECT_ID"}}
```

```json
{"action":"execution.plan","args":{"workspace_id":"WORKSPACE_ID","language":"python","goal":"test"}}
```

```json
{"action":"execution.run","args":{"plan_id":"PLAN_ID","expected_sha256":"PLAN_SHA256","timeout":60,"max_output_bytes":1048576}}
```

The plan exposes the command and source digest. Changed source requires a new plan. Dependencies are not automatically installed, and empty/unknown test output is not a test pass. See [execution pack contracts](EXECUTION_PACKS.md).

```json
{"action":"continuation.checkpoint","args":{"request_id":"REQUEST_ID","revision":1,"generation":1,"summary":"Completed work and remaining checks","artifacts":[{"source_id":"SOURCE_ID","sha256":"ORIGINAL_SHA256"}],"pending":["Verify the remaining claim"]}}
```

```json
{"action":"lifecycle.request","args":{"request_id":"REQUEST_ID","node":"NODE_ID_FROM_PLAN"}}
```

## Worker control and CLI

`POST /worker` is a separate host-scoped interface. Its envelope is `{ "host_id": "HOST_ID", "action": "heartbeat", "args": {} }`, authenticated by that host's bearer token. Allowed actions are `heartbeat`, `status`, `request_state`, `admit`, `renew` and `release`. Workers cannot register hosts, run operator commands, access another host's leases or use reserved interactive slots. Token hashes are excluded from status responses.

Registration returns a host credential once. Store it in a private regular file, not a command argument or repository. A local worker uses:

```sh
awb --home /absolute/state temporal-worker \
  --host-id local --token-file /absolute/private/host-token
```

A remote worker has separate local state and a scoped control connection:

```sh
awb --home /absolute/worker-state temporal-worker \
  --host-id remote-a --token-file /absolute/private/host-token \
  --control-endpoint http://127.0.0.1:18765
```

The loopback example is the worker end of a secure tunnel to the controller. A direct remote control endpoint requires HTTPS. Temporal service credentials/TLS are configured separately. See [runtime setup](RUNTIME_BACKENDS.md).

The stdio MCP server exposes typed tools with argument validation:

| Area | MCP tools |
|---|---|
| Intent and planning | `awb_submit`, `awb_projects`, `awb_request`, `awb_plan`, `awb_capabilities` |
| Context and outcomes | `awb_search`, `awb_packet`, `awb_outcomes`, `awb_backend_recall`, `awb_backend_read`, `awb_backend_ingest` |
| Local execution | `awb_execution_workspace`, `awb_execution_plan`, `awb_execution_run`, `awb_execution_status`, `awb_artifact_verify` |
| Continuation, writing and usage | `awb_checkpoint`, `awb_recover`, `awb_rollover`, `awb_writing_check`, `awb_usage` |

Other operator actions, including fleet registration, outcome acceptance, backend deletion/reconciliation and artifact registration, use `awb request` or HTTP. Read each tool's advertised schema; the MCP surface is deliberately narrower than the full operator API.

Exact signatures in [app.py](../src/assistant_workbench/app.py) and the owning modules define arguments. Unknown actions and unsupported methods fail visibly. This experimental API is versioned with the package; retain conformance tests when upgrading.

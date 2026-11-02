# Olympus action contract v1

This file owns the model and operator JSON protocol for the engineering preview.
Submit a JSON object through `olympus request -` on stdin or `olympus request '<JSON>'`.
Every mutation needs a unique `request_id`; the CLI supplies one if absent.
For network or process retries, retain the original request ID and exact body.
Reusing the same ID with different content is rejected.
Read actions are scoped to the authenticated identity.
A `credential_file` is a private filesystem reference, not a credential to paste into messages.

```bash
olympus request '{"action":"project.create","name":"WebCraft","charter":"Ship an importer with no lost rows","aliases":["webcraft"],"repository":"/absolute/path/to/repo"}'
olympus request '{"action":"agent.create","role":"chief","harness":"pi"}'
olympus request '{"action":"team.create","project":"P-ID","parent":"AG-CHIEF","roles":["senior","rust","quality"]}'
olympus request '{"action":"task.create","project":"P-ID","title":"Preserve invalid rows","requirements":"Retain invalid records and verify the exported count","role":"rust"}'
```

IDs in examples are placeholders; use the IDs returned by the preceding calls.
No example enables or launches a model merely by being displayed.

## Portfolio and work

| Action | Required fields | Authority and result |
|---|---|---|
| `snapshot` | none | Scoped live projection, latest five responses and ten accepted tasks |
| `projects.list` | none | Scoped project registry |
| `project.get` | `project` | Full exact charter and repository binding |
| `intake.preview` | `text` | Explainable, non-mutating candidates |
| `project.create` | `name`, `charter`; optional `aliases`, `repository` | Chief/operator; new project |
| `project.state` | `project`, `state`: active/paused/completed | Manager; completion rejects unfinished tasks |
| `task.create` | `project`, `title`, `requirements`; optional `role`, `priority` | Manager; immutable requirement version 1 |
| `task.get` | `task` | Current task, versions, attempts, dependencies, artifacts, incidents |
| `task.amend` | `task`, `expected_version`, `requirements` | Manager; supersedes old requirements without erasing them |
| `task.ack_version` | `task`, `version`, `checkpoint` | Current owner/operator; confirms a safe current-version checkpoint |
| `task.link` | `task`, `depends_on` | Manager; cycle checked dependency |
| `task.next` | none | At most 20 eligible tasks, ordered by current project load, priority and age |
| `task.claim` | `task`, `agent`; optional `workspace`, `reserve_children` in 0..8 | Owner or manager; atomic capacity reservation |
| `task.heartbeat` | `task`, `generation` | Current owner/operator; contact, never progress |
| `task.progress` | `task`, `generation`, `fingerprint`; optional `checkpoint` | Current owner/operator; distinct progress and loop evidence |
| `task.result` | `task`, `generation`, `body`, `code_revision` | Current owner/operator; candidate artifact and retained response |
| `task.review` | `artifact`, `code_revision`, `passed`, `evidence`; operator also supplies `reviewer` | Independent quality/reviewer identity, exact version/revision |
| `task.block` | `task`, `generation`, `reason`, `next_check` | Current owner/operator; timed block |
| `task.pause` / `task.resume` | `task`, `reason` | Manager; changes obligation state, does not assert a process was interrupted |
| `task.cancel` | `task`, `reason`, `confirmed_stopped` when an attempt exists | Manager; stop proof is a separate runtime obligation |
| `task.recover` | `task`, `confirmed_stopped: true`, `evidence` | Operator; bounded replacement, old generation fenced |
| `incident.resolve` | `incident`, `resolution` | Scoped manager; operator resolves operator-owned incidents |
| `history` | optional `kind`: responses/tasks/projects, `limit`, `before`, `search` | Stable completion/response cursor; use last returned `cursor` for next page |
| `response.get` | `response` | Full retained output |

An accepted task cannot be amended in place; create a follow-up task.
A parent's result is rejected while any child dependency remains unaccepted.
A paused or uncertain attempt still occupies capacity.
A parent that plans to spawn children should claim with `reserve_children` so other parents cannot consume every slot before its child runs.
A child converts one pre-reserved slot atomically; failed child creation rolls the reservation back.

## Teams, routing and runtime operations

| Action | Required fields | Contract |
|---|---|---|
| `agent.create` | `role`; nonchief also `project`; optional `parent`, `harness`, `model` | Manager; returns identity, role instructions and private credential path |
| `team.create` | `project`, `roles`; optional `parent`, `harness`, `model` | Manager; transactional captain plus 1-8 specialists; no runtime launch |
| `agent.activity` | `state`: working/idle; operator also `agent` | Own activity and observation time |
| `agent.retire` | `agent` | Manager; rejects active work or live child identities |
| `child.reserve` | `task`, `generation`, `role`, `title`, `requirements` | Current parent owner; child identity, task, dependency and capacity in one transaction |
| `dispatch.plan` | `task`; optional `tightly_coupled`, `bounded_question`, `coherent_deliverables` | Recommends inline, reserved native child, independent worker or sub-captain; only verified profiles with matching `capabilities.roles` are candidates |
| `profile.register` | `profile`, `harness`, `model`, `version`; optional `capabilities` | Operator; registration or update resets verification |
| `profile.verify` | `profile`, `evidence` | Operator-attested exact-version live conformance; never accepts simulator evidence for a real harness |
| `profiles.list` | none | Profiles and verification evidence |
| `system.configure` | optional `global_cap`, `project_cap` in 1..100, `dispatch_enabled` | Operator; default caps 4 global, 2 per project |
| `operation.prepare` | `task`, `profile`, `verb`; `body` for message/relaunch | Manager; requires enabled dispatch and a claimed attempt; records a pending effect |
| `operations.list` | none | Scoped recent runtime operations |
| `operation.receipt` | `operation`, `state`: confirmed/uncertain/refused, `receipt` | Operator; manual reconciliation; confirmed effects cannot be blindly replayed |
| `evaluation.record` | `task`, `profile`, `outcome`: accepted/failed/cancelled, `elapsed_ms`; optional `cost` | Operator; one measured outcome per task/profile; accepted requires accepted work |
| `runtime.observe` | `snapshot` | Operator; canonical Firstmate v1 inventory with fresh exact-runtime observations |

Supported runtime verbs are launch, message, interrupt, exit and relaunch.
The bridge uses local-only Firstmate tasks with yolo off and unique runtime IDs per Olympus attempt.
The brief includes the task contract, generation, role and credential path.
Firstmate's own validation can refuse launch; its scripts are never bypassed.
A confirmed launcher exit is an effect receipt, not accepted work or proven model progress.
All external effects begin in durable uncertainty; a crash cannot cause automatic re-execution.

A minimal profile and evidence shape follows.

```json
{
  "action": "profile.register",
  "profile": "local-rust-codex",
  "harness": "codex",
  "model": "YOUR_CONFIGURED_MODEL",
  "version": "EXACT_COMPLETE_CODEX_VERSION_OUTPUT",
  "capabilities": {"roles": ["rust", "engineer"], "native_children": false}
}
```

```json
{
  "action": "profile.verify",
  "profile": "local-rust-codex",
  "evidence": {
    "harness": "codex",
    "version": "EXACT_COMPLETE_CODEX_VERSION_OUTPUT",
    "real_harness": true,
    "command": "Actual reproducible conformance command, home and test artifact",
    "observed_result": "Actual observed results; never copy an invented success",
    "checks": {"launch": true, "message": true, "interrupt": true, "resume": true, "stop": true}
  }
}
```

Verification is an operator attestation, not a cryptographically verified test artifact.
For `native_children: true`, evidence must also include true `child_launch`, `child_message`, `child_stop` and `child_budget` checks.
The preview has no universal vendor-native spawn shim; the corresponding installed harness capability must be proven locally before using that route.
Local performance ranking needs at least five outcomes; fewer samples are explicitly labelled insufficient evidence.
Equal scores use stable ordering rather than inventing model quality differences.

## Durable communication and chief obligations

| Action | Fields | Result |
|---|---|---|
| `message.send` | `recipient`, `thread`, `body`; optional `task`, `priority`: normal/progress/critical, future `deadline` | Persisted receipt only |
| `message.inbox` | Agent reads own inbox; operator supplies `recipient` | Bounded pending summaries, critical first |
| `message.get` | `message` | Exact full body, sender/recipient/operator only |
| `message.ack` | `message`, `state`: delivered/read/acted | Recipient-only, sequential receipts; stale task generations/versions rejected |
| `chief.briefing` | none | At most 20 project summaries and five exceptions |
| `chief.finish` | `disposition`: delegated/waiting/decision/result/idle, `reason`; waiting requires future `next_check` | Records disposition; idle refuses ready work; never shuts down daemon |
| `events` | optional `after` | Up to 100 scoped audit events |

Cross-project communication requires an explicit dependency relationship and a task-scoped message.
Unrelated project contents are not exposed by the broker.
Fetch a truncated message using `message.get` before acknowledging it as read or acted.
A sender must preserve a rejected message request and retry after backpressure clears; a success response never implies delivery or action.
Only operator credentials can call the transport-level `system.shutdown` control.

## Memory and context transfer

| Action | Fields | Result |
|---|---|---|
| `memory.put` | `title`, `body`, nonempty `sources`; optional `project`, `status`: candidate/verified | Provenance-backed record plus local Markdown; verification requires manager |
| `memory.search` | optional `query`, `project` | Current accessible records, excluding superseded versions |
| `memory.supersede` | `old`, `new` | Same-scope records; preserve originals and update vault status |
| `context.create` | `task`, `generation`, `checkpoint` | Paused current attempt; exact manifest inventory and hashes |
| `context.get` | `capsule`; optional `key` | Inventory by default; fetch one full required record using its key |
| `context.verify` | `capsule`, ordered `reconstructed` key/hash pairs, `next_action`; operator supplies `successor` | Different identity, matching project/role, unchanged manifest |
| `context.transfer` | `capsule`, `confirmed_stopped: true` | Operator; successor free, old generation fenced, workspace retained |

The manifest includes task requirements, charter, dependencies, artifacts, pending messages, open incidents, workspace/runtime identity and verified knowledge.
New relevant evidence invalidates an old manifest.
Fetching record references incrementally bounds prompt size while preserving access to the complete durable source.

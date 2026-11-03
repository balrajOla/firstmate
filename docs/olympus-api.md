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
| `intake.preview` | `text`; optional `project` | Explainable, non-mutating route: existing_project, new_project or needs_clarification |
| `project.create` | `name`, `charter`; optional `aliases`, `repository` | Chief/operator; new project |
| `project.state` | `project`, `state`: active/paused/completed | Manager; completion rejects unfinished tasks |
| `task.create` | `project`, `title`, `requirements`; optional `role`, `priority`, `complexity` | Manager; immutable requirement version 1 |
| `task.get` | `task` | Current task, versions, attempts, dependencies, artifacts, incidents |
| `task.amend` | `task`, `expected_version`, `requirements`; optional `complexity` | Manager; supersedes requirements, retains history and queues owner/dependent notifications |
| `task.ack_version` | `task`, `version`, `checkpoint` | Current owner/operator; confirms a safe current-version checkpoint |
| `task.link` | `task`, `depends_on` | Manager; cycle checked dependency |
| `task.next` | none | At most 20 eligible tasks, ordered by current project load, priority and age |
| `task.claim` | `task`, `agent`; optional `workspace`, `reserve_children` in 0..8 | Owner or manager; atomic capacity reservation |
| `task.heartbeat` | `task`, `generation` | Current owner/operator; contact, never progress |
| `task.progress` | `task`, `generation`, `fingerprint`; optional `checkpoint` | Current owner/operator; distinct progress and loop evidence |
| `task.result` | `task`, `generation`, `body`, `code_revision` | Current owner/operator; candidate artifact and retained response |
| `task.review` | `artifact`, `code_revision`, `passed`, `evidence`; operator also supplies `reviewer` | Independent quality/reviewer identity, exact version/revision |
| `task.block` | `task`, `generation`, `reason`, `owner`, `next_check` | Current owner/operator; timed block |
| `task.pause` / `task.resume` | `task`, `reason` | Manager; changes obligation state, does not assert a process was interrupted |
| `task.cancel` | `task`, `reason`, `confirmed_stopped` when an attempt exists | Manager; stop proof is a separate runtime obligation |
| `task.recover` | `task`, `confirmed_stopped: true`, `evidence` | Operator; bounded replacement, old generation fenced |
| `incident.resolve` | `incident`, `resolution` | Scoped manager; operator resolves operator-owned incidents |
| `history` | optional `kind`: responses/tasks/projects, `limit`, `before`, `search`, `project` | Stable completion/response cursor; use last returned `cursor` for next page |
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
Profile capabilities may declare `complexities: ["routine", "standard", "complex"]` and `effort_by_complexity: {"routine": "low", "standard": "medium", "complex": "high"}` only where those levels have been verified for that model.
Any effort mapping additionally requires `evidence.checks.effort: true`; unsupported classes are excluded before launch.
Profiles without declared complexity support cannot receive complex tasks.
`operation.prepare` binds the exact configuration and mapped effort to its attempt; the bridge passes the effort to Firstmate and refuses configuration drift.
Local performance ranking needs at least five automatically recorded, independently reviewed outcomes matching role, complexity and exact configuration; fewer samples are explicitly labelled insufficient evidence.
A changed model, version or capability declaration cannot inherit the old configuration’s quality score.
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

The manifest includes task requirements, charter, dependencies, artifacts, pending messages, open incidents, workspace/runtime identity, routing, failure lessons, profile bindings, outcomes, notifications and verified knowledge.
Acknowledging amended requirements never clears an unrelated unresolved blocker.
New relevant evidence invalidates an old manifest.
Fetching record references incrementally bounds prompt size while preserving access to the complete durable source.

## Automatic operation and retained project context

| Action | Fields | Contract |
|---|---|---|
| `intake.submit` | `text`; optional `project`, `title`, `role`, `complexity`, `auto_new` | Manager; confident matches create a contextual follow-up, completed projects reopen, identical requests deduplicate; unmatched requests create an unconnected project by default; ambiguous routes refuse mutation |
| `intake.submit` for a new project | `text`, `new_project: true`; optional `name`, `charter`, `repository` | Chief/operator; explicit separation overrides similarity; absent name/charter derive from the request; no execution permission is inferred |
| `intake.submit` amendment | `text`, `task`, `expected_version`; optional `complexity` | Manager; updates only that exact task through optimistic version checking and durable notifications |
| `project.bind` | `project`, `repository` | Operator; existing local Git working copy, no active attempts; execution policy remains unchanged |
| `learning.status` | `project`; optional `limit` in 1..50, default 20 | Scoped reviewed outcomes and bounded lesson evidence; complete evidence remains in task.get |
| `project.context` | `project` | Bounded project evidence and full-record references, including completed work |
| `project.outcomes` | `project`; optional `before`, `limit` | Separate project counts, blockers, responses and next actions; responses paginate by cursor |
| `memory.get` | `knowledge` | Full exact source-backed record with scope enforcement |
| `automation.configure` | `project`, `enabled`; optional `progress_timeout` in 60..86400 seconds | Operator; enable automatic assignment, independent review, mailbox notifications and bounded recovery; default timeout 900 seconds |
| `automation.status` | none | Scoped project policies and latest reason for waiting |
| `review.submit` | Current review `task`, `generation`, `passed`, `code_revision`, `evidence` | Assigned independent quality worker; review the linked artifact and finish the review job atomically |
| `backups.list` | none | Operator; latest 20 scheduled backup attempts and results |
| `maintenance.configure` | optional IANA `timezone`, `backup_interval` in seconds | Operator; backup interval is 0 to disable or 300..604800; default 86400 |

Automatic dispatch still requires the global dispatch switch, an execution-enabled Firstmate connection and verified role-capable profiles.
Each automatic producer reserves an additional slot for its independent review.
Review reservations are converted atomically; a parent’s admitted children need their own review headroom.
The portable child route is `child.reserve` followed by coordinator dispatch through Firstmate.
No vendor-native API is assumed merely because an agent has a parent.
A second failed review requests bounded recovery to a different eligible profile when one exists and budget remains.
Fresh matching stop evidence is mandatory before replacement; otherwise the old generation remains fenced from replacement.
No eligible alternative or exhausted recovery budget creates a decision.
Failure observations remain distinct from independently accepted fixes, and one attempt produces one quality sample.
Repeated failures are keyed to the requirement version so a later amendment retains context without inheriting the old task-specific exclusion.
Accepted results produce source-linked verified memory records; long outcome bodies are bounded previews whose artifact references retain the full evidence.
The matcher uses explicit names, aliases and shared terms; it does not claim universal semantic understanding.

## Calendar

| Action | Fields | Contract |
|---|---|---|
| `agenda` | optional `view`: today/week, `timezone`, `project` | Due and overdue jobs, active work and completed results in the selected local calendar window |
| `schedules.list` | optional `project` | Recurring templates, next occurrence and last task state |
| `schedule.create` | `project`, `title`, `requirements`, `cadence`: daily/weekdays/weekly; optional `role`, `time`, `timezone`, `weekday` | Manager; defaults engineer, 09:00, configured timezone, Monday=0; Sunday=6 |
| `schedule.state` | `schedule`, `state`: active/paused/cancelled | Manager; changes future occurrence generation, preserves existing tasks |
| `task.due` | `task`, positive Unix timestamp `due` | Manager; records the task’s due date independently of requirements |

A unique schedule/occurrence record prevents duplicate jobs across coordinator restarts.
Missed occurrences coalesce into one outstanding job and the next occurrence advances beyond the current clock.
If the previous occurrence is still unfinished, the schedule waits without creating overlapping work.
The first occurrence of an ambiguous clock time is used; a nonexistent clock time moves forward to the next valid local minute.
Schedules pause on backup restoration until the operator reconciles the restored home.

```json
{"action":"schedule.create","project":"P-YOUR-PROJECT","title":"Weekly delivery review","requirements":"Return separate project outcomes, blockers and next actions with evidence","role":"researcher","cadence":"weekly","weekday":0,"time":"09:00","timezone":"Europe/London"}
```

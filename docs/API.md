# Local operator API

Use `awb request '{"action":"NAME","args":{...}}'`, or pass `-` and send JSON on stdin. `POST /request` accepts the same envelope on the loopback bridge with its private bearer token. This is an operator API, not a hostile-worker sandbox.

| Actions | Purpose |
|---|---|
| `project.add`, `project.list`, `project.grant` | Declare projects and explicit cross-project read grants |
| `prompt.submit` | Persist `text`, `client_key`, optional `project`, `reply_to`, `expected_revision` |
| `request.get`, `request.assign`, `request.cancel`, `request.reopen`, `request.retry` | Inspect and control durable intent |
| `request.plan` | Compile a plan with optional `kind` / `model_override`; does not dispatch |
| `workflow.save_plan` | Save a reviewed plan for the current queued revision |
| `workflow.suggestions`, `workflow.group` | Propose or explicitly group compatible undispatched requests |
| `workflow.obligations`, `workflow.fulfill` | Define deliverables and link their submitted outcome |
| `workflow.project_policy` | Priority and pause of new dispatch; running work remains coordinator-owned |
| `context.inventory`, `context.ingest`, `context.mark`, `context.coverage` | Account for declared corpus processing |
| `context.search`, `context.source`, `context.packet`, `context.history` | Retrieve evidence and permitted saved prompts |
| `context.assertion`, `context.revoke`, `context.purge` | Preserve contradictions and handle active-store withdrawal |
| `outcome.propose`, `outcome.review`, `outcome.list`, `outcome.search`, `outcome.mark` | Version, review and find results; read is not accept |
| `mail.send`, `mail.list`, `mail.ack`, `mail.receipt` | Durable messages with delivered/read/acted receipts |
| `continuation.checkpoint`, `continuation.recover`, `continuation.effect` | Bounded handoff and uncertain-effect accounting |
| `lifecycle.request` | Request a fresh session for an existing plan node |
| `coordinator.tick`, `coordinator.observe` | Deliver one item / inspect actual remote issue state |
| `integration.basic_memory`, `.qmd`, `.history`, `.docling`, `.notebook`, `.researcher`, `.writing` | Execute configured named adapters |
| `capabilities.list`, `capabilities.activate` | Inspect registry and activate pinned procedure bundles |
| `profile.add`, `profile.probe` | Configure explicit official CLI model/effort/auth combinations |
| `writing.lint` | Advisory local style flags |
| `evaluation.routing`, `.judges`, `.propose_policy`, `.apply_policy` | Measured decisions, human calibration and reviewed policy changes |
| `schedule.add`, `schedule.list`, `schedule.pause` | Explicit UTC interval schedules; missed runs coalesce |
| `qualification.record`, `qualification.cutover`, `evidence.list` | Retained target-machine gate evidence |
| `doctor`, `snapshot` | Health distinctions and local state |

Examples:

```json
{"action":"context.ingest","args":{"project":"MyProject","path":"/absolute/path/to/source.md","authority":"source"}}
```

```json
{"action":"workflow.obligations","args":{"request_id":"REQUEST_ID","revision":1,"items":[{"description":"Write the report","acceptance":"Include the source-backed findings and unresolved limitations"}]}}
```

```json
{"action":"continuation.checkpoint","args":{"request_id":"REQUEST_ID","revision":1,"generation":1,"summary":"What is complete and what remains","artifacts":[{"source_id":"SOURCE_ID","sha256":"ORIGINAL_SHA256"}],"pending":["Verify the remaining claim"]}}
```

```json
{"action":"qualification.record","args":{"gate":"terminal_gui","profile":"subscription","result":"passed","artifacts":["/private/evidence/wezterm-session.png"],"notes":"Describe the actual interactions and observed results","observer":"operator"}}
```

Exact Python signatures in `app.py` and the relevant module define the current argument contract. Unknown actions and unsupported methods fail visibly. This experimental API is versioned with the package; do not assume forward compatibility without the tests.

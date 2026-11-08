# Feature scenarios and evidence requirements

Every catalog feature, PF01–PF71, has one explicit scenario with a successful path, a deliberate failure and an edge case in [feature-evaluations.json](../evals/feature-evaluations.json). The four entries previously called optional are included. A feature can remain blocked even when its local tests pass: the original request also requires real workers, live services, target-terminal operation, usable artifacts and independent review.

This is a bounded test plan, not a claim of zero defects or exhaustive correctness. Passing applies only to the tested implementation, environment and listed scenarios. The latest [feature catalog](../features.json) owns implementation maturity; the manifest preserves the maturity at scenario design and a separate limitation for each case.

## Reading the results

| Result | Meaning |
|---|---|
| `passed` | All mapped cases, source binding, required observed gates and all three acceptance paths pass; the catalog records no incomplete integration. This is scenario qualification, not universal correctness. |
| `failed` | A mapped test, observed acceptance path or required measurement fails. Another passing run cannot erase it. |
| `blocked` | Required tests, credentials, service, target environment, current source binding, acceptance paths or retained evidence are missing; or the catalog still records incomplete integration. |
| `not_implemented` | The catalog still says the feature is not implemented, unless an actual failure also needs reporting. |
| `local_status` | Status of the mapped pytest assertions only. It is not the full feature status. Source freshness is separately reported as `evidence_binding_status`. |

A skipped test is blocked. A test function existing on disk is not a result. A passing fixture, protocol emulator, planned agent or headless UI does not establish a live product worker, external service or WezTerm desktop run. An authenticated CLI alone does not establish successful execution. Failed, skipped and missing observations stay visible.

## Running the evaluation

Complete the code and evaluation-manifest edits first. Any later implementation or test change invalidates the source fingerprint. Build a clean install and configure only explicitly supported providers. A subscription sign-in is usable only through its supported harness; it is not an API credential for unrelated services.

Record the implementation just before running the suite:

```sh
.venv/bin/python scripts/evaluate_feature_coverage.py --write-run-metadata .local-evidence/scenarios/run-metadata.json
.venv/bin/pytest -q --junitxml=.local-evidence/scenarios/all-tests.xml
```

Set `AWB_INSTALLED_CLI` to an independently installed Workbench executable when running the installed-package case. Put real Node, TypeScript and Cargo executables on the test process's PATH to exercise their positive and deliberately failing controls. Tests that cannot find the required runtime must stay skipped; do not replace them with stand-ins.

Run the public-interface journey and each relevant gate below. Retain task IDs, agent/run IDs, original prompts, revisions, source anchors, start/finish times, assertions, process exits, outputs, screenshots and independent reviews. For parallel workers, establish overlapping product-owned executions; development assistants working on this repository are not Workbench workers.

The [30-instruction website/mobile/research workload](../evals/scenarios-30.json) uses stable IDs `S01`–`S30`. Each feature's `journey_scenarios` cross-references the actual workload, and the evaluator checks both sides. These are planned coverage references, not proof of execution. The workload includes 22 durable application prompts plus grouping, cancellation, reopening, retry, stale-revision, routing and pause/resume controls; all 30 original instructions are also retained in the run journal.

Run the real application bookkeeping journey in a fresh isolated home:

```sh
.venv/bin/python scripts/run_scenarios.py --home .local-evidence/scenarios/control-run/workbench --output .local-evidence/scenarios/control-run/results.json
```

The runner creates no synthetic website, mobile binary, research findings or accepted outcome. It saves 30 step results, a durable JSONL journal and state snapshots. `bookkeeping_status=passed` means the checked intake/control assertions passed; `delivery.status=blocked` means actual product completion still lacks independent evidence. The long-prompt control proves bounded context assembly with a visible omission. Execution preflight and genuine context rollover remain separate observations.

Add `--live --max-wait-seconds 30` to attempt the configured coordinator after the control sequence. The deadline bounds coordinator calls, not screenshot callbacks. This mode does not inject a model profile, bypass authentication or start a substitute executor; it retains failed/blocked probes and never accepts an outcome. In the current runner, current-revision artifact correctness still requires the feature gates, so live mode exits 3 unless a test/capture failure requires exit 1. Control mode exits 0 for successful bookkeeping even while product delivery is blocked. Existing evidence paths and duplicate run IDs are refused.

For actual terminal capture, import `scripts/run_scenarios.py` and call `run_scenarios(home, output, on_step=callback)`. `callback(step_id, result)` runs after the saved state and per-step snapshot exist; it can return an observation object such as a native screenshot path. Capture failures stay visible without erasing later steps. The runner accepts no arbitrary shell callback.

Evaluate the matching JUnit and observed receipts:

```sh
.venv/bin/python scripts/evaluate_feature_coverage.py --junit .local-evidence/scenarios/all-tests.xml --run-metadata .local-evidence/scenarios/run-metadata.json --live-evidence .local-evidence/scenarios/live-feature-receipts.json --output .local-evidence/scenarios/feature-coverage.json
```

Omit `--live-evidence` when no actual observations exist. The report will explain the resulting blocks. Repeat `--junit PATH` to include distinct suite runs from the same source and recorded run window. A failure in any supplied run remains a failure; an installed-artifact pass may replace an earlier skip. An unmapped failing test also prevents whole-suite qualification and is listed separately. Exit codes are 0 for all 71 scenarios qualified, 1 for a failure, 2 for invalid input and 3 for blocked/not implemented features.

## Observed receipt format

The following is a **blocked template**, not a run result. A passing receipt requires all acceptance paths, concrete observations, current source fingerprint, fresh timestamp and hash-verified retained artifacts. Use the exact feature/scenario/gate combination from the manifest. Add `metrics` where the gate specifies thresholds. Each artifact path must stay within this repository's evidence root. Obtain artifact SHA-256 values from actual retained files; never put credentials in evidence.

```json
{
  "schema": 1,
  "results": [
    {
      "feature_id": "PF01",
      "scenario_id": "E-PF01",
      "gate": "workers",
      "status": "blocked",
      "kind": "live_model",
      "observer": "replace-with-actual-observer",
      "notes": "Actual worker execution has not been observed.",
      "source_fingerprint": "copy-the-matching-run-fingerprint",
      "observed_at": "replace-with-timezone-qualified-observation-time",
      "acceptance": {
        "happy_path": "blocked",
        "failure_path": "blocked",
        "edge_case": "blocked"
      },
      "metrics": {},
      "artifacts": []
    }
  ]
}
```

Passing evidence expires after the gate's declared window (currently 24 hours from observation). Source fingerprints and file hashes protect accounting from stale or altered files; they do not prove that an observation is truthful or an LLM answer is correct. Review artifact contents. Model judges may assist that review but cannot replace measured human calibration or original-source checks.

## Evidence gates

Each feature requires every gate listed for it. For each gate, perform that feature's happy, failure and edge paths. Numeric measurements are checked by the evaluator; descriptions and artifact contents still require inspection. Minimum counts below are bounded qualification thresholds, not claims about unlimited capacity.

| Gate | Required observation | Prerequisite | Enforced numeric measurements |
|---|---|---|---|
| `journey` | Run the stated scenario through public CLI/MCP/operator interfaces; retain prompts, revisions, assertions and final state. | Installed Workbench and isolated website/mobile/research project state | No numeric threshold; inspect all three acceptance paths. |
| `workers` | Product-owned real agents overlap, exchange evidence and produce independently verified artifacts; external development assistants are not product workers. | Authenticated model harness and an actual coordinator-owned worker run | product_workers ≥ 2; peak_concurrent_workers ≥ 2; independently_reviewed_artifacts ≥ 1 |
| `coordinator` | Run the configured coordinator, inspect real agent/issue/run identities and reconcile process failures. | Running configured Paperclip or exclusive qualified replacement coordinator | No numeric threshold; inspect all three acceptance paths. |
| `models` | Probe actual installed harness/model/effort profiles and verify effective settings from real execution receipts. | Signed-in official CLI or explicitly configured API/local model provider | No numeric threshold; inspect all three acceptance paths. |
| `lcm` | Actual LCM capture, fresh-process recall and cross-project rejection; preserve source identities. | Pinned running LCM installation with permitted history capture configured | No numeric threshold; inspect all three acceptance paths. |
| `summaries` | Generate and expand summaries through multiple context windows and check originals, numeric qualifiers, revisions and unresolved contradictions. | Running context backend and a genuine summarizing model | No numeric threshold; inspect all three acceptance paths. |
| `qmd_exact` | Real QMD indexes a project collection and retrieves exact terms without crossing collections. | Built pinned QMD with project-bound collections | No numeric threshold; inspect all three acceptance paths. |
| `qmd_semantic` | Real QMD semantic retrieval returns relevant passages for paraphrased queries and preserves source/project scope. | QMD semantic model and index installed with a held-out query set | No numeric threshold; inspect all three acceptance paths. |
| `corpus` | Enumerate a mixed corpus larger than one model window; retain complete coverage denominator, exclusions, conflicts and bounded retrieval. | Mixed source corpus with known counts, versions and deliberately contradictory facts | No numeric threshold; inspect all three acceptance paths. |
| `retention` | Delete or revoke a source across the active archive and configured external stores, retaining audited failures and backup retention boundaries. | Configured real context stores plus disposable backup/index data | No numeric threshold; inspect all three acceptance paths. |
| `recovery` | Five genuine fresh-model sessions recover sources and amendments; cumulative budgets and uncertain side effects remain fenced. | Actual coordinator, authenticated worker and controlled context/session rollover | fresh_model_sessions ≥ 5; recovered_source_anchors ≥ 1; duplicate_effects ≤ 0; lost_accepted_amendments ≤ 0 |
| `capacity` | Mixed interactive/background work exercises admission, pause, priorities, recovery and capacity limits with recorded latency and no lost prompts. | Running coordinator/worker pool and a bounded load generator | submitted_prompts ≥ 30; projects ≥ 3; lost_prompts ≤ 0; capacity_limit_violations ≤ 0 |
| `gui` | Operate the actual WezTerm desktop: navigate, resize, submit, reconnect and inspect screenshots for the stated scenario. | Installed WezTerm and a real graphical display on the target platform | tested_terminal_sizes ≥ 3; reconnects ≥ 1 |
| `brainstorm` | Activated pinned Superpowers produces alternatives, constraints, tradeoffs and a reviewed decision from actual supplied evidence. | Pinned activated Superpowers procedures and a real reasoning worker | No numeric threshold; inspect all three acceptance paths. |
| `writing` | Evaluate concise output for the stated audience while preserving quantities, negation, uncertainty and source meaning. | Known-answer source corpus and independent editorial review rubric | No numeric threshold; inspect all three acceptance paths. |
| `procedures` | Activate real pinned procedure checkouts; record hashes and block changed/missing procedures on resumed work. | Installed Superpowers, Elements of Style and Humanizer source checkouts | No numeric threshold; inspect all three acceptance paths. |
| `vale` | Actual Vale clean/verbose controls and clear-writing/Humanizer workflow run; inspect alert JSON as well as process exit. | Installed Vale and activated writing procedures | No numeric threshold; inspect all three acceptance paths. |
| `visuals` | Render the actual output artifacts and inspect diagrams, tables, labels, layout and source-linked facts. | Generated HTML/SVG/document artifacts and a real rendering/inspection environment | No numeric threshold; inspect all three acceptance paths. |
| `languages` | Build, test and introduce/reject deliberate regressions in Python, JavaScript/TypeScript and Rust projects. | Python, Node/TypeScript and Rust toolchains with isolated test projects | python_positive_negative_pairs ≥ 1; javascript_positive_negative_pairs ≥ 1; typescript_positive_negative_pairs ≥ 1; rust_positive_negative_pairs ≥ 1 |
| `calibration` | Compare held-out human labels with model judges, report false accepts/rejects and prevent unsupported factual acceptance. | Human-labelled held-out examples and independent judge outputs | held_out_human_labels ≥ 20; independent_judge_runs ≥ 1 |
| `benchmark` | Run held-out routing/model/outcome-finding scenarios and report every miss, latency and resource use without training on the answers. | Qualified profiles and a versioned held-out benchmark with expected outcomes | held_out_cases ≥ 30; unreported_misses ≤ 0 |
| `soak` | Run a 72-hour mixed whole-system soak including workers, faults, restart and storage pressure; retain loss and latency measurements. | Long-running isolated deployment and controlled failure/load injection | duration_seconds ≥ 259200; worker_restarts ≥ 1; lost_prompts ≤ 0; unreconciled_duplicate_effects ≤ 0 |
| `packages` | Build and independently install on supported platforms, verify pinned inputs and record dependency/artifact hashes. | Fresh Linux/macOS environments and build/install tools | independent_linux_installs ≥ 1; independent_macos_installs ≥ 1 |
| `linked_contract` | Read the actual linked project contract, execute compatible request/state/artifact exchanges and verify version mismatch handling. | Accessible linked-project specification and running peer integration | No numeric threshold; inspect all three acceptance paths. |
| `migration` | Back up a customized installation, restore elsewhere, reconcile edits, validate new runtime and retire only identified old software. | Disposable customized legacy installation plus current target evidence; never use real user data as a fixture | No numeric threshold; inspect all three acceptance paths. |
| `isolation` | Attempt unauthorized cross-project/filesystem/network/credential access from actual constrained workers; retain denial and audit evidence. | Configured OS/container isolation and scoped worker credentials | No numeric threshold; inspect all three acceptance paths. |
| `openviking` | Run a real OpenViking backend through project-scoped ingest/search/read/delete and restart; compare provenance with original sources. | Configured OpenViking server and its explicitly selected local/subscription/API model provider | No numeric threshold; inspect all three acceptance paths. |
| `hindsight` | Run a real Hindsight bank through retain/recall/reflect/delete with scoped project mapping and inspect derived assertions. | Configured Hindsight server and its explicitly selected local/subscription/API model provider | No numeric threshold; inspect all three acceptance paths. |
| `temporal` | Execute tasks on the exclusive Temporal backend, interrupt/recover workers and verify signal/cancel/idempotency behavior. | Running Temporal service and registered Workbench workflow/activity worker | No numeric threshold; inspect all three acceptance paths. |
| `multi_host` | Two actual hosts contend for work, exchange scoped evidence, lose connectivity and reconcile ownership without duplicate effects. | Two isolated configured worker hosts and an authenticated coordinator | worker_hosts ≥ 2; injected_disconnects ≥ 1; duplicate_owned_effects ≤ 0 |
| `basic_memory` | Real Basic Memory registers projects, writes/reads/searches notes across processes and rejects stale overwrites/cross-project access. | Installed Basic Memory with isolated project stores | No numeric threshold; inspect all three acceptance paths. |
| `notebook` | Actual Open Notebook source ingestion and Ask produce traceable answers; verify its configured provider/auth profile and failure recovery. | Configured Open Notebook service with an explicitly supported inference provider | No numeric threshold; inspect all three acceptance paths. |
| `researcher` | Actual GPT Researcher investigates authoritative web sources, includes contradictory evidence, and returns a bounded source-linked report. | Installed/configured GPT Researcher with working search and inference providers | No numeric threshold; inspect all three acceptance paths. |

## All 71 features

The count is a count of mapped assertions, not a percentage of functionality implemented. Zero means a direct pytest mapping is still missing; the explicit scenario and observed gates remain required.

| Feature | Capability | Mapped pytest cases | Observed gates |
|---|---|---:|---|
| [PF01](#pf01) | Concurrent prompt inbox; submit while work runs | 4 | `journey`, `workers` |
| [PF02](#pf02) | Durable original prompts and receipts | 5 | `journey` |
| [PF03](#pf03) | Context-aware project routing | 19 | `journey`, `benchmark` |
| [PF04](#pf04) | Compatible request grouping without lost identities | 10 | `journey` |
| [PF05](#pf05) | Multiple deliverables tracked separately | 8 | `journey`, `workers` |
| [PF06](#pf06) | New projects and historical reopening | 6 | `journey` |
| [PF07](#pf07) | Explainable/correctable routing and bounded clarification | 4 | `journey` |
| [PF08](#pf08) | Ordered amendments and retry identity | 8 | `journey` |
| [PF09](#pf09) | Durable project portfolio and priorities | 2 | `journey`, `capacity` |
| [PF10](#pf10) | Scoped cross-project dependencies | 8 | `journey`, `isolation` |
| [PF11](#pf11) | Versioned requirements and canonical plans | 14 | `journey`, `coordinator` |
| [PF12](#pf12) | Fair scheduling and interactive capacity | 4 | `capacity`, `coordinator` |
| [PF13](#pf13) | Recoverable peer agent identities | 2 | `coordinator`, `workers` |
| [PF14](#pf14) | Role/team templates | 3 | `coordinator`, `workers` |
| [PF15](#pf15) | Peer versus child ownership and limits | 1 | `coordinator`, `workers` |
| [PF16](#pf16) | Durable agent communication with receipts | 2 | `journey`, `workers` |
| [PF17](#pf17) | Independent review and bounded repair | 23 | `workers`, `writing` |
| [PF18](#pf18) | Harness/model/effort capability profiles | 11 | `models` |
| [PF19](#pf19) | Captured permitted conversation history | 13 | `lcm` |
| [PF20](#pf20) | Original source archive and anchors | 8 | `corpus` |
| [PF21](#pf21) | Expandable summaries linked to originals | 15 | `summaries`, `lcm` |
| [PF22](#pf22) | QMD exact/semantic scoped retrieval | 10 | `qmd_exact`, `qmd_semantic` |
| [PF23](#pf23) | Whole-corpus coverage accounting | 1 | `corpus` |
| [PF24](#pf24) | Facts, decisions, contradictions and supersession | 6 | `corpus`, `writing` |
| [PF25](#pf25) | Bounded current-context assembly | 5 | `corpus`, `recovery` |
| [PF26](#pf26) | Export, invalidation, retention and deletion | 11 | `retention` |
| [PF27](#pf27) | One replaceable runtime backend | 15 | `coordinator` |
| [PF28](#pf28) | Real readiness and capability health | 16 | `models`, `coordinator` |
| [PF29](#pf29) | Detached parallel execution | 4 | `workers`, `gui` |
| [PF30](#pf30) | Ralph-style fresh-context continuation | 3 | `recovery` |
| [PF31](#pf31) | Bounded iteration and no-progress detection | 3 | `recovery` |
| [PF32](#pf32) | Crash and uncertain-effect reconciliation | 8 | `coordinator`, `recovery` |
| [PF33](#pf33) | Scoped pause/cancel including shared work | 12 | `workers`, `coordinator` |
| [PF34](#pf34) | Isolated workspaces and shared resource limits | 12 | `isolation`, `capacity` |
| [PF35](#pf35) | Native WezTerm navigation | 5 | `gui` |
| [PF36](#pf36) | Persistent requests and obligation progress | 10 | `journey`, `gui` |
| [PF37](#pf37) | Dedicated Outcomes view | 5 | `gui`, `workers` |
| [PF38](#pf38) | Versioned result cards, checks and limitations | 27 | `journey`, `gui` |
| [PF39](#pf39) | Search, pin, read, reopen and export outcomes | 18 | `journey`, `gui` |
| [PF40](#pf40) | Team/agent/child views and meaningful updates | 2 | `workers`, `gui` |
| [PF41](#pf41) | Dependencies, reservations and usage visibility | 14 | `capacity`, `gui` |
| [PF42](#pf42) | Context health and attention inbox | 5 | `journey`, `gui` |
| [PF43](#pf43) | Diagrams, charts and artifact previews | 5 | `visuals`, `gui` |
| [PF44](#pf44) | Safe, legible, recoverable diagnostics UI | 9 | `gui` |
| [PF45](#pf45) | Superpowers evidence-led brainstorming | 1 | `procedures`, `brainstorm` |
| [PF46](#pf46) | Readable plans and executable packets from one model | 2 | `workers`, `writing` |
| [PF47](#pf47) | Versioned skill activation and resume checks | 1 | `procedures`, `recovery` |
| [PF48](#pf48) | Audience and approved writing profile | 2 | `writing` |
| [PF49](#pf49) | Writing Clearly and Concisely, Humanizer and Vale | 3 | `vale`, `writing` |
| [PF50](#pf50) | Rendered, inspected visual documents | 13 | `visuals` |
| [PF51](#pf51) | Python, TypeScript/JavaScript and Rust execution packs | 14 | `languages`, `workers` |
| [PF52](#pf52) | Evidence-based evaluation and human calibration | 1 | `calibration` |
| [PF53](#pf53) | Routing, selection and outcome-finding evaluation | 19 | `benchmark` |
| [PF54](#pf54) | Fault, capacity and soak verification | 13 | `capacity`, `soak` |
| [PF55](#pf55) | Reviewed improvements with rollback | 2 | `journey`, `benchmark` |
| [PF56](#pf56) | One GitHub repository and reproducible packages | 1 | `packages` |
| [PF57](#pf57) | Harness and linked-project contracts | 27 | `linked_contract`, `models` |
| [PF58](#pf58) | Doctor, migration, backup, restore and uninstall | 32 | `migration` |
| [PF59](#pf59) | Authorization, credentials and effect audit | 97 | `isolation` |
| [PF60](#pf60) | Authorized schedules and clear updates | 1 | `journey` |
| [PF61](#pf61) | Optional OpenViking replacement context backend | 40 | `openviking`, `retention` |
| [PF62](#pf62) | Optional Hindsight experience memory | 30 | `hindsight`, `retention` |
| [PF63](#pf63) | Optional Temporal execution backend | 21 | `temporal`, `workers` |
| [PF64](#pf64) | Optional multi-host and larger fleets | 10 | `multi_host`, `isolation`, `capacity` |
| [PF65](#pf65) | Basic Memory editable knowledge and recall | 16 | `basic_memory`, `retention` |
| [PF66](#pf66) | Open Notebook source-workspace integration | 1 | `notebook`, `writing` |
| [PF67](#pf67) | GPT Researcher bounded investigation integration | 1 | `researcher`, `writing` |
| [PF68](#pf68) | Automatic request-to-team planning | 20 | `workers`, `benchmark` |
| [PF69](#pf69) | Evaluated model and effort selection with explanation | 11 | `models`, `benchmark` |
| [PF70](#pf70) | Capability registry and workflow compilation | 45 | `procedures`, `coordinator` |
| [PF71](#pf71) | Subscription, API and tested local/hybrid installation profiles | 28 | `models`, `packages`, `workers` |

## Per-feature scenarios

Each mapping below points to collected pytest node IDs. Parameterized variants are listed individually. Tests often cover only part of the scenario; read the automated scope before drawing conclusions. Reuse of a test across related features does not create independent observations.

### PF01

**Concurrent prompt inbox; submit while work runs** — `E-PF01`

Submit website, mobile and research requests while workers are active.

- **Successful path:** All prompts receive unique durable receipts; independent projects remain responsive.
- **Failure path:** Retry the same key concurrently; one request is created.
- **Edge case:** A slow or paused project does not block intake for another project.
- **Automated scope:** Concurrent local submissions and fixture dispatch admission; not actual worker overlap.
- **Remaining limitation:** Live worker interaction remains a separate gate.
- **Required observations:** `journey`, `workers`.
- **Prerequisites:** Installed Workbench and isolated website/mobile/research project state; Authenticated model harness and an actual coordinator-owned worker run.
- **Workload tags:** `website`, `mobile`, `research`, `load`.
- **Workload instructions:** `S01`, `S02`, `S03`, `S25`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_intake.py::test_concurrent_unique_and_retry_receipts`
- `tests/test_execution.py::test_paused_project_does_not_block_other_projects`
- `tests/test_requirement_scenarios.py::test_parallel_group_member_amendments_preserve_both_updates`
- `tests/test_scenario_runner.py::test_thirty_instructions_persist_real_state_without_fake_products`

### PF02

**Durable original prompts and receipts** — `E-PF02`

Restart the process after submitting and amending a request.

- **Successful path:** Original prompt bytes and all receipt identities are recovered.
- **Failure path:** Reuse a client key with changed text; reject without mutating the saved request.
- **Edge case:** Identical text with a new key remains a new intentional request.
- **Automated scope:** Actual local restarts and idempotent storage; no model comprehension claim.
- **Remaining limitation:** Live worker interaction remains a separate gate.
- **Required observations:** `journey`.
- **Prerequisites:** Installed Workbench and isolated website/mobile/research project state.
- **Workload tags:** `portfolio`.
- **Workload instructions:** `S01`, `S02`, `S03`, `S15`, `S16`, `S17`, `S20`, `S28`, `S29`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_boundaries.py::test_original_prompt_history_survives_fresh_application`
- `tests/test_intake.py::test_retry_conflict_and_intentional_repeat`
- `tests/test_surfaces.py::test_cli_exit_codes_and_restart`
- `tests/test_requirement_scenarios.py::test_retrying_grouped_amendment_does_not_increment_parent_twice`
- `tests/test_scenario_runner.py::test_thirty_instructions_persist_real_state_without_fake_products`

### PF03

**Context-aware project routing** — `E-PF03`

Route initial requests and paraphrased follow-ups to the right existing or new project.

- **Successful path:** Website/mobile/research follow-ups retain the intended project and explain the choice.
- **Failure path:** An ambiguous reference produces a visible assignment need instead of a guessed merge.
- **Edge case:** A reply mentioning another project cannot silently move ownership.
- **Automated scope:** Explicit IDs, replies and exact-name routing only; semantic accuracy requires the benchmark.
- **Remaining limitation:** Routing uses explicit links and exact names; semantic/historical disambiguation needs evaluated planning.
- **Required observations:** `journey`, `benchmark`.
- **Prerequisites:** Installed Workbench and isolated website/mobile/research project state; Qualified profiles and a versioned held-out benchmark with expected outcomes.
- **Workload tags:** `website`, `mobile`, `research`, `routing`.
- **Workload instructions:** `S10`, `S11`, `S22`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_intake.py::test_routing_ambiguity_does_not_block_other_work`
- `tests/test_intake.py::test_reply_cannot_move_project`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Create a website for HarborWeb with accessible event booking.-code]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Create a mobile app for PocketTasks with offline habits.-code]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Report the architecture review.-report]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Add dark mode to the website.-code]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Research offline-first mobile architecture and provide a report.-research]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Write code that produces a report.-code]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Create a plan for building a website.-design]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Please summarize the research about code review.-report]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Fix the failing test without changing the research report.-code]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Compare two designs before implementing either.-research]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Design the architecture for a mobile app.-design]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Create a report from the architecture decisions.-report]`
- `tests/test_policy_scenarios.py::test_manifest_context_prefix_keeps_requested_work[S10-code]`
- `tests/test_policy_scenarios.py::test_manifest_context_prefix_keeps_requested_work[S11-code]`
- `tests/test_policy_scenarios.py::test_manifest_context_prefix_keeps_requested_work[S16-code]`
- `tests/test_policy_scenarios.py::test_manifest_context_prefix_keeps_requested_work[S17-code]`
- `tests/test_policy_scenarios.py::test_manifest_context_prefix_keeps_requested_work[S25-report]`

### PF04

**Compatible request grouping without lost identities** — `E-PF04`

Group compatible website additions while keeping their original requests.

- **Successful path:** One parent plan retains each original prompt and separate obligation.
- **Failure path:** Reject a cross-project or already-dispatched grouping.
- **Edge case:** Withdraw one grouped demand; other obligations and history survive.
- **Automated scope:** Local grouping and fixture cancellation; compatibility is explicitly reviewed.
- **Remaining limitation:** Grouping is explicitly reviewed; arbitrary prompts are not automatically decomposed.
- **Required observations:** `journey`.
- **Prerequisites:** Installed Workbench and isolated website/mobile/research project state.
- **Workload tags:** `website`, `grouping`.
- **Workload instructions:** `S12`, `S13`, `S14`, `S18`, `S19`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_intake.py::test_group_preserves_requests_and_creates_obligations`
- `tests/test_intake.py::test_cross_project_group_rejected`
- `tests/test_boundaries.py::test_group_withdrawal_preserves_surviving_demand`
- `tests/test_requirement_scenarios.py::test_grouping_preserves_each_explicit_acceptance_criterion`
- `tests/test_requirement_scenarios.py::test_grouped_child_amendment_updates_parent_without_duplicate_dispatch`
- `tests/test_requirement_scenarios.py::test_group_withdrawal_keeps_custom_parent_and_survivor_criteria`
- `tests/test_requirement_scenarios.py::test_reopened_member_can_be_withdrawn_without_leaking_its_old_requirements`
- `tests/test_requirement_scenarios.py::test_nested_groups_are_rejected_before_any_request_is_changed`
- `tests/test_requirement_scenarios.py::test_last_group_withdrawal_cannot_reopen_a_blank_scope`
- `tests/test_scenario_runner.py::test_conflicting_requirements_group_identity_and_stale_rejection_are_observed`

### PF05

**Multiple deliverables tracked separately** — `E-PF05`

Request website code, tests and a brief release note as distinct deliverables.

- **Successful path:** Track and review each obligation against the current request revision.
- **Failure path:** Reject acceptance when any required deliverable is missing.
- **Edge case:** An amendment preserves still-required deliverables and invalidates stale fulfillment.
- **Automated scope:** Explicit obligation acceptance checks; free-text decomposition is not established.
- **Remaining limitation:** Grouping is explicitly reviewed; arbitrary prompts are not automatically decomposed.
- **Required observations:** `journey`, `workers`.
- **Prerequisites:** Installed Workbench and isolated website/mobile/research project state; Authenticated model harness and an actual coordinator-owned worker run.
- **Workload tags:** `website`, `mobile`, `amendment`.
- **Workload instructions:** `S01`, `S02`, `S04`, `S05`, `S10`, `S11`, `S12`, `S14`, `S16`, `S17`, `S18`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_context_quality.py::test_obligations_gate_acceptance`
- `tests/test_requirement_scenarios.py::test_amendments_carry_unfulfilled_deliverables_to_each_revision`
- `tests/test_requirement_scenarios.py::test_separate_deliverables_can_be_reviewed_without_premature_completion`
- `tests/test_requirement_scenarios.py::test_accepted_deliverable_cannot_be_reassigned_while_others_remain`
- `tests/test_scenario_runner.py::test_conflicting_requirements_group_identity_and_stale_rejection_are_observed`
- `tests/test_report_deliverables.py::test_export_all_separately_accepted_deliverables_with_provenance`
- `tests/test_report_deliverables.py::test_partial_export_does_not_replace_accepted_work_with_latest_rejected_draft`
- `tests/test_report_deliverables.py::test_one_outcome_can_cover_multiple_deliverables_without_duplicate_body`

### PF06

**New projects and historical reopening** — `E-PF06`

Create three projects, close a request, then reopen its history.

- **Successful path:** The new request references retained content and has a fresh identity.
- **Failure path:** Reject silent amendment of a closed request.
- **Edge case:** Restart the app before reopening; history and project scope remain intact.
- **Automated scope:** Local project persistence and reopen behavior.
- **Remaining limitation:** Live worker interaction remains a separate gate.
- **Required observations:** `journey`.
- **Prerequisites:** Installed Workbench and isolated website/mobile/research project state.
- **Workload tags:** `website`, `mobile`, `research`, `reopen`.
- **Workload instructions:** `S15`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_intake.py::test_cancel_before_dispatch_and_reopen_preserves_history`
- `tests/test_boundaries.py::test_original_prompt_history_survives_fresh_application`
- `tests/test_requirement_scenarios.py::test_reopen_preserves_deliverables_and_links_historical_request`
- `tests/test_requirement_scenarios.py::test_last_group_withdrawal_cannot_reopen_a_blank_scope`
- `tests/test_tui_scenarios.py::test_tui_new_external_project_and_older_request_search`
- `tests/test_scenario_runner.py::test_conflicting_requirements_group_identity_and_stale_rejection_are_observed`

### PF07

**Explainable/correctable routing and bounded clarification** — `E-PF07`

Submit an ambiguous follow-up, inspect its explanation and correct the project.

- **Successful path:** The prompt remains saved and correction queues only the selected project.
- **Failure path:** Unresolved routing never silently chooses a project.
- **Edge case:** Other prompts continue while one clarification is pending.
- **Automated scope:** Explicit needs-project state and assignment correction.
- **Remaining limitation:** Live worker interaction remains a separate gate.
- **Required observations:** `journey`.
- **Prerequisites:** Installed Workbench and isolated website/mobile/research project state.
- **Workload tags:** `routing`, `amendment`.
- **Workload instructions:** `S22`, `S23`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_intake.py::test_routing_ambiguity_does_not_block_other_work`
- `tests/test_requirement_scenarios.py::test_unassigned_amendment_still_requires_project_assignment`
- `tests/test_tui_scenarios.py::test_view_filter_does_not_change_prompt_destination`
- `tests/test_scenario_runner.py::test_conflicting_requirements_group_identity_and_stale_rejection_are_observed`

### PF08

**Ordered amendments and retry identity** — `E-PF08`

Send concurrent changes to one website request using revision checks.

- **Successful path:** One amendment advances the revision and retains its original text.
- **Failure path:** Reject a stale competing amendment and changed payload under the same retry key.
- **Edge case:** A successful retry returns its original receipt without a second revision.
- **Automated scope:** Atomic revision checks and prompt identity.
- **Remaining limitation:** Live worker interaction remains a separate gate.
- **Required observations:** `journey`.
- **Prerequisites:** Installed Workbench and isolated website/mobile/research project state.
- **Workload tags:** `website`, `mobile`, `amendment`.
- **Workload instructions:** `S04`, `S05`, `S06`, `S07`, `S08`, `S09`, `S13`, `S19`, `S20`, `S21`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_intake.py::test_revision_cas_and_originals`
- `tests/test_intake.py::test_retry_conflict_and_intentional_repeat`
- `tests/test_requirement_scenarios.py::test_amendments_carry_unfulfilled_deliverables_to_each_revision`
- `tests/test_requirement_scenarios.py::test_retrying_grouped_amendment_does_not_increment_parent_twice`
- `tests/test_requirement_scenarios.py::test_parallel_group_member_amendments_preserve_both_updates`
- `tests/test_requirement_scenarios.py::test_group_amendment_after_parent_acceptance_is_rejected_without_writes`
- `tests/test_tui_scenarios.py::test_tui_stale_amendment_is_rejected_without_clobbering`
- `tests/test_scenario_runner.py::test_conflicting_requirements_group_identity_and_stale_rejection_are_observed`

### PF09

**Durable project portfolio and priorities** — `E-PF09`

Run three projects with different priorities and pause one.

- **Successful path:** The portfolio persists priorities and the eligible project can progress.
- **Failure path:** A paused project cannot start new work.
- **Edge case:** Resume after restart without losing queued requests or starvation observations.
- **Automated scope:** Pause isolation is covered; fairness and durable priority ordering need additional evidence.
- **Remaining limitation:** Grouping is explicitly reviewed; arbitrary prompts are not automatically decomposed.
- **Required observations:** `journey`, `capacity`.
- **Prerequisites:** Installed Workbench and isolated website/mobile/research project state; Running coordinator/worker pool and a bounded load generator.
- **Workload tags:** `portfolio`, `load`.
- **Workload instructions:** `S10`, `S11`, `S16`, `S17`, `S24`, `S25`, `S26`, `S29`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_execution.py::test_paused_project_does_not_block_other_projects`
- `tests/test_scenario_runner.py::test_thirty_instructions_persist_real_state_without_fake_products`

### PF10

**Scoped cross-project dependencies** — `E-PF10`

Allow the website project to read only an approved research source.

- **Successful path:** The scoped grant permits that source with provenance.
- **Failure path:** Without a grant, cross-project source reads and grouping are rejected.
- **Edge case:** Revoking access or superseding the dependency prevents use of stale evidence.
- **Automated scope:** Local source grants and request ownership checks; not hostile-worker isolation.
- **Remaining limitation:** Read grants exist; cross-project execution and hostile-worker isolation need stronger enforcement.
- **Required observations:** `journey`, `isolation`.
- **Prerequisites:** Installed Workbench and isolated website/mobile/research project state; Configured OS/container isolation and scoped worker credentials.
- **Workload tags:** `website`, `research`, `scope`.
- **Workload instructions:** `S22`, `S23`, `S27`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_context_quality.py::test_scope_originals_and_corruption`
- `tests/test_intake.py::test_cross_project_group_rejected`
- `tests/test_intake.py::test_reply_cannot_move_project`
- `tests/test_scenario_runner.py::test_scope_and_context_budget_checks_do_not_claim_execution_rollover`
- `tests/test_server_workers.py::test_http_worker_scope_hides_other_hosts_projects_and_lease_tokens`
- `tests/test_report_deliverables.py::test_unavailable_evidence_never_exports_as_accepted[scope]`
- `tests/test_report_deliverables.py::test_unavailable_evidence_never_exports_as_accepted[archive]`
- `tests/test_report_deliverables.py::test_export_scope_excludes_unrelated_outcomes_and_cross_project_link`

### PF11

**Versioned requirements and canonical plans** — `E-PF11`

Compile a plan, amend requirements and resume execution.

- **Successful path:** Plan identity and revision match the current request and preserve approved requirements.
- **Failure path:** Reject stale plans, cycles and missing dependencies.
- **Edge case:** An amendment during dispatch invalidates superseded work and results.
- **Automated scope:** Local revision and graph checks plus a coordinator fixture.
- **Remaining limitation:** Live worker interaction remains a separate gate.
- **Required observations:** `journey`, `coordinator`.
- **Prerequisites:** Installed Workbench and isolated website/mobile/research project state; Running configured Paperclip or exclusive qualified replacement coordinator.
- **Workload tags:** `website`, `mobile`, `amendment`.
- **Workload instructions:** `S04`, `S05`, `S07`, `S08`, `S12`, `S13`, `S18`, `S19`, `S21`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_intake.py::test_revision_cas_and_originals`
- `tests/test_execution.py::test_plan_cycles_and_child_limits`
- `tests/test_execution.py::test_cancellation_during_remote_mutation_cancels_new_issue`
- `tests/test_requirement_scenarios.py::test_amendments_carry_unfulfilled_deliverables_to_each_revision`
- `tests/test_requirement_scenarios.py::test_grouped_child_amendment_updates_parent_without_duplicate_dispatch`
- `tests/test_scenario_runner.py::test_conflicting_requirements_group_identity_and_stale_rejection_are_observed`
- `tests/test_engine_preflight.py::test_saved_plan_is_validated_again_before_remote_mutation[cycle]`
- `tests/test_engine_preflight.py::test_saved_plan_is_validated_again_before_remote_mutation[missing_dependency]`
- `tests/test_engine_preflight.py::test_saved_plan_is_validated_again_before_remote_mutation[wrong_revision]`
- `tests/test_engine_preflight.py::test_saved_plan_is_validated_again_before_remote_mutation[missing_role]`
- `tests/test_engine_preflight.py::test_saved_plan_is_validated_again_before_remote_mutation[budget_escalation]`
- `tests/test_engine_preflight.py::test_saved_plan_is_validated_again_before_remote_mutation[skill_hash]`
- `tests/test_engine_preflight.py::test_saved_acyclic_nodes_are_dispatched_in_dependency_order`
- `tests/test_engine_preflight.py::test_amendment_during_preflight_prevents_remote_mutations`

### PF12

**Fair scheduling and interactive capacity** — `E-PF12`

Mix interactive prompts with a backlog and a slow project.

- **Successful path:** Admission respects configured capacity and records wait time per project.
- **Failure path:** Reject over-capacity work without losing its queue entry.
- **Edge case:** Priorities age so low-priority work progresses after a finite bounded wait.
- **Automated scope:** Fixture pause/claim controls only; reservations and starvation measurements require load evidence.
- **Remaining limitation:** Priority aging/pause implemented; reserved interactive capacity and fleet load qualification remain.
- **Required observations:** `capacity`, `coordinator`.
- **Prerequisites:** Running coordinator/worker pool and a bounded load generator; Running configured Paperclip or exclusive qualified replacement coordinator.
- **Workload tags:** `load`.
- **Workload instructions:** `S24`, `S26`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_execution.py::test_paused_project_does_not_block_other_projects`
- `tests/test_execution.py::test_concurrent_bridge_calls_claim_once`
- `tests/test_runtime_backends.py::test_fleet_capacity_reservation_and_idempotency`
- `tests/test_runtime_backends.py::test_fleet_expired_lease_keeps_capacity_until_stop`

### PF13

**Recoverable peer agent identities** — `E-PF13`

Restart the bridge during agent creation and replay delivery.

- **Successful path:** Existing peer identities are reused after reconciliation.
- **Failure path:** A lost creation response cannot create a duplicate agent.
- **Edge case:** Missing or terminated cached agents remain visible for reconciliation.
- **Automated scope:** Fixture idempotency and uncertain creation handling.
- **Remaining limitation:** Real coordinator/harness conformance, native child ownership and broad selection evaluation remain.
- **Required observations:** `coordinator`, `workers`.
- **Prerequisites:** Running configured Paperclip or exclusive qualified replacement coordinator; Authenticated model harness and an actual coordinator-owned worker run.
- **Workload tags:** `recovery`.

Mapped pytest evidence:

- `tests/test_execution.py::test_bridge_replay_is_idempotent`
- `tests/test_execution.py::test_uncertain_agent_create_does_not_duplicate`

### PF14

**Role/team templates** — `E-PF14`

Ask for code, a design and a two-source research report.

- **Successful path:** Each task receives the documented roles and dependency order.
- **Failure path:** An unqualified profile blocks team creation rather than claiming a running team.
- **Edge case:** Two research peers have distinct identities, followed by synthesis and review.
- **Automated scope:** Fixture role/dependency plans; no real role performance proof.
- **Remaining limitation:** Templates and official CLI probes implemented; actual model/profile qualification must run on target.
- **Required observations:** `coordinator`, `workers`.
- **Prerequisites:** Running configured Paperclip or exclusive qualified replacement coordinator; Authenticated model harness and an actual coordinator-owned worker run.
- **Workload tags:** `website`, `mobile`, `research`.
- **Workload instructions:** `S01`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_execution.py::test_parallel_team_has_distinct_peers_and_review_dependencies`
- `tests/test_execution.py::test_model_override_and_missing_qualification`
- `tests/test_policy_scenarios.py::test_website_and_mobile_use_engineer_and_reviewer`

### PF15

**Peer versus child ownership and limits** — `E-PF15`

Create peers and nested child tasks up to the configured depth.

- **Successful path:** Parent/child lifetime and ownership remain explicit and recoverable.
- **Failure path:** Reject cyclic ownership, unknown parents and excessive depth.
- **Edge case:** Cancelling a parent stops its children without stopping an unrelated peer.
- **Automated scope:** The existing test verifies a dependency cycle, not all native child ownership behavior.
- **Remaining limitation:** Real coordinator/harness conformance, native child ownership and broad selection evaluation remain.
- **Required observations:** `coordinator`, `workers`.
- **Prerequisites:** Running configured Paperclip or exclusive qualified replacement coordinator; Authenticated model harness and an actual coordinator-owned worker run.
- **Workload tags:** `teams`, `cancellation`.

Mapped pytest evidence:

- `tests/test_execution.py::test_plan_cycles_and_child_limits`

### PF16

**Durable agent communication with receipts** — `E-PF16`

Have researchers send evidence to a reviewer and restart both sides.

- **Successful path:** Distinct delivered/read/acted receipts are retained in order.
- **Failure path:** Reject a receipt or acknowledgement from another recipient.
- **Edge case:** Replay the same message key once and apply backpressure to a full mailbox.
- **Automated scope:** Local mailbox/receipt semantics; actual peer exchange and saturation need execution evidence.
- **Remaining limitation:** Actual peer-agent exchange and backpressure qualification remain.
- **Required observations:** `journey`, `workers`.
- **Prerequisites:** Installed Workbench and isolated website/mobile/research project state; Authenticated model harness and an actual coordinator-owned worker run.
- **Workload tags:** `research`, `teams`.

Mapped pytest evidence:

- `tests/test_intake.py::test_mail_replay_and_recipient_check`
- `tests/test_boundaries.py::test_message_receipts_have_distinct_ordered_meanings`

### PF17

**Independent review and bounded repair** — `E-PF17`

Have a reviewer reject an incomplete artifact, then request a bounded repair.

- **Successful path:** A distinct reviewer accepts only the current complete deliverables.
- **Failure path:** The author cannot self-approve; stale or fabricated evidence is rejected.
- **Edge case:** Repeated unchanged repairs hit the no-progress limit.
- **Automated scope:** Local identity, citation and no-progress checks; evaluator identity is not proof of independent reasoning.
- **Remaining limitation:** Real coordinator/harness conformance, native child ownership and broad selection evaluation remain.
- **Required observations:** `workers`, `writing`.
- **Prerequisites:** Authenticated model harness and an actual coordinator-owned worker run; Known-answer source corpus and independent editorial review rubric.
- **Workload tags:** `website`, `mobile`, `research`, `review`.
- **Workload instructions:** `S30`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_context_quality.py::test_grounded_outcome_review_and_revocation`
- `tests/test_context_quality.py::test_fabricated_quote_and_stale_revision_rejected`
- `tests/test_execution.py::test_checkpoint_generation_budget_progress_and_effects`
- `tests/test_requirement_scenarios.py::test_cosmetic_identity_changes_cannot_self_review[author ]`
- `tests/test_requirement_scenarios.py::test_cosmetic_identity_changes_cannot_self_review[ AUTHOR]`
- `tests/test_requirement_scenarios.py::test_cosmetic_identity_changes_cannot_self_review[\tAuthor\n]`
- `tests/test_requirement_scenarios.py::test_competing_outcomes_cannot_both_close_one_request`
- `tests/test_requirement_scenarios.py::test_proposal_cannot_reopen_request_accepted_during_source_validation`
- `tests/test_execution_packs.py::test_research_citations_require_real_scoped_sources_and_remain_reviewable`
- `tests/test_execution_packs.py::test_mobile_source_and_fake_archive_never_certify_a_working_app`
- `tests/test_scenario_runner.py::test_fixture_accepted_text_cannot_certify_live_delivery`
- `tests/test_runtime_observations.py::test_runtime_observation_proposes_once_never_accepts`
- `tests/test_runtime_observations.py::test_stale_runtime_observation_does_not_publish`
- `tests/test_runtime_observations.py::test_invalid_result_never_becomes_outcome[identity]`
- `tests/test_runtime_observations.py::test_invalid_result_never_becomes_outcome[missing]`
- `tests/test_runtime_observations.py::test_invalid_result_never_becomes_outcome[exit]`
- `tests/test_runtime_observations.py::test_invalid_result_never_becomes_outcome[role]`
- `tests/test_runtime_observations.py::test_invalid_result_never_becomes_outcome[empty]`
- `tests/test_runtime_observations.py::test_changed_runtime_output_rejected`
- `tests/test_server_workers.py::test_http_completed_worker_receipts_need_observation_and_independent_review`
- `tests/test_report_deliverables.py::test_export_all_separately_accepted_deliverables_with_provenance`
- `tests/test_report_deliverables.py::test_partial_export_does_not_replace_accepted_work_with_latest_rejected_draft`
- `tests/test_report_deliverables.py::test_one_outcome_can_cover_multiple_deliverables_without_duplicate_body`

### PF18

**Harness/model/effort capability profiles** — `E-PF18`

Run explicit Claude/Codex/local model and effort profiles.

- **Successful path:** Selection records the configured qualified profile and effective harness settings.
- **Failure path:** Missing/expired or edited qualification cannot silently qualify a model.
- **Edge case:** An unsupported effort or explicit unavailable model produces a clear blocker.
- **Automated scope:** Profile validation and fixture adapter-field mapping; actual provider settings require a real probe.
- **Remaining limitation:** Templates and official CLI probes implemented; actual model/profile qualification must run on target.
- **Required observations:** `models`.
- **Prerequisites:** Signed-in official CLI or explicitly configured API/local model provider.
- **Workload tags:** `models`.

Mapped pytest evidence:

- `tests/test_execution.py::test_model_override_and_missing_qualification`
- `tests/test_execution.py::test_effort_maps_to_pinned_codex_field`
- `tests/test_boundaries.py::test_profile_change_invalidates_qualification`
- `tests/test_harness_surfaces.py::test_failed_reprobe_revokes_old_qualification`
- `tests/test_harness_surfaces.py::test_probe_requires_exact_successful_assistant_completion[echo]`
- `tests/test_harness_surfaces.py::test_probe_requires_exact_successful_assistant_completion[refusal]`
- `tests/test_harness_surfaces.py::test_probe_requires_exact_successful_assistant_completion[incomplete]`
- `tests/test_harness_surfaces.py::test_probe_requires_exact_successful_assistant_completion[failed_turn]`
- `tests/test_harness_surfaces.py::test_probe_requires_exact_successful_assistant_completion[success]`
- `tests/test_engine_preflight.py::test_missing_qualification_is_rejected_before_creating_remote_project`
- `tests/test_engine_preflight.py::test_saved_profile_qualification_is_rechecked_when_receipt_expires`
- `tests/test_harness_surfaces.py::test_probe_records_effective_model_reported_by_cli[claude-sonnet-5-5-usage0-effective0]`
- `tests/test_harness_surfaces.py::test_probe_records_effective_model_reported_by_cli[claude-opus-5-5-usage1-effective1]`
- `tests/test_harness_surfaces.py::test_probe_records_effective_model_reported_by_cli[sonnet-usage2-effective2]`
- `tests/test_harness_surfaces.py::test_probe_rejects_a_substituted_model[claude-opus-5-5-usage0]`
- `tests/test_harness_surfaces.py::test_probe_rejects_a_substituted_model[opus-usage1]`
- `tests/test_harness_surfaces.py::test_probe_keeps_unreported_model_identity_unknown`

### PF19

**Captured permitted conversation history** — `E-PF19`

Capture permitted conversation turns and recall them in a new session.

- **Successful path:** Original turns retain project, source and timing provenance.
- **Failure path:** A different project cannot recall the captured private turn.
- **Edge case:** Interrupted capture retries without a duplicate turn or silent loss.
- **Automated scope:** Loopback contracts check original archival, project filtering and request validation. Opt-in real pinned LCM tests exercise capture/search, cross-project denial and checksum-guarded merging of Codex hooks while preserving user hooks. Installing a hook does not establish that the actual harness invoked it; summaries remain explicitly disabled in these probes.
- **Remaining limitation:** A permitted capture interface and host-hook installation are covered. Actual harness-hook invocation, interruption recovery and all supported harnesses still need end-to-end observations.
- **Required observations:** `lcm`.
- **Prerequisites:** Pinned running LCM installation with permitted history capture configured.
- **Workload tags:** `research`, `recovery`.

Mapped pytest evidence:

- `tests/test_memory_integration_contracts.py::test_lcm_capture_archives_original_search_filters_scope_and_summary_reports_disabled`
- `tests/test_memory_integration_contracts.py::test_lcm_rejects_scope_overrides_and_silently_dropped_messages[expand-args0]`
- `tests/test_memory_integration_contracts.py::test_lcm_rejects_scope_overrides_and_silently_dropped_messages[expand-args1]`
- `tests/test_memory_integration_contracts.py::test_lcm_rejects_scope_overrides_and_silently_dropped_messages[capture-args2]`
- `tests/test_memory_integration_contracts.py::test_lcm_rejects_scope_overrides_and_silently_dropped_messages[capture-args3]`
- `tests/test_memory_integration_contracts.py::test_lcm_rejects_scope_overrides_and_silently_dropped_messages[summarize-args4]`
- `tests/test_memory_integration_contracts.py::test_lcm_success_http_status_cannot_hide_operation_failure[payload0]`
- `tests/test_memory_integration_contracts.py::test_lcm_success_http_status_cannot_hide_operation_failure[payload1]`
- `tests/test_memory_integration_contracts.py::test_lcm_success_http_status_cannot_hide_operation_failure[payload2]`
- `tests/test_memory_integration_contracts.py::test_lcm_success_http_status_cannot_hide_operation_failure[payload3]`
- `tests/test_memory_integration_contracts.py::test_lcm_refuses_missing_shared_or_overlapping_scope`
- `tests/test_memory_integration_contracts.py::test_real_lcm_capture_isolation_and_disabled_summary_are_observed`
- `tests/test_memory_integration_contracts.py::test_real_lcm_connector_merges_private_project_hooks_with_checksum`

### PF20

**Original source archive and anchors** — `E-PF20`

Ingest text and extracted binary sources, then reopen original anchors.

- **Successful path:** Quotes and hashes lead back to the exact original artifact.
- **Failure path:** Corrupting original or extracted text blocks its use.
- **Edge case:** Superseded files retain accessible history and the current source stays explicit.
- **Automated scope:** Local UTF-8 archival integrity and source supersession; binary anchoring needs a mixed corpus.
- **Remaining limitation:** Broader binary extraction and source-anchor fidelity need corpus-specific checks.
- **Required observations:** `corpus`.
- **Prerequisites:** Mixed source corpus with known counts, versions and deliberately contradictory facts.
- **Workload tags:** `research`, `context`.
- **Workload instructions:** `S27`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_context_quality.py::test_scope_originals_and_corruption`
- `tests/test_context_quality.py::test_extracted_text_tampering_is_detected`
- `tests/test_context_quality.py::test_source_supersession_and_packet_qualification`
- `tests/test_context_backends.py::test_backend_round_trip_preserves_original_and_marks_derived[hindsight]`
- `tests/test_context_backends.py::test_backend_round_trip_preserves_original_and_marks_derived[openviking]`
- `tests/test_context_backends.py::test_backend_readback_detects_remote_content_changes[hindsight]`
- `tests/test_context_backends.py::test_backend_readback_detects_remote_content_changes[openviking]`
- `tests/test_scenario_runner.py::test_scope_and_context_budget_checks_do_not_claim_execution_rollover`

### PF21

**Expandable summaries linked to originals** — `E-PF21`

Summarize a long conversation and expand a decisive passage later.

- **Successful path:** Expanded originals restore quantities, qualifiers and changed decisions.
- **Failure path:** A summary cannot replace or invent its missing source.
- **Edge case:** Repeated summary layers retain contradictory statements until resolved.
- **Automated scope:** Contracts check scoped summary expansion and explicitly disabled generation; real pinned LCM tests confirm that disabled summarization reports not_generated and no model call. These tests must not be called successful generated-summary or semantic-preservation evaluations.
- **Remaining limitation:** Source-linked summary interfaces exist, but genuine model-generated summaries, repeated layers and meaning-preservation across fresh sessions remain required observed gates.
- **Required observations:** `summaries`, `lcm`.
- **Prerequisites:** Running context backend and a genuine summarizing model; Pinned running LCM installation with permitted history capture configured.
- **Workload tags:** `research`, `context`, `recovery`.

Mapped pytest evidence:

- `tests/test_memory_integration_contracts.py::test_lcm_capture_archives_original_search_filters_scope_and_summary_reports_disabled`
- `tests/test_memory_integration_contracts.py::test_lcm_rejects_scope_overrides_and_silently_dropped_messages[expand-args0]`
- `tests/test_memory_integration_contracts.py::test_lcm_rejects_scope_overrides_and_silently_dropped_messages[expand-args1]`
- `tests/test_memory_integration_contracts.py::test_lcm_rejects_scope_overrides_and_silently_dropped_messages[capture-args2]`
- `tests/test_memory_integration_contracts.py::test_lcm_rejects_scope_overrides_and_silently_dropped_messages[capture-args3]`
- `tests/test_memory_integration_contracts.py::test_lcm_rejects_scope_overrides_and_silently_dropped_messages[summarize-args4]`
- `tests/test_memory_integration_contracts.py::test_lcm_success_http_status_cannot_hide_operation_failure[payload0]`
- `tests/test_memory_integration_contracts.py::test_lcm_success_http_status_cannot_hide_operation_failure[payload1]`
- `tests/test_memory_integration_contracts.py::test_lcm_success_http_status_cannot_hide_operation_failure[payload2]`
- `tests/test_memory_integration_contracts.py::test_lcm_success_http_status_cannot_hide_operation_failure[payload3]`
- `tests/test_memory_integration_contracts.py::test_lcm_refuses_missing_shared_or_overlapping_scope`
- `tests/test_memory_integration_contracts.py::test_real_lcm_capture_isolation_and_disabled_summary_are_observed`
- `tests/test_memory_integration_contracts.py::test_real_lcm_connector_merges_private_project_hooks_with_checksum`
- `tests/test_memory_integration_contracts.py::test_lcm_summary_contract_marks_derived_output_and_rejects_provider_drift`
- `tests/test_memory_integration_contracts.py::test_lcm_summary_provider_needs_matching_auth_profile`

### PF22

**QMD exact/semantic scoped retrieval** — `E-PF22`

Search exact and paraphrased research queries across project collections.

- **Successful path:** Both modes return relevant passages and stable source references.
- **Failure path:** No result leaks from an ungranted collection.
- **Edge case:** An unavailable semantic model is reported without silently relabelling exact search as semantic.
- **Automated scope:** Contracts distinguish lexical, vector and hybrid commands, require explicit inference activation and reject malformed/cross-project results. An opt-in pinned QMD test performs actual exact and vector retrieval on a small two-document corpus with local embedding models; missing models skip and block the feature.
- **Remaining limitation:** A small real exact/vector retrieval probe is not a whole-corpus relevance benchmark, exhaustive coverage result or hybrid-mode quality evaluation. Held-out paraphrases, larger corpora and retention still require explicit observations.
- **Required observations:** `qmd_exact`, `qmd_semantic`.
- **Prerequisites:** Built pinned QMD with project-bound collections; QMD semantic model and index installed with a held-out query set.
- **Workload tags:** `research`, `context`.

Mapped pytest evidence:

- `tests/test_memory_integration_contracts.py::test_qmd_distinguishes_lexical_vector_and_hybrid_contracts[exact-search]`
- `tests/test_memory_integration_contracts.py::test_qmd_distinguishes_lexical_vector_and_hybrid_contracts[semantic-vsearch]`
- `tests/test_memory_integration_contracts.py::test_qmd_distinguishes_lexical_vector_and_hybrid_contracts[hybrid-query]`
- `tests/test_memory_integration_contracts.py::test_qmd_model_modes_need_explicit_activation_and_unique_scope`
- `tests/test_memory_integration_contracts.py::test_qmd_malformed_and_cross_project_results_fail_closed[payload0]`
- `tests/test_memory_integration_contracts.py::test_qmd_malformed_and_cross_project_results_fail_closed[payload1]`
- `tests/test_memory_integration_contracts.py::test_qmd_malformed_and_cross_project_results_fail_closed[payload2]`
- `tests/test_memory_integration_contracts.py::test_qmd_malformed_and_cross_project_results_fail_closed[payload3]`
- `tests/test_memory_integration_contracts.py::test_qmd_malformed_and_cross_project_results_fail_closed[payload4]`
- `tests/test_memory_integration_contracts.py::test_real_qmd_exact_and_vector_query_use_bound_collection`

### PF23

**Whole-corpus coverage accounting** — `E-PF23`

Inventory a mixed corpus larger than a model window and request complete analysis.

- **Successful path:** Coverage counts every in-scope file and distinguishes extraction from review.
- **Failure path:** Unreadable sources remain failures or explicit exclusions.
- **Edge case:** A partial search result cannot claim whole-corpus completeness.
- **Automated scope:** Small mixed-corpus accounting; large-scale processing requires a recorded corpus run.
- **Remaining limitation:** Very large corpus orchestration, tokenizer-specific budgets and automatic contradiction discovery remain.
- **Required observations:** `corpus`.
- **Prerequisites:** Mixed source corpus with known counts, versions and deliberately contradictory facts.
- **Workload tags:** `research`, `context`.
- **Workload instructions:** `S03`, `S27`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_context_quality.py::test_coverage_does_not_treat_extraction_as_review`

### PF24

**Facts, decisions, contradictions and supersession** — `E-PF24`

Ingest conflicting budgets and a later approved correction.

- **Successful path:** Both claims and their provenance remain visible until explicit supersession.
- **Failure path:** Do not turn the newest unapproved assertion into an authoritative decision.
- **Edge case:** Current retrieval omits superseded versions while history remains accessible.
- **Automated scope:** Explicit assertion conflicts and source versions, not automatic contradiction understanding.
- **Remaining limitation:** Very large corpus orchestration, tokenizer-specific budgets and automatic contradiction discovery remain.
- **Required observations:** `corpus`, `writing`.
- **Prerequisites:** Mixed source corpus with known counts, versions and deliberately contradictory facts; Known-answer source corpus and independent editorial review rubric.
- **Workload tags:** `research`, `contradiction`.
- **Workload instructions:** `S06`, `S07`, `S08`, `S19`, `S25`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_boundaries.py::test_current_assertions_surface_conflicts_without_inventing_resolution`
- `tests/test_context_quality.py::test_source_supersession_and_packet_qualification`
- `tests/test_execution_packs.py::test_research_citations_require_real_scoped_sources_and_remain_reviewable`
- `tests/test_context_backends.py::test_backend_recall_omits_superseded_source_versions[hindsight]`
- `tests/test_context_backends.py::test_backend_recall_omits_superseded_source_versions[openviking]`
- `tests/test_scenario_runner.py::test_conflicting_requirements_group_identity_and_stale_rejection_are_observed`

### PF25

**Bounded current-context assembly** — `E-PF25`

Assemble context near the configured limit with several long sources.

- **Successful path:** The packet fits its budget and preserves complete useful passages and source IDs.
- **Failure path:** An oversized passage or execution packet creates a visible omission/block.
- **Edge case:** Old summaries cannot silently remove a recent amendment or critical negation.
- **Automated scope:** Character-budget checks are covered; model-token limits and real recovery need evidence.
- **Remaining limitation:** Very large corpus orchestration, tokenizer-specific budgets and automatic contradiction discovery remain.
- **Required observations:** `corpus`, `recovery`.
- **Prerequisites:** Mixed source corpus with known counts, versions and deliberately contradictory facts; Actual coordinator, authenticated worker and controlled context/session rollover.
- **Workload tags:** `context`, `recovery`.
- **Workload instructions:** `S27`, `S28`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_context_quality.py::test_oversized_passage_is_visible_omission`
- `tests/test_context_quality.py::test_source_supersession_and_packet_qualification`
- `tests/test_execution.py::test_oversized_packet_is_not_silently_truncated`
- `tests/test_scenario_runner.py::test_scope_and_context_budget_checks_do_not_claim_execution_rollover`
- `tests/test_engine_preflight.py::test_every_node_packet_is_checked_before_any_remote_mutation`

### PF26

**Export, invalidation, retention and deletion** — `E-PF26`

Revoke and delete a source used by several projects and retained indexes.

- **Successful path:** Affected results are invalidated and configured stores acknowledge deletion.
- **Failure path:** A failed external deletion remains pending/blocked, never silently complete.
- **Edge case:** Shared originals survive until the final owner is removed; backup expiry is separately visible.
- **Automated scope:** Active local archive purge/revocation; external-index and backup retention require actual checks.
- **Remaining limitation:** Active archive purge/invalidation exists; external-index deletion and automated backup expiry remain.
- **Required observations:** `retention`.
- **Prerequisites:** Configured real context stores plus disposable backup/index data.
- **Workload tags:** `context`, `deletion`.

Mapped pytest evidence:

- `tests/test_context_quality.py::test_grounded_outcome_review_and_revocation`
- `tests/test_boundaries.py::test_purge_keeps_shared_original_until_last_owner_removed`
- `tests/test_context_backends.py::test_remote_deletion_remains_possible_after_local_purge[hindsight]`
- `tests/test_context_backends.py::test_remote_deletion_remains_possible_after_local_purge[openviking]`
- `tests/test_context_backends.py::test_backend_delete_timeout_needs_reconciliation[hindsight]`
- `tests/test_context_backends.py::test_backend_delete_timeout_needs_reconciliation[openviking]`
- `tests/test_context_backends.py::test_backend_absent_delete_is_idempotent[hindsight]`
- `tests/test_context_backends.py::test_backend_absent_delete_is_idempotent[openviking]`
- `tests/test_report_deliverables.py::test_unavailable_evidence_never_exports_as_accepted[scope]`
- `tests/test_report_deliverables.py::test_unavailable_evidence_never_exports_as_accepted[archive]`
- `tests/test_report_deliverables.py::test_export_scope_excludes_unrelated_outcomes_and_cross_project_link`

### PF27

**One replaceable runtime backend** — `E-PF27`

Select one coordinator backend and restart the application.

- **Successful path:** Only the selected backend owns execution and recovery.
- **Failure path:** Reject an unsupported backend or simultaneous competing owners.
- **Edge case:** A backend outage preserves queued intent without starting a hidden fallback runner.
- **Automated scope:** Runtime selection rejects switching with active or uncertain leases and rejects an inactive backend. This directly checks ownership fences, not interchangeable live model execution on two coordinators.
- **Remaining limitation:** Real coordinator/harness conformance, native child ownership and broad selection evaluation remain.
- **Required observations:** `coordinator`.
- **Prerequisites:** Running configured Paperclip or exclusive qualified replacement coordinator.
- **Workload tags:** `recovery`, `backends`.

Mapped pytest evidence:

- `tests/test_runtime_backends.py::test_runtime_switch_rejects_active_or_uncertain_process`
- `tests/test_server_workers.py::test_background_uses_selected_coordinator_and_recovers_observation_health[paperclip]`
- `tests/test_server_workers.py::test_background_uses_selected_coordinator_and_recovers_observation_health[temporal]`
- `tests/test_engine_preflight.py::test_existing_bridge_cannot_dispatch_after_temporal_is_selected`
- `tests/test_engine_preflight.py::test_direct_delivery_also_checks_the_runtime_fence`
- `tests/test_app_integrations.py::test_coordinator_dispatch_preserves_paperclip_default`
- `tests/test_app_integrations.py::test_temporal_dispatch_routes_health_tick_and_observation_reconciliation`
- `tests/test_app_integrations.py::test_temporal_lifecycle_never_falls_back_to_paperclip`
- `tests/test_app_integrations.py::test_paperclip_lifecycle_keeps_existing_request_contract`
- `tests/test_app_integrations.py::test_dispatch_runtime_switch_is_fenced_by_active_lease[coordinator.tick-args0]`
- `tests/test_app_integrations.py::test_dispatch_runtime_switch_is_fenced_by_active_lease[lifecycle.request-args1]`
- `tests/test_app_integrations.py::test_configuration_requires_exactly_one_coordinator[None]`
- `tests/test_app_integrations.py::test_configuration_requires_exactly_one_coordinator[coordinator1]`
- `tests/test_app_integrations.py::test_configuration_requires_exactly_one_coordinator[coordinator2]`
- `tests/test_app_integrations.py::test_configuration_requires_exactly_one_coordinator[coordinator3]`

### PF28

**Real readiness and capability health** — `E-PF28`

Run doctor against healthy, missing, logged-out and expired components.

- **Successful path:** Readiness distinguishes installed, configured, authenticated and actually observed.
- **Failure path:** A missing service or expired probe cannot be green.
- **Edge case:** Changing the profile invalidates qualification; no secret appears in health output.
- **Automated scope:** Qualification accounting is covered; live service/CLI health is a separate observation.
- **Remaining limitation:** Real coordinator/harness conformance, native child ownership and broad selection evaluation remain.
- **Required observations:** `models`, `coordinator`.
- **Prerequisites:** Signed-in official CLI or explicitly configured API/local model provider; Running configured Paperclip or exclusive qualified replacement coordinator.
- **Workload tags:** `models`, `health`.

Mapped pytest evidence:

- `tests/test_operations.py::test_missing_or_expired_qualification_is_not_a_pass`
- `tests/test_boundaries.py::test_profile_change_invalidates_qualification`
- `tests/test_operations.py::test_pass_requires_evidence`
- `tests/test_harness_surfaces.py::test_failed_reprobe_revokes_old_qualification`
- `tests/test_harness_surfaces.py::test_probe_requires_exact_successful_assistant_completion[echo]`
- `tests/test_harness_surfaces.py::test_probe_requires_exact_successful_assistant_completion[refusal]`
- `tests/test_harness_surfaces.py::test_probe_requires_exact_successful_assistant_completion[incomplete]`
- `tests/test_harness_surfaces.py::test_probe_requires_exact_successful_assistant_completion[failed_turn]`
- `tests/test_harness_surfaces.py::test_probe_requires_exact_successful_assistant_completion[success]`
- `tests/test_server_workers.py::test_background_uses_selected_coordinator_and_recovers_observation_health[paperclip]`
- `tests/test_server_workers.py::test_background_uses_selected_coordinator_and_recovers_observation_health[temporal]`
- `tests/test_runtime_backends.py::test_temporal_missing_sdk_is_actionable`
- `tests/test_runtime_backends.py::test_temporal_missing_worker_poller_blocks_dispatch`
- `tests/test_engine_preflight.py::test_missing_qualification_is_rejected_before_creating_remote_project`
- `tests/test_engine_preflight.py::test_saved_profile_qualification_is_rechecked_when_receipt_expires`
- `tests/test_app_integrations.py::test_temporal_configuration_is_valid_without_sdk_and_health_reports_missing_sdk`

### PF29

**Detached parallel execution** — `E-PF29`

Launch independent website/mobile work, close the terminal and reconnect.

- **Successful path:** Actual workers continue and their distinct run IDs and artifacts are visible on reconnect.
- **Failure path:** A failed worker cannot mark its project complete.
- **Edge case:** Restarting the view neither restarts work nor cancels unrelated workers.
- **Automated scope:** Fixture parallel plan and bridge claims; detached process execution remains live evidence.
- **Remaining limitation:** Real coordinator/harness conformance, native child ownership and broad selection evaluation remain.
- **Required observations:** `workers`, `gui`.
- **Prerequisites:** Authenticated model harness and an actual coordinator-owned worker run; Installed WezTerm and a real graphical display on the target platform.
- **Workload tags:** `website`, `mobile`, `teams`.
- **Workload instructions:** `S29`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_execution.py::test_parallel_team_has_distinct_peers_and_review_dependencies`
- `tests/test_execution.py::test_concurrent_bridge_calls_claim_once`
- `tests/test_server_workers.py::test_background_uses_selected_coordinator_and_recovers_observation_health[paperclip]`
- `tests/test_server_workers.py::test_background_uses_selected_coordinator_and_recovers_observation_health[temporal]`

### PF30

**Ralph-style fresh-context continuation** — `E-PF30`

Drive a long task through five genuine fresh-model sessions.

- **Successful path:** Each successor loads source-backed checkpoints and the current requirement revision.
- **Failure path:** No successor starts before the prior worker stop acknowledgement.
- **Edge case:** Cancel or amend during rollover; the stale successor cannot continue.
- **Automated scope:** Protocol fixtures only; a fixture wakeup is not a real new model context.
- **Remaining limitation:** Protocol/failure tests pass; five genuine fresh-model sessions and hard-context-limit recovery remain.
- **Required observations:** `recovery`.
- **Prerequisites:** Actual coordinator, authenticated worker and controlled context/session rollover.
- **Workload tags:** `recovery`, `amendment`, `cancellation`.
- **Workload instructions:** `S28`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_execution.py::test_rollover_waits_for_stop_receipt_then_requests_fresh_session`
- `tests/test_execution.py::test_rollover_cancelled_request_never_restarts`
- `tests/test_runtime_backends.py::test_temporal_real_service_checkpoint_rollover`

### PF31

**Bounded iteration and no-progress detection** — `E-PF31`

Repeat a repair without artifact progress until the configured bound.

- **Successful path:** The task stops with a useful reason and saved state.
- **Failure path:** Text-only status changes cannot count as artifact progress.
- **Edge case:** Cumulative time/iteration budgets continue across real context sessions.
- **Automated scope:** Local progress/generation rules; real-session cumulative behavior needs observation.
- **Remaining limitation:** Protocol/failure tests pass; five genuine fresh-model sessions and hard-context-limit recovery remain.
- **Required observations:** `recovery`.
- **Prerequisites:** Actual coordinator, authenticated worker and controlled context/session rollover.
- **Workload tags:** `recovery`, `review`.

Mapped pytest evidence:

- `tests/test_execution.py::test_checkpoint_generation_budget_progress_and_effects`
- `tests/test_execution.py::test_checkpoint_cannot_invent_artifact_progress`
- `tests/test_engine_preflight.py::test_plan_can_narrow_enforced_runtime_timeout_without_reusing_broader_agent`

### PF32

**Crash and uncertain-effect reconciliation** — `E-PF32`

Kill the bridge or worker between a side effect and its response.

- **Successful path:** Recovery reconciles receipts before replaying effects.
- **Failure path:** An uncertain create/publish does not run again blindly.
- **Edge case:** Stale generations and changed checkpoint artifacts cannot resume writes.
- **Automated scope:** Selected local and coordinator-fixture uncertain-effect boundaries.
- **Remaining limitation:** Protocol/failure tests pass; five genuine fresh-model sessions and hard-context-limit recovery remain.
- **Required observations:** `coordinator`, `recovery`.
- **Prerequisites:** Running configured Paperclip or exclusive qualified replacement coordinator; Actual coordinator, authenticated worker and controlled context/session rollover.
- **Workload tags:** `recovery`.
- **Workload instructions:** `S20`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_execution.py::test_uncertain_agent_create_does_not_duplicate`
- `tests/test_execution.py::test_checkpoint_generation_budget_progress_and_effects`
- `tests/test_execution.py::test_checkpoint_cannot_invent_artifact_progress`
- `tests/test_runtime_backends.py::test_fleet_expired_lease_keeps_capacity_until_stop`
- `tests/test_runtime_backends.py::test_temporal_real_service_dag_idempotency_recovery`
- `tests/test_runtime_backends.py::test_temporal_real_service_checkpoint_rollover`
- `tests/test_workspace_backup.py::test_backup_restores_artifacts_and_rebinds_workspace_then_executes`
- `tests/test_app_integrations.py::test_retry_works_for_temporal_without_constructing_paperclip`

### PF33

**Scoped pause/cancel including shared work** — `E-PF33`

Cancel one project and one demand in a shared group while work runs.

- **Successful path:** Only affected work stops; surviving obligations remain actionable.
- **Failure path:** A cancellation racing with issue creation cancels the newly created issue too.
- **Edge case:** A cancelled rollover never starts a successor.
- **Automated scope:** Contract cancellation tests; native worker stop confirmation needs real runs.
- **Remaining limitation:** Real coordinator/harness conformance, native child ownership and broad selection evaluation remain.
- **Required observations:** `workers`, `coordinator`.
- **Prerequisites:** Authenticated model harness and an actual coordinator-owned worker run; Running configured Paperclip or exclusive qualified replacement coordinator.
- **Workload tags:** `cancellation`, `grouping`.
- **Workload instructions:** `S14`, `S24`, `S26`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_execution.py::test_cancel_reaches_remote_issues`
- `tests/test_execution.py::test_cancellation_during_remote_mutation_cancels_new_issue`
- `tests/test_boundaries.py::test_group_withdrawal_preserves_surviving_demand`
- `tests/test_execution.py::test_rollover_cancelled_request_never_restarts`
- `tests/test_requirement_scenarios.py::test_cancelled_outcome_cannot_be_fulfilled_or_reviewed`
- `tests/test_tui_scenarios.py::test_tui_deliverables_blockers_and_scoped_pause`
- `tests/test_execution_packs.py::test_running_process_can_be_cancelled_from_another_operator`
- `tests/test_runtime_backends.py::test_fleet_revision_and_cancel_fence_renewal`
- `tests/test_runtime_backends.py::test_temporal_real_service_cancellation_stops_activity`
- `tests/test_runtime_backends.py::test_cli_process_contract_stdin_receipt_and_cancel`
- `tests/test_engine_preflight.py::test_stale_cancel_job_does_not_cancel_current_issues`
- `tests/test_engine_preflight.py::test_scope_change_while_reading_agent_pool_prevents_agent_creation`

### PF34

**Isolated workspaces and shared resource limits** — `E-PF34`

Have two coding agents edit separate project workspaces under capacity limits.

- **Successful path:** Artifacts and temporary ports stay project-scoped and reservations are released.
- **Failure path:** Deny conflicting ownership, excess workers and writes outside the allowed workspace.
- **Edge case:** Crash one worker and reconcile its leases without granting a stale worker ownership.
- **Automated scope:** Fixture requests ask for isolated workspaces; OS enforcement is not verified by that flag. Private workspace copies, symlink/path-escape denial, secret/.git exclusion and stale plan hashes are exercised. A copied directory is not an OS security sandbox. Linux virtual-address exhaustion is exercised; unsupported platforms reject an explicit memory bound before starting project code.
- **Remaining limitation:** Real coordinator/harness conformance, native child ownership and broad selection evaluation remain.
- **Required observations:** `isolation`, `capacity`.
- **Prerequisites:** Configured OS/container isolation and scoped worker credentials; Running coordinator/worker pool and a bounded load generator.
- **Workload tags:** `website`, `mobile`, `load`.

Mapped pytest evidence:

- `tests/test_execution.py::test_parallel_team_has_distinct_peers_and_review_dependencies`
- `tests/test_execution.py::test_concurrent_bridge_calls_claim_once`
- `tests/test_execution_packs.py::test_python_real_pass_fail_and_source_isolation`
- `tests/test_execution_packs.py::test_workspace_rejects_source_symlink_and_excludes_secret_and_git_files`
- `tests/test_execution_packs.py::test_registered_workspace_cannot_be_replaced_by_external_symlink`
- `tests/test_execution_packs.py::test_plan_hash_and_resource_parameters_are_enforced`
- `tests/test_execution_packs.py::test_python_virtual_memory_limit_is_observed`
- `tests/test_execution_packs.py::test_unsupported_memory_bound_never_starts_project_code[darwin]`
- `tests/test_execution_packs.py::test_unsupported_memory_bound_never_starts_project_code[win32]`
- `tests/test_workspace_backup.py::test_backup_rejects_actual_running_process_until_cancelled`
- `tests/test_workspace_backup.py::test_backup_rejects_workspace_path_outside_owned_state`
- `tests/test_app_integrations.py::test_execution_actions_run_real_tests_and_reopen_results`

### PF35

**Native WezTerm navigation** — `E-PF35`

Install the WezTerm module over a customized terminal configuration.

- **Successful path:** The shortcut opens Workbench and navigation/resize remain legible on the target desktop.
- **Failure path:** Reject shortcut/include conflicts without overwriting user settings.
- **Edge case:** Uninstall preserves later theme edits; reconnect/sleep/reboot are verified on the target.
- **Automated scope:** Lua include lifecycle tests only; no native desktop observation implied.
- **Remaining limitation:** Install and inspect the actual Mac WezTerm plugin, conflicts, reconnect and service behavior.
- **Required observations:** `gui`.
- **Prerequisites:** Installed WezTerm and a real graphical display on the target platform.
- **Workload tags:** `terminal`.

Mapped pytest evidence:

- `tests/test_operations.py::test_terminal_include_preserves_theme_and_local_edits`
- `tests/test_operations.py::test_complex_lua_config_is_not_guessed`
- `tests/test_tui_scenarios.py::test_tui_project_amend_cancel_and_export_journey[size0]`
- `tests/test_tui_scenarios.py::test_tui_project_amend_cancel_and_export_journey[size1]`
- `tests/test_tui_scenarios.py::test_tui_project_amend_cancel_and_export_journey[size2]`

### PF36

**Persistent requests and obligation progress** — `E-PF36`

Submit and amend tickets while viewing their obligation progress.

- **Successful path:** Every displayed ticket matches its durable current revision and deliverable counts.
- **Failure path:** A stale or missing progress observation is visibly unknown.
- **Edge case:** Repeated refresh, restart and more than one screen of requests do not hide unresolved work.
- **Automated scope:** Headless prompt interactions and obligation acceptance are tested separately, not full progress presentation.
- **Remaining limitation:** Headless views and safe exports exist; real WezTerm GUI, preview routing, usage/reservation display and broader visual inspection remain.
- **Required observations:** `journey`, `gui`.
- **Prerequisites:** Installed Workbench and isolated website/mobile/research project state; Installed WezTerm and a real graphical display on the target platform.
- **Workload tags:** `terminal`, `amendment`.
- **Workload instructions:** `S29`, `S30`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_surfaces.py::test_tui_submit_outcomes_and_log_flood[size0]`
- `tests/test_surfaces.py::test_tui_submit_outcomes_and_log_flood[size1]`
- `tests/test_surfaces.py::test_tui_submit_outcomes_and_log_flood[size2]`
- `tests/test_context_quality.py::test_obligations_gate_acceptance`
- `tests/test_tui_scenarios.py::test_tui_project_amend_cancel_and_export_journey[size0]`
- `tests/test_tui_scenarios.py::test_tui_project_amend_cancel_and_export_journey[size1]`
- `tests/test_tui_scenarios.py::test_tui_project_amend_cancel_and_export_journey[size2]`
- `tests/test_tui_scenarios.py::test_tui_deliverables_blockers_and_scoped_pause`
- `tests/test_tui_scenarios.py::test_tui_new_external_project_and_older_request_search`
- `tests/test_scenario_runner.py::test_thirty_instructions_persist_real_state_without_fake_products`

### PF37

**Dedicated Outcomes view** — `E-PF37`

Keep an outcome open while workers produce a large log stream.

- **Successful path:** The answer remains readable in its own view with stable selection.
- **Failure path:** Worker logs never replace the selected outcome.
- **Edge case:** Refresh/reconnect returns the persisted result after restart.
- **Automated scope:** Actual headless log-flood interactions at three terminal sizes.
- **Remaining limitation:** Headless views and safe exports exist; real WezTerm GUI, preview routing, usage/reservation display and broader visual inspection remain.
- **Required observations:** `gui`, `workers`.
- **Prerequisites:** Installed WezTerm and a real graphical display on the target platform; Authenticated model harness and an actual coordinator-owned worker run.
- **Workload tags:** `terminal`, `load`.
- **Workload instructions:** `S30`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_surfaces.py::test_tui_submit_outcomes_and_log_flood[size0]`
- `tests/test_surfaces.py::test_tui_submit_outcomes_and_log_flood[size1]`
- `tests/test_surfaces.py::test_tui_submit_outcomes_and_log_flood[size2]`
- `tests/test_tui_scenarios.py::test_tui_pin_reorders_preserves_selection_and_log_flood`
- `tests/test_tui_scenarios.py::test_view_filter_does_not_change_prompt_destination`

### PF38

**Versioned result cards, checks and limitations** — `E-PF38`

Publish an outcome, amend its request and inspect both versions.

- **Successful path:** Each result shows status, revision, source checks and limitations.
- **Failure path:** Reject review of the superseded result.
- **Edge case:** Revoking a cited source visibly invalidates the previously accepted result.
- **Automated scope:** Local result-version checks and headless rendering; full live cards require screenshots.
- **Remaining limitation:** Headless views and safe exports exist; real WezTerm GUI, preview routing, usage/reservation display and broader visual inspection remain.
- **Required observations:** `journey`, `gui`.
- **Prerequisites:** Installed Workbench and isolated website/mobile/research project state; Installed WezTerm and a real graphical display on the target platform.
- **Workload tags:** `terminal`, `review`.
- **Workload instructions:** `S30`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_context_quality.py::test_fabricated_quote_and_stale_revision_rejected`
- `tests/test_context_quality.py::test_grounded_outcome_review_and_revocation`
- `tests/test_surfaces.py::test_tui_submit_outcomes_and_log_flood[size0]`
- `tests/test_surfaces.py::test_tui_submit_outcomes_and_log_flood[size1]`
- `tests/test_surfaces.py::test_tui_submit_outcomes_and_log_flood[size2]`
- `tests/test_requirement_scenarios.py::test_separate_deliverables_can_be_reviewed_without_premature_completion`
- `tests/test_requirement_scenarios.py::test_proposal_cannot_reopen_request_accepted_during_source_validation`
- `tests/test_tui_scenarios.py::test_tui_project_amend_cancel_and_export_journey[size0]`
- `tests/test_tui_scenarios.py::test_tui_project_amend_cancel_and_export_journey[size1]`
- `tests/test_tui_scenarios.py::test_tui_project_amend_cancel_and_export_journey[size2]`
- `tests/test_runtime_observations.py::test_runtime_observation_proposes_once_never_accepts`
- `tests/test_runtime_observations.py::test_stale_runtime_observation_does_not_publish`
- `tests/test_runtime_observations.py::test_invalid_result_never_becomes_outcome[identity]`
- `tests/test_runtime_observations.py::test_invalid_result_never_becomes_outcome[missing]`
- `tests/test_runtime_observations.py::test_invalid_result_never_becomes_outcome[exit]`
- `tests/test_runtime_observations.py::test_invalid_result_never_becomes_outcome[role]`
- `tests/test_runtime_observations.py::test_invalid_result_never_becomes_outcome[empty]`
- `tests/test_runtime_observations.py::test_changed_runtime_output_rejected`
- `tests/test_report_deliverables.py::test_export_all_separately_accepted_deliverables_with_provenance`
- `tests/test_report_deliverables.py::test_partial_export_does_not_replace_accepted_work_with_latest_rejected_draft`
- `tests/test_report_deliverables.py::test_revision_change_excludes_previously_accepted_body_and_keeps_audit_history`
- `tests/test_report_deliverables.py::test_revoked_evidence_removes_one_accepted_deliverable_from_current_report`
- `tests/test_report_deliverables.py::test_unavailable_evidence_never_exports_as_accepted[scope]`
- `tests/test_report_deliverables.py::test_unavailable_evidence_never_exports_as_accepted[archive]`
- `tests/test_report_deliverables.py::test_one_outcome_can_cover_multiple_deliverables_without_duplicate_body`
- `tests/test_report_deliverables.py::test_export_scope_excludes_unrelated_outcomes_and_cross_project_link`
- `tests/test_report_deliverables.py::test_cancelled_request_does_not_present_historical_acceptance_as_current`

### PF39

**Search, pin, read, reopen and export outcomes** — `E-PF39`

Find an older result, pin/read it, reopen its request and export it.

- **Successful path:** Search returns the intended result; markers and exports survive restart.
- **Failure path:** Reject conflicting export destinations and preserve unsafe text as escaped content.
- **Edge case:** More than 200 results and reordered pins remain discoverable without losing selection.
- **Automated scope:** Markers/export/reopen are tested; complete outcome-finding/search coverage requires the scenario.
- **Remaining limitation:** Headless views and safe exports exist; real WezTerm GUI, preview routing, usage/reservation display and broader visual inspection remain.
- **Required observations:** `journey`, `gui`.
- **Prerequisites:** Installed Workbench and isolated website/mobile/research project state; Installed WezTerm and a real graphical display on the target platform.
- **Workload tags:** `terminal`, `reopen`.
- **Workload instructions:** `S15`, `S30`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_surfaces.py::test_tui_submit_outcomes_and_log_flood[size0]`
- `tests/test_surfaces.py::test_tui_submit_outcomes_and_log_flood[size1]`
- `tests/test_surfaces.py::test_tui_submit_outcomes_and_log_flood[size2]`
- `tests/test_surfaces.py::test_export_escapes_untrusted_content`
- `tests/test_intake.py::test_cancel_before_dispatch_and_reopen_preserves_history`
- `tests/test_tui_scenarios.py::test_tui_project_amend_cancel_and_export_journey[size0]`
- `tests/test_tui_scenarios.py::test_tui_project_amend_cancel_and_export_journey[size1]`
- `tests/test_tui_scenarios.py::test_tui_project_amend_cancel_and_export_journey[size2]`
- `tests/test_tui_scenarios.py::test_tui_pin_reorders_preserves_selection_and_log_flood`
- `tests/test_report_deliverables.py::test_export_all_separately_accepted_deliverables_with_provenance`
- `tests/test_report_deliverables.py::test_partial_export_does_not_replace_accepted_work_with_latest_rejected_draft`
- `tests/test_report_deliverables.py::test_revision_change_excludes_previously_accepted_body_and_keeps_audit_history`
- `tests/test_report_deliverables.py::test_revoked_evidence_removes_one_accepted_deliverable_from_current_report`
- `tests/test_report_deliverables.py::test_unavailable_evidence_never_exports_as_accepted[scope]`
- `tests/test_report_deliverables.py::test_unavailable_evidence_never_exports_as_accepted[archive]`
- `tests/test_report_deliverables.py::test_one_outcome_can_cover_multiple_deliverables_without_duplicate_body`
- `tests/test_report_deliverables.py::test_export_scope_excludes_unrelated_outcomes_and_cross_project_link`
- `tests/test_report_deliverables.py::test_cancelled_request_does_not_present_historical_acceptance_as_current`

### PF40

**Team/agent/child views and meaningful updates** — `E-PF40`

Observe peer and child agents during the three-project workload.

- **Successful path:** Views show actual run/parent identities, role, status, progress and blockers.
- **Failure path:** A planned or missing agent is never labelled running.
- **Edge case:** A failed child and an independent healthy peer stay distinguishable after reconnect.
- **Automated scope:** Headless Textual verifies that planned agents and manually injected fixture runs are labelled accurately and stale observations remain visible. No product-owned live worker run is established by this check.
- **Remaining limitation:** Headless views and safe exports exist; real WezTerm GUI, preview routing, usage/reservation display and broader visual inspection remain.
- **Required observations:** `workers`, `gui`.
- **Prerequisites:** Authenticated model harness and an actual coordinator-owned worker run; Installed WezTerm and a real graphical display on the target platform.
- **Workload tags:** `teams`, `terminal`.
- **Workload instructions:** `S30`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_tui_scenarios.py::test_tui_planned_and_fixture_agents_are_not_claimed_live`
- `tests/test_runtime_observations.py::test_partial_runtime_refresh_preserves_other_project_nodes`

### PF41

**Dependencies, reservations and usage visibility** — `E-PF41`

Display a dependency graph alongside reserved/used capacity.

- **Successful path:** Blocked dependencies and actual resource use agree with coordinator observations.
- **Failure path:** Missing usage data is labelled unknown, not zero.
- **Edge case:** Reservation expiry and dependency cancellation update the view without losing history.
- **Automated scope:** Protocol-shaped Codex/Claude usage fixtures validate provider-reported counts, cache/reasoning buckets, duplicate observation/revision accounting, conflicting or missing values as unknown, and capacity reads. No real provider call, subscription balance, monetary cost or actual terminal rendering is inferred. Headless Textual verifies missing heartbeats, occupied capacity and uncertain leases without disclosing tokens.
- **Remaining limitation:** Provider-reported token accounting and capacity read models are present. Actual harness usage collection, reservation/dependency updates and target-terminal presentation still require observed end-to-end runs; price and subscription balances are explicitly unknown.
- **Required observations:** `capacity`, `gui`.
- **Prerequisites:** Running coordinator/worker pool and a bounded load generator; Installed WezTerm and a real graphical display on the target platform.
- **Workload tags:** `teams`, `load`, `terminal`.

Mapped pytest evidence:

- `tests/test_runtime_backends.py::test_cli_usage_claude_normalizes_separate_cache_buckets_and_unknown_reasoning`
- `tests/test_runtime_backends.py::test_cli_usage_codex_preserves_cache_and_reasoning_without_double_count`
- `tests/test_runtime_backends.py::test_cli_usage_malformed_counter_is_unknown_not_zero[-1]`
- `tests/test_runtime_backends.py::test_cli_usage_malformed_counter_is_unknown_not_zero[True]`
- `tests/test_runtime_backends.py::test_cli_usage_malformed_counter_is_unknown_not_zero[100]`
- `tests/test_runtime_backends.py::test_cli_usage_malformed_counter_is_unknown_not_zero[1.5]`
- `tests/test_runtime_backends.py::test_cli_usage_malformed_counter_is_unknown_not_zero[1180591620717411303424]`
- `tests/test_runtime_backends.py::test_cli_usage_malformed_counter_is_unknown_not_zero[bad5]`
- `tests/test_runtime_backends.py::test_cli_usage_malformed_counter_is_unknown_not_zero[bad6]`
- `tests/test_runtime_backends.py::test_cli_usage_missing_and_malformed_terminal_event_remains_unknown`
- `tests/test_runtime_backends.py::test_usage_report_conflicts_unknown_generations_and_read_only`
- `tests/test_runtime_backends.py::test_usage_report_deduplicates_sources_observations_and_counts_revisions`
- `tests/test_runtime_backends.py::test_usage_report_without_observations_never_invents_zero_usage`
- `tests/test_tui_scenarios.py::test_tui_fleet_capacity_keeps_uncertain_work_visible`

### PF42

**Context health and attention inbox** — `E-PF42`

Create an unresolved route, stale context and an unavailable backend.

- **Successful path:** The attention inbox exposes each blocker with a correct recovery action.
- **Failure path:** An error cannot disappear merely because the view refreshes.
- **Edge case:** Recover one component while another remains blocked; health stays component-specific.
- **Automated scope:** Headless Textual shows deliverable/blocker state, scoped pause and diagnostic file corruption/recovery. Corpus-level context health and a complete operator attention workflow remain separate observations.
- **Remaining limitation:** Headless views and safe exports exist; real WezTerm GUI, preview routing, usage/reservation display and broader visual inspection remain.
- **Required observations:** `journey`, `gui`.
- **Prerequisites:** Installed Workbench and isolated website/mobile/research project state; Installed WezTerm and a real graphical display on the target platform.
- **Workload tags:** `context`, `health`, `terminal`.
- **Workload instructions:** `S24`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_tui_scenarios.py::test_tui_deliverables_blockers_and_scoped_pause`
- `tests/test_tui_scenarios.py::test_tui_corrupt_diagnostics_remain_visible_and_recoverable`
- `tests/test_runtime_observations.py::test_failed_runtime_moves_request_to_attention_once`
- `tests/test_runtime_observations.py::test_partial_runtime_refresh_preserves_other_project_nodes`
- `tests/test_tui_scenarios.py::test_tui_fleet_capacity_keeps_uncertain_work_visible`

### PF43

**Diagrams, charts and artifact previews** — `E-PF43`

Request a source-linked architecture diagram and a comparison table.

- **Successful path:** Rendered diagrams, labels and previews match the actual architecture and data.
- **Failure path:** Untrusted HTML/script/terminal sequences remain inert.
- **Edge case:** Inspect narrow terminal and exported document views for clipped labels or missing legends.
- **Automated scope:** Export escaping only; diagram/chart rendering and factual inspection are separate. Local web asset and research citation validation detects changes; fake mobile archives cannot certify an application. These checks do not render a browser, device or diagram.
- **Remaining limitation:** Headless views and safe exports exist; real WezTerm GUI, preview routing, usage/reservation display and broader visual inspection remain.
- **Required observations:** `visuals`, `gui`.
- **Prerequisites:** Generated HTML/SVG/document artifacts and a real rendering/inspection environment; Installed WezTerm and a real graphical display on the target platform.
- **Workload tags:** `website`, `research`, `terminal`.
- **Workload instructions:** `S09`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_surfaces.py::test_export_escapes_untrusted_content`
- `tests/test_execution_packs.py::test_web_artifact_checks_follow_real_assets_and_detect_drift`
- `tests/test_execution_packs.py::test_mobile_source_and_fake_archive_never_certify_a_working_app`
- `tests/test_execution_packs.py::test_artifact_paths_and_symlink_escape_are_rejected`
- `tests/test_app_integrations.py::test_execution_actions_run_real_tests_and_reopen_results`

### PF44

**Safe, legible, recoverable diagnostics UI** — `E-PF44`

Display malicious-looking tool output and diagnostic floods.

- **Successful path:** Diagnostics remain legible and cannot execute terminal controls.
- **Failure path:** OSC hyperlinks/clipboard and ANSI controls are stripped from untrusted content.
- **Edge case:** Recover from malformed MCP input, output overflow and a long log burst.
- **Automated scope:** Headless log flood and malformed/overflow recovery; target rendering needs a GUI run.
- **Remaining limitation:** Headless views and safe exports exist; real WezTerm GUI, preview routing, usage/reservation display and broader visual inspection remain.
- **Required observations:** `gui`.
- **Prerequisites:** Installed WezTerm and a real graphical display on the target platform.
- **Workload tags:** `terminal`, `load`.
- **Workload instructions:** `S21`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_surfaces.py::test_mcp_malformed_request_recovers_for_next_request`
- `tests/test_surfaces.py::test_fast_process_output_overflow_is_not_silently_truncated`
- `tests/test_surfaces.py::test_tui_submit_outcomes_and_log_flood[size0]`
- `tests/test_surfaces.py::test_tui_submit_outcomes_and_log_flood[size1]`
- `tests/test_surfaces.py::test_tui_submit_outcomes_and_log_flood[size2]`
- `tests/test_tui_scenarios.py::test_tui_corrupt_diagnostics_remain_visible_and_recoverable`
- `tests/test_tui_scenarios.py::test_tui_stale_amendment_is_rejected_without_clobbering`
- `tests/test_tui_scenarios.py::test_view_filter_does_not_change_prompt_destination`
- `tests/test_report_deliverables.py::test_export_all_separately_accepted_deliverables_with_provenance`

### PF45

**Superpowers evidence-led brainstorming** — `E-PF45`

Ask Superpowers to compare materially different architectures from conflicting evidence.

- **Successful path:** The activated pinned procedure produces alternatives, constraints, tradeoffs and a reviewable decision.
- **Failure path:** An explicit Superpowers request blocks if its procedures are absent.
- **Edge case:** A changed dependency/constraint updates the decision without discarding the original rationale.
- **Automated scope:** Missing-procedure blocker only; a generated brainstorming outcome needs real execution.
- **Remaining limitation:** Pinned Superpowers activation is implemented; real brainstorming artifact/quality qualification remains.
- **Required observations:** `procedures`, `brainstorm`.
- **Prerequisites:** Installed Superpowers, Elements of Style and Humanizer source checkouts; Pinned activated Superpowers procedures and a real reasoning worker.
- **Workload tags:** `website`, `research`, `brainstorm`.
- **Workload instructions:** `S03`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_boundaries.py::test_explicit_missing_superpowers_is_visible_blocker`

### PF46

**Readable plans and executable packets from one model** — `E-PF46`

Produce a human plan and executable role packets for the same task.

- **Successful path:** Both representations share request revision, requirements, dependencies and acceptance checks.
- **Failure path:** A stale or oversized packet is rejected with a visible reason.
- **Edge case:** A later scope change updates both representations without changing approved unaffected constraints.
- **Automated scope:** Graph and packet-limit checks only; semantic agreement/readability needs observed artifacts.
- **Remaining limitation:** Versioned procedures and language guidance exist; full real execution packs and editorial meaning-preservation evaluation remain.
- **Required observations:** `workers`, `writing`.
- **Prerequisites:** Authenticated model harness and an actual coordinator-owned worker run; Known-answer source corpus and independent editorial review rubric.
- **Workload tags:** `website`, `mobile`, `amendment`.
- **Workload instructions:** `S06`, `S09`, `S29`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_execution.py::test_oversized_packet_is_not_silently_truncated`
- `tests/test_execution.py::test_plan_cycles_and_child_limits`

### PF47

**Versioned skill activation and resume checks** — `E-PF47`

Activate pinned procedures, checkpoint, edit a procedure and resume.

- **Successful path:** Receipts retain revision/hash and the worker uses the expected procedure.
- **Failure path:** Changed or missing procedure content blocks or explicitly recompiles the resumed plan.
- **Edge case:** Reactivating the same version is idempotent and does not duplicate procedures.
- **Automated scope:** Unavailable-procedure handling; full version-change/resume conformance needs the scenario.
- **Remaining limitation:** Versioned procedures and language guidance exist; full real execution packs and editorial meaning-preservation evaluation remain.
- **Required observations:** `procedures`, `recovery`.
- **Prerequisites:** Installed Superpowers, Elements of Style and Humanizer source checkouts; Actual coordinator, authenticated worker and controlled context/session rollover.
- **Workload tags:** `recovery`, `skills`.

Mapped pytest evidence:

- `tests/test_boundaries.py::test_explicit_missing_superpowers_is_visible_blocker`

### PF48

**Audience and approved writing profile** — `E-PF48`

Generate an executive summary and an engineering appendix for approved audiences.

- **Successful path:** Audience, word budget and allowed terminology persist in the plan and output.
- **Failure path:** Reject unreviewed policy changes or unsupported certainty in a short rewrite.
- **Edge case:** Later audience edits do not retroactively change the provenance of older results.
- **Automated scope:** Word-budget/style lint is advisory; audience-policy execution needs actual outputs.
- **Remaining limitation:** Versioned procedures and language guidance exist; full real execution packs and editorial meaning-preservation evaluation remain.
- **Required observations:** `writing`.
- **Prerequisites:** Known-answer source corpus and independent editorial review rubric.
- **Workload tags:** `research`, `writing`.
- **Workload instructions:** `S09`, `S29`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_context_quality.py::test_lint_is_advisory_not_truth_detector`
- `tests/test_requirement_scenarios.py::test_outcome_style_uses_the_configured_audience_budget`

### PF49

**Writing Clearly and Concisely, Humanizer and Vale** — `E-PF49`

Rewrite a verbose report containing numbers, negation and uncertainty.

- **Successful path:** Output is concise and preserves every material fact and caveat.
- **Failure path:** Vale catches seeded vague language; Humanizer must not fabricate a more confident claim.
- **Edge case:** Clean text can still be factually wrong, so style success cannot certify truth.
- **Automated scope:** Local style and quote guards; actual procedures, Vale and meaning preservation remain separate checks.
- **Remaining limitation:** Versioned procedures and language guidance exist; full real execution packs and editorial meaning-preservation evaluation remain.
- **Required observations:** `vale`, `writing`.
- **Prerequisites:** Installed Vale and activated writing procedures; Known-answer source corpus and independent editorial review rubric.
- **Workload tags:** `research`, `writing`.
- **Workload instructions:** `S03`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_context_quality.py::test_lint_is_advisory_not_truth_detector`
- `tests/test_context_quality.py::test_fabricated_quote_and_stale_revision_rejected`
- `tests/test_requirement_scenarios.py::test_outcome_style_uses_the_configured_audience_budget`

### PF50

**Rendered, inspected visual documents** — `E-PF50`

Export the final report with a table and architecture diagram, then render it.

- **Successful path:** Every page/view is inspected for readable labels, intact content and correct source references.
- **Failure path:** Broken assets, clipping or false labels fail the visual review.
- **Edge case:** Long titles, narrow viewports and multi-page content remain legible.
- **Automated scope:** Safe HTML export only; visual inspection is not inferred from a file existing. Mechanical artifact/hash checks are exercised and require separate semantic review; no actual browser/device rendering is inferred.
- **Remaining limitation:** Headless views and safe exports exist; real WezTerm GUI, preview routing, usage/reservation display and broader visual inspection remain.
- **Required observations:** `visuals`.
- **Prerequisites:** Generated HTML/SVG/document artifacts and a real rendering/inspection environment.
- **Workload tags:** `research`, `terminal`.
- **Workload instructions:** `S09`, `S30`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_surfaces.py::test_export_escapes_untrusted_content`
- `tests/test_execution_packs.py::test_web_artifact_checks_follow_real_assets_and_detect_drift`
- `tests/test_execution_packs.py::test_mobile_source_and_fake_archive_never_certify_a_working_app`
- `tests/test_execution_packs.py::test_research_citations_require_real_scoped_sources_and_remain_reviewable`
- `tests/test_report_deliverables.py::test_export_all_separately_accepted_deliverables_with_provenance`
- `tests/test_report_deliverables.py::test_partial_export_does_not_replace_accepted_work_with_latest_rejected_draft`
- `tests/test_report_deliverables.py::test_revision_change_excludes_previously_accepted_body_and_keeps_audit_history`
- `tests/test_report_deliverables.py::test_revoked_evidence_removes_one_accepted_deliverable_from_current_report`
- `tests/test_report_deliverables.py::test_unavailable_evidence_never_exports_as_accepted[scope]`
- `tests/test_report_deliverables.py::test_unavailable_evidence_never_exports_as_accepted[archive]`
- `tests/test_report_deliverables.py::test_one_outcome_can_cover_multiple_deliverables_without_duplicate_body`
- `tests/test_report_deliverables.py::test_export_scope_excludes_unrelated_outcomes_and_cross_project_link`
- `tests/test_report_deliverables.py::test_cancelled_request_does_not_present_historical_acceptance_as_current`

### PF51

**Python, TypeScript/JavaScript and Rust execution packs** — `E-PF51`

Execute small Python, TypeScript and Rust changes with tests in each project.

- **Successful path:** Each language pack selects real build/test commands and records exit codes and artifacts.
- **Failure path:** A deliberately broken assertion/build fails the task instead of being ignored.
- **Edge case:** Missing toolchains and timeouts remain explicit blockers without claiming all languages work.
- **Automated scope:** Actual Python, JavaScript, TypeScript and Rust subprocess tests include passing and deliberately failing controls. Missing toolchains skip their positive cases and therefore block this feature. Zero-test commands cannot pass; unreviewed package scripts and missing configuration are blocked. These test projects are fixtures, not agent-generated applications or device/browser qualification.
- **Remaining limitation:** Versioned procedures and language guidance exist; full real execution packs and editorial meaning-preservation evaluation remain.
- **Required observations:** `languages`, `workers`.
- **Prerequisites:** Python, Node/TypeScript and Rust toolchains with isolated test projects; Authenticated model harness and an actual coordinator-owned worker run.
- **Workload tags:** `website`, `mobile`, `languages`.
- **Workload instructions:** `S01`, `S02`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_surfaces.py::test_process_timeout_output_and_no_shell_interpolation`
- `tests/test_execution_packs.py::test_python_real_pass_fail_and_source_isolation`
- `tests/test_execution_packs.py::test_empty_and_zero_test_suites_do_not_pass`
- `tests/test_execution_packs.py::test_node_builtin_real_pass_and_seeded_failure`
- `tests/test_execution_packs.py::test_package_script_needs_review_and_zero_exit_is_not_tests`
- `tests/test_execution_packs.py::test_conflicting_package_locks_and_missing_typescript_script_are_blocked`
- `tests/test_execution_packs.py::test_rust_discovery_requires_runtime_and_reviewed_lock`
- `tests/test_execution_packs.py::test_typescript_real_typecheck_and_seeded_type_error`
- `tests/test_execution_packs.py::test_rust_real_test_and_seeded_assertion_failure`
- `tests/test_workspace_backup.py::test_backup_restores_artifacts_and_rebinds_workspace_then_executes`
- `tests/test_app_integrations.py::test_execution_actions_run_real_tests_and_reopen_results`
- `tests/test_execution_packs.py::test_arbitrary_scripts_cannot_qualify_by_printing_test_totals[Tests: 42]`
- `tests/test_execution_packs.py::test_arbitrary_scripts_cannot_qualify_by_printing_test_totals[# tests 42]`
- `tests/test_execution_packs.py::test_declared_node_test_script_runs_actual_test`

### PF52

**Evidence-based evaluation and human calibration** — `E-PF52`

Evaluate reports against held-out facts and human accept/reject labels.

- **Successful path:** Report agreement, false accepts and false rejects separately.
- **Failure path:** Reject a confident judge approval of a source-inconsistent claim.
- **Edge case:** Judge disagreement remains visible; consensus is not treated as factual proof.
- **Automated scope:** Agreement arithmetic only; no live judge accuracy or human calibration implied.
- **Remaining limitation:** Calibration/policy mechanisms exist; held-out live model judges, task-quality/cost benchmarks and human calibration remain.
- **Required observations:** `calibration`.
- **Prerequisites:** Human-labelled held-out examples and independent judge outputs.
- **Workload tags:** `research`, `review`.

Mapped pytest evidence:

- `tests/test_evaluation.py::test_judges_report_false_accepts`

### PF53

**Routing, selection and outcome-finding evaluation** — `E-PF53`

Measure routing, model selection and locating old outcomes on held-out requests.

- **Successful path:** Each case has a recorded expected/actual result, miss category and latency.
- **Failure path:** Do not hide ambiguous or incorrectly routed cases from the denominator.
- **Edge case:** Paraphrases and cross-project references remain in the evaluation set.
- **Automated scope:** Accounting preserves misses; the existing routing demo is not held-out validation.
- **Remaining limitation:** Calibration/policy mechanisms exist; held-out live model judges, task-quality/cost benchmarks and human calibration remain.
- **Required observations:** `benchmark`.
- **Prerequisites:** Qualified profiles and a versioned held-out benchmark with expected outcomes.
- **Workload tags:** `routing`, `models`.

Mapped pytest evidence:

- `tests/test_evaluation.py::test_routing_reports_misses_instead_of_hiding_them`
- `tests/test_execution.py::test_model_override_and_missing_qualification`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Create a website for HarborWeb with accessible event booking.-code]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Create a mobile app for PocketTasks with offline habits.-code]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Report the architecture review.-report]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Add dark mode to the website.-code]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Research offline-first mobile architecture and provide a report.-research]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Write code that produces a report.-code]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Create a plan for building a website.-design]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Please summarize the research about code review.-report]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Fix the failing test without changing the research report.-code]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Compare two designs before implementing either.-research]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Design the architecture for a mobile app.-design]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Create a report from the architecture decisions.-report]`
- `tests/test_policy_scenarios.py::test_manifest_context_prefix_keeps_requested_work[S10-code]`
- `tests/test_policy_scenarios.py::test_manifest_context_prefix_keeps_requested_work[S11-code]`
- `tests/test_policy_scenarios.py::test_manifest_context_prefix_keeps_requested_work[S16-code]`
- `tests/test_policy_scenarios.py::test_manifest_context_prefix_keeps_requested_work[S17-code]`
- `tests/test_policy_scenarios.py::test_manifest_context_prefix_keeps_requested_work[S25-report]`

### PF54

**Fault, capacity and soak verification** — `E-PF54`

Run concurrent prompts with worker crashes, storage pressure and a 72-hour soak.

- **Successful path:** Retain every accepted prompt and reconcile run/effect ownership across failures.
- **Failure path:** Disk full, output overflow, bad messages and killed workers produce recoverable visible errors.
- **Edge case:** Host sleep/reboot and sustained load cannot silently lose outcomes or duplicate effects.
- **Automated scope:** Selected fault/concurrency tests, not an exhaustive or 72-hour mixed-system qualification.
- **Remaining limitation:** Selected concurrency/fault cases tested; disk-full, host sleep/reboot, sustained load and 72-hour soak remain.
- **Required observations:** `capacity`, `soak`.
- **Prerequisites:** Running coordinator/worker pool and a bounded load generator; Long-running isolated deployment and controlled failure/load injection.
- **Workload tags:** `load`, `recovery`.
- **Workload instructions:** `S30`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_intake.py::test_concurrent_unique_and_retry_receipts`
- `tests/test_execution.py::test_uncertain_agent_create_does_not_duplicate`
- `tests/test_operations.py::test_snapshot_restore_and_tamper_detection`
- `tests/test_surfaces.py::test_fast_process_output_overflow_is_not_silently_truncated`
- `tests/test_execution_packs.py::test_real_process_wall_timeout_and_output_limit`
- `tests/test_execution_packs.py::test_running_process_can_be_cancelled_from_another_operator`
- `tests/test_execution_packs.py::test_python_virtual_memory_limit_is_observed`
- `tests/test_execution_packs.py::test_run_progress_and_result_survive_new_application_instance`
- `tests/test_scenario_runner.py::test_injected_stale_acceptance_defect_is_detected`
- `tests/test_scenario_runner.py::test_missing_initial_request_blocks_dependents_without_hiding_other_projects`
- `tests/test_scenario_runner.py::test_live_probe_timeout_preserves_uncertainty`
- `tests/test_workspace_backup.py::test_backup_detects_concurrent_writes_and_removes_partial_copy[filesystem]`
- `tests/test_workspace_backup.py::test_backup_detects_concurrent_writes_and_removes_partial_copy[database]`

### PF55

**Reviewed improvements with rollback** — `E-PF55`

Propose a measured policy change, approve it, then roll it back.

- **Successful path:** The proposal is inert until reviewed and rollback restores its exact prior value.
- **Failure path:** Reject unsupported policies and rollback over newer unrelated configuration changes.
- **Edge case:** Retain evaluation evidence and a history of which policy each task used.
- **Automated scope:** Local controlled policy mutations and rollback; outcome improvement needs measured evaluation.
- **Remaining limitation:** Calibration/policy mechanisms exist; held-out live model judges, task-quality/cost benchmarks and human calibration remain.
- **Required observations:** `journey`, `benchmark`.
- **Prerequisites:** Installed Workbench and isolated website/mobile/research project state; Qualified profiles and a versioned held-out benchmark with expected outcomes.
- **Workload tags:** `models`, `review`.

Mapped pytest evidence:

- `tests/test_evaluation.py::test_policy_review_and_conflicting_rollback`
- `tests/test_evaluation.py::test_invalid_policy_is_not_applied`

### PF56

**One GitHub repository and reproducible packages** — `E-PF56`

Build from a recorded commit and install independently on Linux and macOS.

- **Successful path:** Each fresh package runs the installed prompt/outcome/backup/restore journey.
- **Failure path:** Missing locks, changed artifacts or incompatible dependency resolution are visible failures.
- **Edge case:** Paths with spaces and repeated installation preserve existing user configuration.
- **Automated scope:** Independently installed artifact scenario; universal upstream reproducibility is not inferred.
- **Remaining limitation:** Repository packages and CI included; all-upstream hash locking and cross-platform reproducibility remain.
- **Required observations:** `packages`.
- **Prerequisites:** Fresh Linux/macOS environments and build/install tools.
- **Workload tags:** `installation`.

Mapped pytest evidence:

- `tests/test_installed.py::test_installed_prompt_outcome_backup_restore`
- `tests/test_dependency_install.py::test_failed_build_reports_its_output_and_allows_a_clean_retry`
- `tests/test_dependency_install.py::test_retry_after_failure_at_same_revision_runs_again`

### PF57

**Harness and linked-project contracts** — `E-PF57`

Use CLI, MCP and the explicitly linked project from separate sessions.

- **Successful path:** Contracts preserve project/request identity, revisions and artifact references across clients.
- **Failure path:** Malformed envelopes and unsupported versions fail clearly without killing subsequent requests.
- **Edge case:** The actual linked project contract must be read; unavailable shared-chat contents cannot be invented.
- **Automated scope:** Generic local CLI/MCP tests only; specific external-project compatibility remains separate.
- **Remaining limitation:** Generic CLI/MCP/project contracts exist; the referenced shared conversation was unavailable and its specific project compatibility is not certified.
- **Required observations:** `linked_contract`, `models`.
- **Prerequisites:** Accessible linked-project specification and running peer integration; Signed-in official CLI or explicitly configured API/local model provider.
- **Workload tags:** `installation`, `recovery`.

Mapped pytest evidence:

- `tests/test_surfaces.py::test_mcp_stdio_round_trip_and_argument_validation`
- `tests/test_surfaces.py::test_mcp_malformed_request_recovers_for_next_request`
- `tests/test_surfaces.py::test_cli_exit_codes_and_restart`
- `tests/test_harness_surfaces.py::test_mcp_new_project_execution_and_scoped_tools`
- `tests/test_harness_surfaces.py::test_mcp_rejects_nonobject_arguments[value0]`
- `tests/test_harness_surfaces.py::test_mcp_rejects_nonobject_arguments[False]`
- `tests/test_harness_surfaces.py::test_mcp_rejects_nonobject_arguments[0]`
- `tests/test_harness_surfaces.py::test_mcp_rejects_nonobject_arguments[]`
- `tests/test_harness_surfaces.py::test_mcp_rejects_nonobject_arguments[None]`
- `tests/test_harness_surfaces.py::test_mcp_validates_advertised_types[arguments0]`
- `tests/test_harness_surfaces.py::test_mcp_validates_advertised_types[arguments1]`
- `tests/test_harness_surfaces.py::test_mcp_validates_advertised_types[arguments2]`
- `tests/test_app_integrations.py::test_new_dispatch_namespaces_use_exact_method_allowlists[backend._bound]`
- `tests/test_app_integrations.py::test_new_dispatch_namespaces_use_exact_method_allowlists[backend.ingest.extra]`
- `tests/test_app_integrations.py::test_new_dispatch_namespaces_use_exact_method_allowlists[backend.request]`
- `tests/test_app_integrations.py::test_new_dispatch_namespaces_use_exact_method_allowlists[execution._execute]`
- `tests/test_app_integrations.py::test_new_dispatch_namespaces_use_exact_method_allowlists[execution.run.extra]`
- `tests/test_app_integrations.py::test_new_dispatch_namespaces_use_exact_method_allowlists[execution.__class__]`
- `tests/test_app_integrations.py::test_new_dispatch_namespaces_use_exact_method_allowlists[fleet._host]`
- `tests/test_app_integrations.py::test_new_dispatch_namespaces_use_exact_method_allowlists[fleet.worker_request]`
- `tests/test_app_integrations.py::test_new_dispatch_namespaces_use_exact_method_allowlists[fleet.admit]`
- `tests/test_app_integrations.py::test_new_dispatch_namespaces_use_exact_method_allowlists[fleet.register.extra]`
- `tests/test_app_integrations.py::test_dispatch_rejects_malformed_envelopes[None-args0]`
- `tests/test_app_integrations.py::test_dispatch_rejects_malformed_envelopes[action1-args1]`
- `tests/test_app_integrations.py::test_dispatch_rejects_malformed_envelopes[-args2]`
- `tests/test_app_integrations.py::test_dispatch_rejects_malformed_envelopes[backend.status-args3]`
- `tests/test_app_integrations.py::test_dispatch_rejects_malformed_envelopes[backend.status-token]`

### PF58

**Doctor, migration, backup, restore and uninstall** — `E-PF58`

Replace a customized disposable Olympus installation and then restore it.

- **Successful path:** Preserve local edits, ignored files, Git history and credentials in private backups.
- **Failure path:** Refuse cutover without evidence, overlapping roots, tampered backups or conflicting restore targets.
- **Edge case:** Symlinks and later theme/user edits are preserved; uninstall removes only owned components.
- **Automated scope:** Disposable migration fixtures and backup/restore checks; no actual user-machine cutover implied.
- **Remaining limitation:** Actual customized Mac cutover and service lifecycle still require target-machine evidence.
- **Required observations:** `migration`.
- **Prerequisites:** Disposable customized legacy installation plus current target evidence; never use real user data as a fixture.
- **Workload tags:** `installation`, `recovery`.

Mapped pytest evidence:

- `tests/test_operations.py::test_snapshot_restore_and_tamper_detection`
- `tests/test_operations.py::test_snapshot_database_is_self_contained_without_wal_sidecars`
- `tests/test_operations.py::test_snapshot_paths_accept_os_symlinks_but_reject_user_symlinks`
- `tests/test_operations.py::test_migration_preserves_local_edits_ignored_files_and_git_history`
- `tests/test_operations.py::test_migration_rejects_overlap_and_account_root`
- `tests/test_operations.py::test_migration_never_retires_without_target_evidence`
- `tests/test_operations.py::test_verified_retire_is_recoverable_and_does_not_overwrite_new_changes`
- `tests/test_operations.py::test_backup_inventory_tampering_is_detected`
- `tests/test_operations.py::test_symlink_backups_do_not_follow_targets`
- `tests/test_operations.py::test_terminal_include_preserves_theme_and_local_edits`
- `tests/test_workspace_backup.py::test_backup_restores_artifacts_and_rebinds_workspace_then_executes`
- `tests/test_workspace_backup.py::test_legacy_absolute_workspace_path_is_normalized_in_snapshot`
- `tests/test_workspace_backup.py::test_backup_rejects_symlink_in_owned_workspace[False]`
- `tests/test_workspace_backup.py::test_backup_rejects_symlink_in_owned_workspace[True]`
- `tests/test_workspace_backup.py::test_restore_rejects_entries_absent_from_inventory[extra.txt]`
- `tests/test_workspace_backup.py::test_restore_rejects_entries_absent_from_inventory[extra-directory]`
- `tests/test_workspace_backup.py::test_restore_rejects_symlink_even_when_target_is_inside_snapshot[False]`
- `tests/test_workspace_backup.py::test_restore_rejects_symlink_even_when_target_is_inside_snapshot[True]`
- `tests/test_workspace_backup.py::test_restore_rejects_symlink_destination_ancestor_and_nested_source`
- `tests/test_workspace_backup.py::test_restore_validates_manifest_paths_before_copy[../escaped.txt]`
- `tests/test_workspace_backup.py::test_restore_validates_manifest_paths_before_copy[/absolute.txt]`
- `tests/test_workspace_backup.py::test_restore_validates_manifest_paths_before_copy[archive/../escaped.txt]`
- `tests/test_workspace_backup.py::test_restore_validates_manifest_paths_before_copy[archive//file]`
- `tests/test_workspace_backup.py::test_restore_validates_manifest_paths_before_copy[archive\\file]`
- `tests/test_workspace_backup.py::test_restore_detects_artifact_content_drift[changed]`
- `tests/test_workspace_backup.py::test_restore_detects_artifact_content_drift[missing]`
- `tests/test_workspace_backup.py::test_backup_requires_registered_workspaces`
- `tests/test_workspace_backup.py::test_backup_rejects_workspace_path_outside_owned_state`
- `tests/test_workspace_backup.py::test_restore_validates_database_and_removes_failed_restore[corrupt]`
- `tests/test_workspace_backup.py::test_restore_validates_database_and_removes_failed_restore[wrong_schema]`
- `tests/test_workspace_backup.py::test_restore_rejects_registered_but_missing_workspace_even_with_rehashed_manifest`
- `tests/test_workspace_backup.py::test_backup_detects_concurrent_writes_and_removes_partial_copy[filesystem]`
- `tests/test_workspace_backup.py::test_backup_detects_concurrent_writes_and_removes_partial_copy[database]`
- `tests/test_workspace_backup.py::test_backup_rejects_actual_running_process_until_cancelled`

### PF59

**Authorization, credentials and effect audit** — `E-PF59`

Attempt unauthorized operator, source and side-effect actions.

- **Successful path:** Authorized actions have scoped identities and durable audit receipts.
- **Failure path:** Reject missing tokens, foreign origins, credential misuse and shell interpolation.
- **Edge case:** After an uncertain effect, a stale worker cannot repeat it under a new generation.
- **Automated scope:** Local API/effect/environment guards; OS-level hostile-worker isolation needs live attempts.
- **Remaining limitation:** Local operator controls and effect audit exist; hostile agents require OS/container and scoped credential boundaries.
- **Required observations:** `isolation`.
- **Prerequisites:** Configured OS/container isolation and scoped worker credentials.
- **Workload tags:** `scope`, `security`.
- **Workload instructions:** `S27`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_surfaces.py::test_http_token_origin_and_durable_prompt`
- `tests/test_surfaces.py::test_process_timeout_output_and_no_shell_interpolation`
- `tests/test_execution.py::test_checkpoint_generation_budget_progress_and_effects`
- `tests/test_boundaries.py::test_telemetry_is_disabled_before_integration_process_start`
- `tests/test_execution_packs.py::test_workspace_rejects_source_symlink_and_excludes_secret_and_git_files`
- `tests/test_execution_packs.py::test_registered_workspace_cannot_be_replaced_by_external_symlink`
- `tests/test_execution_packs.py::test_research_manifest_rejects_cross_project_source`
- `tests/test_execution_packs.py::test_plan_hash_and_resource_parameters_are_enforced`
- `tests/test_context_backends.py::test_backend_transport_refuses_redirect_and_missing_credentials`
- `tests/test_context_backends.py::test_backend_refuses_shared_scopes_and_foreign_source_ingestion[hindsight]`
- `tests/test_context_backends.py::test_backend_refuses_shared_scopes_and_foreign_source_ingestion[openviking]`
- `tests/test_context_backends.py::test_backend_binding_change_cannot_redirect_existing_deletions[hindsight]`
- `tests/test_context_backends.py::test_backend_binding_change_cannot_redirect_existing_deletions[openviking]`
- `tests/test_scenario_runner.py::test_scope_and_context_budget_checks_do_not_claim_execution_rollover`
- `tests/test_harness_surfaces.py::test_mcp_new_project_execution_and_scoped_tools`
- `tests/test_harness_surfaces.py::test_mcp_rejects_nonobject_arguments[value0]`
- `tests/test_harness_surfaces.py::test_mcp_rejects_nonobject_arguments[False]`
- `tests/test_harness_surfaces.py::test_mcp_rejects_nonobject_arguments[0]`
- `tests/test_harness_surfaces.py::test_mcp_rejects_nonobject_arguments[]`
- `tests/test_harness_surfaces.py::test_mcp_rejects_nonobject_arguments[None]`
- `tests/test_harness_surfaces.py::test_mcp_validates_advertised_types[arguments0]`
- `tests/test_harness_surfaces.py::test_mcp_validates_advertised_types[arguments1]`
- `tests/test_harness_surfaces.py::test_mcp_validates_advertised_types[arguments2]`
- `tests/test_server_workers.py::test_http_worker_credentials_cannot_access_operator_routes`
- `tests/test_server_workers.py::test_http_worker_scope_hides_other_hosts_projects_and_lease_tokens`
- `tests/test_server_workers.py::test_http_browser_origins_cannot_use_host_or_operator_tokens[null]`
- `tests/test_server_workers.py::test_http_browser_origins_cannot_use_host_or_operator_tokens[http://127.0.0.1]`
- `tests/test_server_workers.py::test_http_browser_origins_cannot_use_host_or_operator_tokens[https://untrusted.example]`
- `tests/test_server_workers.py::test_http_worker_body_bounds_reject_before_mutation[0]`
- `tests/test_server_workers.py::test_http_worker_body_bounds_reject_before_mutation[-1]`
- `tests/test_server_workers.py::test_http_worker_body_bounds_reject_before_mutation[2097153]`
- `tests/test_server_workers.py::test_http_worker_rejects_nonobject_arguments[args0]`
- `tests/test_server_workers.py::test_http_worker_rejects_nonobject_arguments[False]`
- `tests/test_server_workers.py::test_http_worker_rejects_nonobject_arguments[0]`
- `tests/test_server_workers.py::test_http_worker_rejects_nonobject_arguments[]`
- `tests/test_server_workers.py::test_http_worker_rejects_nonobject_arguments[not-an-object]`
- `tests/test_server_workers.py::test_http_worker_bad_envelopes_have_no_effect[{broken]`
- `tests/test_server_workers.py::test_http_worker_bad_envelopes_have_no_effect[[]]`
- `tests/test_server_workers.py::test_http_worker_bad_envelopes_have_no_effect[{"host_id":"host-a","action":"heartbeat","unexpected":1}]`
- `tests/test_runtime_backends.py::test_worker_control_host_project_and_lease_scope`
- `tests/test_runtime_backends.py::test_worker_control_transport_rejects_unsafe_origins`
- `tests/test_report_deliverables.py::test_unavailable_evidence_never_exports_as_accepted[scope]`
- `tests/test_report_deliverables.py::test_unavailable_evidence_never_exports_as_accepted[archive]`
- `tests/test_report_deliverables.py::test_export_scope_excludes_unrelated_outcomes_and_cross_project_link`
- `tests/test_memory_integration_contracts.py::test_basic_memory_rejects_scope_escape_before_execution[../other.md]`
- `tests/test_memory_integration_contracts.py::test_basic_memory_rejects_scope_escape_before_execution[/tmp/other.md]`
- `tests/test_memory_integration_contracts.py::test_basic_memory_rejects_scope_escape_before_execution[memory://other/secret.md]`
- `tests/test_memory_integration_contracts.py::test_basic_memory_rejects_scope_escape_before_execution[other\\secret.md]`
- `tests/test_memory_integration_contracts.py::test_basic_memory_rejects_scope_escape_before_execution[notes/%2e%2e/x.md]`
- `tests/test_memory_integration_contracts.py::test_basic_memory_rejects_scope_escape_before_execution[-x.md]`
- `tests/test_memory_integration_contracts.py::test_basic_memory_rejects_scope_escape_before_execution[a//x.md]`
- `tests/test_memory_integration_contracts.py::test_basic_memory_rejects_scope_escape_before_execution[note]`
- `tests/test_memory_integration_contracts.py::test_basic_memory_forces_private_local_configuration`
- `tests/test_memory_integration_contracts.py::test_qmd_malformed_and_cross_project_results_fail_closed[payload0]`
- `tests/test_memory_integration_contracts.py::test_qmd_malformed_and_cross_project_results_fail_closed[payload1]`
- `tests/test_memory_integration_contracts.py::test_qmd_malformed_and_cross_project_results_fail_closed[payload2]`
- `tests/test_memory_integration_contracts.py::test_qmd_malformed_and_cross_project_results_fail_closed[payload3]`
- `tests/test_memory_integration_contracts.py::test_qmd_malformed_and_cross_project_results_fail_closed[payload4]`
- `tests/test_memory_integration_contracts.py::test_lcm_rejects_scope_overrides_and_silently_dropped_messages[expand-args0]`
- `tests/test_memory_integration_contracts.py::test_lcm_rejects_scope_overrides_and_silently_dropped_messages[expand-args1]`
- `tests/test_memory_integration_contracts.py::test_lcm_rejects_scope_overrides_and_silently_dropped_messages[capture-args2]`
- `tests/test_memory_integration_contracts.py::test_lcm_rejects_scope_overrides_and_silently_dropped_messages[capture-args3]`
- `tests/test_memory_integration_contracts.py::test_lcm_rejects_scope_overrides_and_silently_dropped_messages[summarize-args4]`
- `tests/test_app_integrations.py::test_new_dispatch_namespaces_use_exact_method_allowlists[backend._bound]`
- `tests/test_app_integrations.py::test_new_dispatch_namespaces_use_exact_method_allowlists[backend.ingest.extra]`
- `tests/test_app_integrations.py::test_new_dispatch_namespaces_use_exact_method_allowlists[backend.request]`
- `tests/test_app_integrations.py::test_new_dispatch_namespaces_use_exact_method_allowlists[execution._execute]`
- `tests/test_app_integrations.py::test_new_dispatch_namespaces_use_exact_method_allowlists[execution.run.extra]`
- `tests/test_app_integrations.py::test_new_dispatch_namespaces_use_exact_method_allowlists[execution.__class__]`
- `tests/test_app_integrations.py::test_new_dispatch_namespaces_use_exact_method_allowlists[fleet._host]`
- `tests/test_app_integrations.py::test_new_dispatch_namespaces_use_exact_method_allowlists[fleet.worker_request]`
- `tests/test_app_integrations.py::test_new_dispatch_namespaces_use_exact_method_allowlists[fleet.admit]`
- `tests/test_app_integrations.py::test_new_dispatch_namespaces_use_exact_method_allowlists[fleet.register.extra]`
- `tests/test_app_integrations.py::test_dispatch_rejects_malformed_envelopes[None-args0]`
- `tests/test_app_integrations.py::test_dispatch_rejects_malformed_envelopes[action1-args1]`
- `tests/test_app_integrations.py::test_dispatch_rejects_malformed_envelopes[-args2]`
- `tests/test_app_integrations.py::test_dispatch_rejects_malformed_envelopes[backend.status-args3]`
- `tests/test_app_integrations.py::test_dispatch_rejects_malformed_envelopes[backend.status-token]`
- `tests/test_app_integrations.py::test_fleet_operator_surface_does_not_expose_registration_token`
- `tests/test_app_integrations.py::test_invalid_temporal_configuration_rejected_without_network[settings0]`
- `tests/test_app_integrations.py::test_invalid_temporal_configuration_rejected_without_network[settings1]`
- `tests/test_app_integrations.py::test_invalid_temporal_configuration_rejected_without_network[settings2]`
- `tests/test_app_integrations.py::test_invalid_temporal_configuration_rejected_without_network[settings3]`
- `tests/test_app_integrations.py::test_invalid_temporal_configuration_rejected_without_network[settings4]`
- `tests/test_app_integrations.py::test_invalid_temporal_configuration_rejected_without_network[settings5]`
- `tests/test_app_integrations.py::test_invalid_temporal_configuration_rejected_without_network[settings6]`
- `tests/test_app_integrations.py::test_invalid_temporal_configuration_rejected_without_network[settings7]`
- `tests/test_app_integrations.py::test_invalid_temporal_configuration_rejected_without_network[settings8]`
- `tests/test_app_integrations.py::test_invalid_temporal_configuration_rejected_without_network[settings9]`
- `tests/test_app_integrations.py::test_invalid_temporal_configuration_rejected_without_network[settings10]`
- `tests/test_app_integrations.py::test_invalid_temporal_configuration_rejected_without_network[settings11]`
- `tests/test_app_integrations.py::test_invalid_temporal_configuration_rejected_without_network[settings12]`
- `tests/test_app_integrations.py::test_invalid_temporal_configuration_rejected_without_network[settings13]`
- `tests/test_app_integrations.py::test_invalid_temporal_configuration_rejected_without_network[settings14]`
- `tests/test_app_integrations.py::test_invalid_temporal_configuration_rejected_without_network[settings15]`
- `tests/test_app_integrations.py::test_temporal_auth_configuration_does_not_read_credentials_or_require_sdk`
- `tests/test_memory_integration_contracts.py::test_basic_memory_read_cannot_resolve_another_project_identifier`

### PF60

**Authorized schedules and clear updates** — `E-PF60`

Create and pause an authorized schedule while the app is intermittently offline.

- **Successful path:** Missed intervals coalesce once with a visible durable update and clear next run.
- **Failure path:** An unconfigured external destination is never contacted.
- **Edge case:** DST/calendar and overlap policy must be explicit when advertised; UTC-only support is labelled.
- **Automated scope:** UTC-interval coalescing only; calendar/DST and external delivery are not implied.
- **Remaining limitation:** UTC interval schedules only; calendar/DST/overlap policies and external destinations remain.
- **Required observations:** `journey`.
- **Prerequisites:** Installed Workbench and isolated website/mobile/research project state.
- **Workload tags:** `schedules`.

Mapped pytest evidence:

- `tests/test_intake.py::test_schedule_coalesces_missed_ticks_without_duplicates`

### PF61

**Optional OpenViking replacement context backend** — `E-PF61`

Select OpenViking as the project context backend and complete a source lifecycle.

- **Successful path:** Ingest/search/read retain scope and source identifiers through restart.
- **Failure path:** Reject unknown project roots, path traversal and provider failure without a hidden fallback.
- **Edge case:** Delete/revoke a source and confirm stale context cannot be returned.
- **Automated scope:** Real loopback HTTP contract servers exercise scoped ingest/recall/read/delete, idempotency, uncertain mutations, malformed responses, provenance and retained deletion handles. These are controlled protocol fixtures, not the named running service or a model.
- **Remaining limitation:** Project-scoped adapter protocol behavior is covered by loopback contract tests. A real backend deployment, its inference provider, consolidation semantics, restart recovery and external retention remain required observations; provider backup deletion is not inferred from local purge.
- **Required observations:** `openviking`, `retention`.
- **Prerequisites:** Configured OpenViking server and its explicitly selected local/subscription/API model provider; Configured real context stores plus disposable backup/index data.
- **Workload tags:** `backends`, `context`.

Mapped pytest evidence:

- `tests/test_context_backends.py::test_backend_round_trip_preserves_original_and_marks_derived[openviking]`
- `tests/test_context_backends.py::test_backend_does_not_leak_unmanaged_or_revoked_results[openviking]`
- `tests/test_context_backends.py::test_remote_deletion_remains_possible_after_local_purge[openviking]`
- `tests/test_context_backends.py::test_backend_requires_explicit_project_and_write_configuration[openviking]`
- `tests/test_context_backends.py::test_backend_binding_change_cannot_redirect_existing_deletions[openviking]`
- `tests/test_context_backends.py::test_backend_refuses_shared_scopes_and_foreign_source_ingestion[openviking]`
- `tests/test_context_backends.py::test_backend_uncertain_write_is_not_blindly_retried[openviking]`
- `tests/test_context_backends.py::test_backend_readback_detects_remote_content_changes[openviking]`
- `tests/test_context_backends.py::test_openviking_reports_incomplete_indexing`
- `tests/test_context_backends.py::test_backend_rejects_project_root_prefix_collision`
- `tests/test_context_backends.py::test_backend_transport_rejects_unsafe_origins[http://external.example]`
- `tests/test_context_backends.py::test_backend_transport_rejects_unsafe_origins[https://user:pass@example.com]`
- `tests/test_context_backends.py::test_backend_transport_rejects_unsafe_origins[https://example.com/base]`
- `tests/test_context_backends.py::test_backend_transport_rejects_unsafe_origins[https://example.com?secret=x]`
- `tests/test_context_backends.py::test_backend_transport_rejects_unsafe_origins[https://example.com/#frag]`
- `tests/test_context_backends.py::test_backend_transport_rejects_unsafe_origins[file:///tmp/db]`
- `tests/test_context_backends.py::test_backend_transport_rejects_unsafe_origins[https://example.com:bad]`
- `tests/test_context_backends.py::test_backend_transport_rejects_unsafe_origins[http://]`
- `tests/test_context_backends.py::test_backend_rejects_unsafe_viking_roots[viking://resources/]`
- `tests/test_context_backends.py::test_backend_rejects_unsafe_viking_roots[viking://resources/project/../other]`
- `tests/test_context_backends.py::test_backend_rejects_unsafe_viking_roots[viking://resources/project/%2e%2e]`
- `tests/test_context_backends.py::test_backend_rejects_unsafe_viking_roots[viking://resources/project?other=x]`
- `tests/test_context_backends.py::test_backend_rejects_unsafe_viking_roots[viking://resources/project\\other]`
- `tests/test_context_backends.py::test_backend_rejects_unsafe_viking_roots[https://other.example/resource]`
- `tests/test_context_backends.py::test_backend_transport_refuses_redirect_and_missing_credentials`
- `tests/test_context_backends.py::test_backend_delete_timeout_needs_reconciliation[openviking]`
- `tests/test_context_backends.py::test_backend_recall_rejects_invalid_limits[0]`
- `tests/test_context_backends.py::test_backend_recall_rejects_invalid_limits[101]`
- `tests/test_context_backends.py::test_backend_recall_rejects_invalid_limits[True]`
- `tests/test_context_backends.py::test_backend_recall_rejects_invalid_limits[8]`
- `tests/test_context_backends.py::test_backend_concurrent_ingest_does_not_duplicate_writes[openviking]`
- `tests/test_context_backends.py::test_backend_unexpected_write_schema_records_uncertain_mutation[openviking]`
- `tests/test_context_backends.py::test_backend_unexpected_deletion_schema_does_not_claim_success`
- `tests/test_context_backends.py::test_backend_recall_rejects_malformed_identity[openviking]`
- `tests/test_context_backends.py::test_backend_absent_delete_is_idempotent[openviking]`
- `tests/test_context_backends.py::test_backend_read_rejects_oversize_response[openviking]`
- `tests/test_context_backends.py::test_backend_transport_rejects_invalid_ipv6_and_boolean_timeout`
- `tests/test_context_backends.py::test_backend_recall_omits_superseded_source_versions[openviking]`
- `tests/test_context_backends.py::test_openviking_uses_service_token_header`
- `tests/test_app_integrations.py::test_context_backend_actions_reach_http_and_preserve_receipts[openviking]`

### PF62

**Optional Hindsight experience memory** — `E-PF62`

Use a Hindsight bank for experience memory and source-linked reflection.

- **Successful path:** Retain/recall/reflect respect the configured project bank and provenance.
- **Failure path:** A missing provider, invalid bank or cross-project attempt fails clearly.
- **Edge case:** Derived reflection stays distinguishable from original facts after source deletion.
- **Automated scope:** Real loopback HTTP contract servers exercise scoped ingest/recall/read/delete, idempotency, uncertain mutations, malformed responses, provenance and retained deletion handles. These are controlled protocol fixtures, not the named running service or a model.
- **Remaining limitation:** Project-scoped adapter protocol behavior is covered by loopback contract tests. A real backend deployment, its inference provider, consolidation semantics, restart recovery and external retention remain required observations; provider backup deletion is not inferred from local purge.
- **Required observations:** `hindsight`, `retention`.
- **Prerequisites:** Configured Hindsight server and its explicitly selected local/subscription/API model provider; Configured real context stores plus disposable backup/index data.
- **Workload tags:** `backends`, `context`.

Mapped pytest evidence:

- `tests/test_context_backends.py::test_backend_round_trip_preserves_original_and_marks_derived[hindsight]`
- `tests/test_context_backends.py::test_backend_does_not_leak_unmanaged_or_revoked_results[hindsight]`
- `tests/test_context_backends.py::test_remote_deletion_remains_possible_after_local_purge[hindsight]`
- `tests/test_context_backends.py::test_backend_requires_explicit_project_and_write_configuration[hindsight]`
- `tests/test_context_backends.py::test_backend_binding_change_cannot_redirect_existing_deletions[hindsight]`
- `tests/test_context_backends.py::test_backend_refuses_shared_scopes_and_foreign_source_ingestion[hindsight]`
- `tests/test_context_backends.py::test_backend_uncertain_write_is_not_blindly_retried[hindsight]`
- `tests/test_context_backends.py::test_backend_readback_detects_remote_content_changes[hindsight]`
- `tests/test_context_backends.py::test_backend_transport_rejects_unsafe_origins[http://external.example]`
- `tests/test_context_backends.py::test_backend_transport_rejects_unsafe_origins[https://user:pass@example.com]`
- `tests/test_context_backends.py::test_backend_transport_rejects_unsafe_origins[https://example.com/base]`
- `tests/test_context_backends.py::test_backend_transport_rejects_unsafe_origins[https://example.com?secret=x]`
- `tests/test_context_backends.py::test_backend_transport_rejects_unsafe_origins[https://example.com/#frag]`
- `tests/test_context_backends.py::test_backend_transport_rejects_unsafe_origins[file:///tmp/db]`
- `tests/test_context_backends.py::test_backend_transport_rejects_unsafe_origins[https://example.com:bad]`
- `tests/test_context_backends.py::test_backend_transport_rejects_unsafe_origins[http://]`
- `tests/test_context_backends.py::test_backend_transport_refuses_redirect_and_missing_credentials`
- `tests/test_context_backends.py::test_backend_delete_timeout_needs_reconciliation[hindsight]`
- `tests/test_context_backends.py::test_backend_recall_rejects_invalid_limits[0]`
- `tests/test_context_backends.py::test_backend_recall_rejects_invalid_limits[101]`
- `tests/test_context_backends.py::test_backend_recall_rejects_invalid_limits[True]`
- `tests/test_context_backends.py::test_backend_recall_rejects_invalid_limits[8]`
- `tests/test_context_backends.py::test_backend_concurrent_ingest_does_not_duplicate_writes[hindsight]`
- `tests/test_context_backends.py::test_backend_unexpected_write_schema_records_uncertain_mutation[hindsight]`
- `tests/test_context_backends.py::test_backend_recall_rejects_malformed_identity[hindsight]`
- `tests/test_context_backends.py::test_backend_absent_delete_is_idempotent[hindsight]`
- `tests/test_context_backends.py::test_backend_read_rejects_oversize_response[hindsight]`
- `tests/test_context_backends.py::test_backend_transport_rejects_invalid_ipv6_and_boolean_timeout`
- `tests/test_context_backends.py::test_backend_recall_omits_superseded_source_versions[hindsight]`
- `tests/test_app_integrations.py::test_context_backend_actions_reach_http_and_preserve_receipts[hindsight]`

### PF63

**Optional Temporal execution backend** — `E-PF63`

Replace the coordinator with Temporal and run/recover an actual workflow.

- **Successful path:** Only Temporal owns the selected execution and its workflow IDs persist.
- **Failure path:** Reject dual ownership and duplicate effects after an activity retry.
- **Edge case:** Cancel during a dependency wait or worker restart and reconcile the final state.
- **Automated scope:** Real Temporal devserver/SDK tests cover parallel fixture activities, DAG dependencies, idempotency, controller recovery, cancellation, a two-generation checkpoint signal and a separate worker store over loopback HTTP. Official server-dependent cases skip without its binary. Explicit CLI process fixtures cover I/O and stop receipts. These are not actual LLM responses, five fresh model sessions or physical multi-host/TLS qualification.
- **Remaining limitation:** Temporal workflow/activity implementation and actual service tests are present; model-driven execution, fresh-model context rollover and operational recovery must still be observed separately before qualification.
- **Required observations:** `temporal`, `workers`.
- **Prerequisites:** Running Temporal service and registered Workbench workflow/activity worker; Authenticated model harness and an actual coordinator-owned worker run.
- **Workload tags:** `backends`, `recovery`.

Mapped pytest evidence:

- `tests/test_runtime_backends.py::test_runtime_switch_rejects_active_or_uncertain_process`
- `tests/test_runtime_backends.py::test_temporal_graph_bounds_dependencies_and_cycle`
- `tests/test_runtime_backends.py::test_temporal_real_service_dag_idempotency_recovery`
- `tests/test_runtime_backends.py::test_temporal_real_service_cancellation_stops_activity`
- `tests/test_server_workers.py::test_http_completed_worker_receipts_need_observation_and_independent_review`
- `tests/test_runtime_backends.py::test_temporal_real_service_checkpoint_rollover`
- `tests/test_runtime_backends.py::test_temporal_separate_worker_store_over_http`
- `tests/test_runtime_backends.py::test_worker_control_real_http_host_auth_and_scope`
- `tests/test_runtime_backends.py::test_cli_process_contract_stdin_receipt_and_cancel`
- `tests/test_engine_preflight.py::test_existing_bridge_cannot_dispatch_after_temporal_is_selected`
- `tests/test_engine_preflight.py::test_direct_delivery_also_checks_the_runtime_fence`
- `tests/test_app_integrations.py::test_coordinator_dispatch_preserves_paperclip_default`
- `tests/test_app_integrations.py::test_temporal_dispatch_routes_health_tick_and_observation_reconciliation`
- `tests/test_app_integrations.py::test_temporal_lifecycle_never_falls_back_to_paperclip`
- `tests/test_app_integrations.py::test_paperclip_lifecycle_keeps_existing_request_contract`
- `tests/test_app_integrations.py::test_dispatch_runtime_switch_is_fenced_by_active_lease[coordinator.tick-args0]`
- `tests/test_app_integrations.py::test_dispatch_runtime_switch_is_fenced_by_active_lease[lifecycle.request-args1]`
- `tests/test_app_integrations.py::test_configuration_requires_exactly_one_coordinator[None]`
- `tests/test_app_integrations.py::test_configuration_requires_exactly_one_coordinator[coordinator1]`
- `tests/test_app_integrations.py::test_configuration_requires_exactly_one_coordinator[coordinator2]`
- `tests/test_app_integrations.py::test_configuration_requires_exactly_one_coordinator[coordinator3]`

### PF64

**Optional multi-host and larger fleets** — `E-PF64`

Dispatch work to two actual hosts and interrupt their connectivity.

- **Successful path:** Tasks have one active owner and artifact/context exchange remains scoped.
- **Failure path:** Reject stale leases, unavailable hosts and duplicate completion receipts.
- **Edge case:** A recovered host cannot overwrite a successor run after failover.
- **Automated scope:** Fleet admission and actual loopback HTTP tests cover host/project/lease authorization, interactive reservations, expiry and uncertain ownership. An actual Temporal worker uses a separate local store through HTTP. Both stores and fixture activities run on one machine; two real hosts, physical isolation and model-worker behavior remain unqualified.
- **Remaining limitation:** Host admission and lease controls are tested locally. Two independent worker hosts, credential isolation, network partitions and duplicate-effect reconciliation remain live qualification gates.
- **Required observations:** `multi_host`, `isolation`, `capacity`.
- **Prerequisites:** Two isolated configured worker hosts and an authenticated coordinator; Configured OS/container isolation and scoped worker credentials; Running coordinator/worker pool and a bounded load generator.
- **Workload tags:** `backends`, `load`, `recovery`.

Mapped pytest evidence:

- `tests/test_runtime_backends.py::test_fleet_registration_scope_and_no_secrets`
- `tests/test_runtime_backends.py::test_fleet_capacity_reservation_and_idempotency`
- `tests/test_runtime_backends.py::test_fleet_expired_lease_keeps_capacity_until_stop`
- `tests/test_runtime_backends.py::test_fleet_revision_and_cancel_fence_renewal`
- `tests/test_server_workers.py::test_http_worker_scope_hides_other_hosts_projects_and_lease_tokens`
- `tests/test_runtime_backends.py::test_temporal_separate_worker_store_over_http`
- `tests/test_runtime_backends.py::test_worker_control_real_http_host_auth_and_scope`
- `tests/test_runtime_backends.py::test_worker_control_host_project_and_lease_scope`
- `tests/test_runtime_backends.py::test_worker_control_transport_rejects_unsafe_origins`
- `tests/test_app_integrations.py::test_fleet_operator_surface_does_not_expose_registration_token`

### PF65

**Basic Memory editable knowledge and recall** — `E-PF65`

Edit Basic Memory notes from separate processes and retrieve them by project.

- **Successful path:** Writes and fresh-process recall preserve the current note version.
- **Failure path:** Reject a stale checksum overwrite and ungranted project access.
- **Edge case:** Concurrent edits, restart and deletion leave a reconciled version/retention record.
- **Automated scope:** Command contracts enforce project-local configuration, path boundaries and checksum conflicts. An opt-in real Basic Memory test registers isolated projects, races two guarded writes and checks readback and foreign-project search. Missing the actual package skips the positive test and blocks this feature.
- **Remaining limitation:** Broader fresh-process recall, synchronization with external editors and retention/deletion across indexed or backed-up notes remain observations beyond the bounded write-conflict probe.
- **Required observations:** `basic_memory`, `retention`.
- **Prerequisites:** Installed Basic Memory with isolated project stores; Configured real context stores plus disposable backup/index data.
- **Workload tags:** `context`.

Mapped pytest evidence:

- `tests/test_memory_integration_contracts.py::test_basic_memory_checksum_guards_concurrent_replacement`
- `tests/test_memory_integration_contracts.py::test_basic_memory_rejects_scope_escape_before_execution[../other.md]`
- `tests/test_memory_integration_contracts.py::test_basic_memory_rejects_scope_escape_before_execution[/tmp/other.md]`
- `tests/test_memory_integration_contracts.py::test_basic_memory_rejects_scope_escape_before_execution[memory://other/secret.md]`
- `tests/test_memory_integration_contracts.py::test_basic_memory_rejects_scope_escape_before_execution[other\\secret.md]`
- `tests/test_memory_integration_contracts.py::test_basic_memory_rejects_scope_escape_before_execution[notes/%2e%2e/x.md]`
- `tests/test_memory_integration_contracts.py::test_basic_memory_rejects_scope_escape_before_execution[-x.md]`
- `tests/test_memory_integration_contracts.py::test_basic_memory_rejects_scope_escape_before_execution[a//x.md]`
- `tests/test_memory_integration_contracts.py::test_basic_memory_rejects_scope_escape_before_execution[note]`
- `tests/test_memory_integration_contracts.py::test_basic_memory_forces_private_local_configuration`
- `tests/test_memory_integration_contracts.py::test_basic_memory_malformed_or_conflicting_results_are_errors[payload0]`
- `tests/test_memory_integration_contracts.py::test_basic_memory_malformed_or_conflicting_results_are_errors[payload1]`
- `tests/test_memory_integration_contracts.py::test_basic_memory_malformed_or_conflicting_results_are_errors[payload2]`
- `tests/test_memory_integration_contracts.py::test_basic_memory_malformed_or_conflicting_results_are_errors[payload3]`
- `tests/test_memory_integration_contracts.py::test_real_basic_memory_concurrent_revision_guards_and_project_isolation`
- `tests/test_memory_integration_contracts.py::test_basic_memory_read_cannot_resolve_another_project_identifier`

### PF66

**Open Notebook source-workspace integration** — `E-PF66`

Add research sources to Open Notebook and ask a sourced question.

- **Successful path:** The returned answer identifies the correct notebook and original sources.
- **Failure path:** Unsupported subscription bridging, missing provider and unavailable sources block honestly.
- **Edge case:** Restart or partial ingestion resumes without duplicating or silently omitting sources.
- **Automated scope:** Authentication-boundary rejection only; no real source ingestion or generation implied.
- **Remaining limitation:** Real service/generation run remains; no qualified subscription-only inference bridge.
- **Required observations:** `notebook`, `writing`.
- **Prerequisites:** Configured Open Notebook service with an explicitly supported inference provider; Known-answer source corpus and independent editorial review rubric.
- **Workload tags:** `research`, `backends`.

Mapped pytest evidence:

- `tests/test_surfaces.py::test_named_integrations_never_silently_use_subscription_as_api`

### PF67

**GPT Researcher bounded investigation integration** — `E-PF67`

Run GPT Researcher, then request contradictory evidence and an expanded report.

- **Successful path:** The report cites verified primary sources and distinguishes disagreement from resolution.
- **Failure path:** Search/provider failures and unsupported auth cannot yield a claimed completed report.
- **Edge case:** A later counterargument updates the same project without dropping the original question.
- **Automated scope:** Unsupported-auth rejection only; actual research and source verification need a run.
- **Remaining limitation:** Real service/generation run remains; no qualified subscription-only inference bridge.
- **Required observations:** `researcher`, `writing`.
- **Prerequisites:** Installed/configured GPT Researcher with working search and inference providers; Known-answer source corpus and independent editorial review rubric.
- **Workload tags:** `research`, `contradiction`.
- **Workload instructions:** `S03`, `S06`, `S25`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_surfaces.py::test_named_integrations_never_silently_use_subscription_as_api`

### PF68

**Automatic request-to-team planning** — `E-PF68`

Submit website, mobile and research requests without preselecting teams.

- **Successful path:** The system selects justified roles and dependencies for each current request.
- **Failure path:** Missing capability or ambiguous intent produces a visible correction/block.
- **Edge case:** Follow-up features revise the same project plan and retain unaffected constraints.
- **Automated scope:** Template compilation and selection guards; automatic task interpretation requires held-out/live evidence.
- **Remaining limitation:** Real coordinator/harness conformance, native child ownership and broad selection evaluation remain.
- **Required observations:** `workers`, `benchmark`.
- **Prerequisites:** Authenticated model harness and an actual coordinator-owned worker run; Qualified profiles and a versioned held-out benchmark with expected outcomes.
- **Workload tags:** `website`, `mobile`, `research`, `amendment`.
- **Workload instructions:** `S01`, `S02`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_execution.py::test_parallel_team_has_distinct_peers_and_review_dependencies`
- `tests/test_execution.py::test_model_override_and_missing_qualification`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Create a website for HarborWeb with accessible event booking.-code]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Create a mobile app for PocketTasks with offline habits.-code]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Report the architecture review.-report]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Add dark mode to the website.-code]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Research offline-first mobile architecture and provide a report.-research]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Write code that produces a report.-code]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Create a plan for building a website.-design]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Please summarize the research about code review.-report]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Fix the failing test without changing the research report.-code]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Compare two designs before implementing either.-research]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Design the architecture for a mobile app.-design]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Create a report from the architecture decisions.-report]`
- `tests/test_policy_scenarios.py::test_website_and_mobile_use_engineer_and_reviewer`
- `tests/test_policy_scenarios.py::test_manifest_context_prefix_keeps_requested_work[S10-code]`
- `tests/test_policy_scenarios.py::test_manifest_context_prefix_keeps_requested_work[S11-code]`
- `tests/test_policy_scenarios.py::test_manifest_context_prefix_keeps_requested_work[S16-code]`
- `tests/test_policy_scenarios.py::test_manifest_context_prefix_keeps_requested_work[S17-code]`
- `tests/test_policy_scenarios.py::test_manifest_context_prefix_keeps_requested_work[S25-report]`

### PF69

**Evaluated model and effort selection with explanation** — `E-PF69`

Compare qualified models/efforts on simple and complex tasks within a budget.

- **Successful path:** Selection records why a profile fits and respects explicit overrides and measured capability.
- **Failure path:** An unqualified or unsupported profile cannot be selected merely because it is cheap.
- **Edge case:** Expired qualification or provider failure does not silently trigger a paid fallback.
- **Automated scope:** Configured-priority selection and effort mapping; it is not an inferred best-model ranking.
- **Remaining limitation:** Real coordinator/harness conformance, native child ownership and broad selection evaluation remain.
- **Required observations:** `models`, `benchmark`.
- **Prerequisites:** Signed-in official CLI or explicitly configured API/local model provider; Qualified profiles and a versioned held-out benchmark with expected outcomes.
- **Workload tags:** `models`.

Mapped pytest evidence:

- `tests/test_execution.py::test_model_override_and_missing_qualification`
- `tests/test_execution.py::test_effort_maps_to_pinned_codex_field`
- `tests/test_boundaries.py::test_profile_change_invalidates_qualification`
- `tests/test_harness_surfaces.py::test_failed_reprobe_revokes_old_qualification`
- `tests/test_harness_surfaces.py::test_probe_requires_exact_successful_assistant_completion[echo]`
- `tests/test_harness_surfaces.py::test_probe_requires_exact_successful_assistant_completion[refusal]`
- `tests/test_harness_surfaces.py::test_probe_requires_exact_successful_assistant_completion[incomplete]`
- `tests/test_harness_surfaces.py::test_probe_requires_exact_successful_assistant_completion[failed_turn]`
- `tests/test_harness_surfaces.py::test_probe_requires_exact_successful_assistant_completion[success]`
- `tests/test_engine_preflight.py::test_missing_qualification_is_rejected_before_creating_remote_project`
- `tests/test_engine_preflight.py::test_saved_profile_qualification_is_rechecked_when_receipt_expires`

### PF70

**Capability registry and workflow compilation** — `E-PF70`

Compile a workflow requiring installed skills, tools and backend capabilities.

- **Successful path:** The execution packet identifies exact procedure versions and required capabilities.
- **Failure path:** Missing explicitly requested skills or oversized packets block before unsafe dispatch.
- **Edge case:** An upstream version change invalidates a stale plan until recompiled and reviewed.
- **Automated scope:** Selected compilation guards; full capability compatibility and automatic activation need observed checks.
- **Remaining limitation:** Real coordinator/harness conformance, native child ownership and broad selection evaluation remain.
- **Required observations:** `procedures`, `coordinator`.
- **Prerequisites:** Installed Superpowers, Elements of Style and Humanizer source checkouts; Running configured Paperclip or exclusive qualified replacement coordinator.
- **Workload tags:** `skills`, `backends`.
- **Workload instructions:** `S28`. References are planned coverage, not proof of execution.

Mapped pytest evidence:

- `tests/test_boundaries.py::test_explicit_missing_superpowers_is_visible_blocker`
- `tests/test_execution.py::test_oversized_packet_is_not_silently_truncated`
- `tests/test_execution.py::test_plan_cycles_and_child_limits`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Create a website for HarborWeb with accessible event booking.-code]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Create a mobile app for PocketTasks with offline habits.-code]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Report the architecture review.-report]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Add dark mode to the website.-code]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Research offline-first mobile architecture and provide a report.-research]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Write code that produces a report.-code]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Create a plan for building a website.-design]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Please summarize the research about code review.-report]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Fix the failing test without changing the research report.-code]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Compare two designs before implementing either.-research]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Design the architecture for a mobile app.-design]`
- `tests/test_policy_scenarios.py::test_requested_action_drives_workflow[Create a report from the architecture decisions.-report]`
- `tests/test_harness_surfaces.py::test_mcp_new_project_execution_and_scoped_tools`
- `tests/test_engine_preflight.py::test_saved_plan_is_validated_again_before_remote_mutation[cycle]`
- `tests/test_engine_preflight.py::test_saved_plan_is_validated_again_before_remote_mutation[missing_dependency]`
- `tests/test_engine_preflight.py::test_saved_plan_is_validated_again_before_remote_mutation[wrong_revision]`
- `tests/test_engine_preflight.py::test_saved_plan_is_validated_again_before_remote_mutation[missing_role]`
- `tests/test_engine_preflight.py::test_saved_plan_is_validated_again_before_remote_mutation[budget_escalation]`
- `tests/test_engine_preflight.py::test_saved_plan_is_validated_again_before_remote_mutation[skill_hash]`
- `tests/test_engine_preflight.py::test_saved_acyclic_nodes_are_dispatched_in_dependency_order`
- `tests/test_engine_preflight.py::test_amendment_during_preflight_prevents_remote_mutations`
- `tests/test_engine_preflight.py::test_every_node_packet_is_checked_before_any_remote_mutation`
- `tests/test_app_integrations.py::test_new_dispatch_namespaces_use_exact_method_allowlists[backend._bound]`
- `tests/test_app_integrations.py::test_new_dispatch_namespaces_use_exact_method_allowlists[backend.ingest.extra]`
- `tests/test_app_integrations.py::test_new_dispatch_namespaces_use_exact_method_allowlists[backend.request]`
- `tests/test_app_integrations.py::test_new_dispatch_namespaces_use_exact_method_allowlists[execution._execute]`
- `tests/test_app_integrations.py::test_new_dispatch_namespaces_use_exact_method_allowlists[execution.run.extra]`
- `tests/test_app_integrations.py::test_new_dispatch_namespaces_use_exact_method_allowlists[execution.__class__]`
- `tests/test_app_integrations.py::test_new_dispatch_namespaces_use_exact_method_allowlists[fleet._host]`
- `tests/test_app_integrations.py::test_new_dispatch_namespaces_use_exact_method_allowlists[fleet.worker_request]`
- `tests/test_app_integrations.py::test_new_dispatch_namespaces_use_exact_method_allowlists[fleet.admit]`
- `tests/test_app_integrations.py::test_new_dispatch_namespaces_use_exact_method_allowlists[fleet.register.extra]`
- `tests/test_app_integrations.py::test_dispatch_rejects_malformed_envelopes[None-args0]`
- `tests/test_app_integrations.py::test_dispatch_rejects_malformed_envelopes[action1-args1]`
- `tests/test_app_integrations.py::test_dispatch_rejects_malformed_envelopes[-args2]`
- `tests/test_app_integrations.py::test_dispatch_rejects_malformed_envelopes[backend.status-args3]`
- `tests/test_app_integrations.py::test_dispatch_rejects_malformed_envelopes[backend.status-token]`
- `tests/test_policy_scenarios.py::test_manifest_context_prefix_keeps_requested_work[S10-code]`
- `tests/test_policy_scenarios.py::test_manifest_context_prefix_keeps_requested_work[S11-code]`
- `tests/test_policy_scenarios.py::test_manifest_context_prefix_keeps_requested_work[S16-code]`
- `tests/test_policy_scenarios.py::test_manifest_context_prefix_keeps_requested_work[S17-code]`
- `tests/test_policy_scenarios.py::test_manifest_context_prefix_keeps_requested_work[S25-report]`

### PF71

**Subscription, API and tested local/hybrid installation profiles** — `E-PF71`

Install and execute the same bounded task in subscription, API and local/hybrid profiles.

- **Successful path:** Each advertised profile has actual model/provider receipts and a verified outcome.
- **Failure path:** Never extract subscription tokens or silently convert a no-key setup to paid API use.
- **Edge case:** Profile changes, expired sign-in and missing optional services remain explicit blockers.
- **Automated scope:** Guardrails and installed core only; each advertised auth/provider combination needs its own real execution.
- **Remaining limitation:** Official CLI no-key path implemented; live subscription/API/local full-profile certification remains.
- **Required observations:** `models`, `packages`, `workers`.
- **Prerequisites:** Signed-in official CLI or explicitly configured API/local model provider; Fresh Linux/macOS environments and build/install tools; Authenticated model harness and an actual coordinator-owned worker run.
- **Workload tags:** `installation`, `models`.

Mapped pytest evidence:

- `tests/test_surfaces.py::test_named_integrations_never_silently_use_subscription_as_api`
- `tests/test_boundaries.py::test_profile_change_invalidates_qualification`
- `tests/test_installed.py::test_installed_prompt_outcome_backup_restore`
- `tests/test_harness_surfaces.py::test_failed_reprobe_revokes_old_qualification`
- `tests/test_harness_surfaces.py::test_probe_requires_exact_successful_assistant_completion[echo]`
- `tests/test_harness_surfaces.py::test_probe_requires_exact_successful_assistant_completion[refusal]`
- `tests/test_harness_surfaces.py::test_probe_requires_exact_successful_assistant_completion[incomplete]`
- `tests/test_harness_surfaces.py::test_probe_requires_exact_successful_assistant_completion[failed_turn]`
- `tests/test_harness_surfaces.py::test_probe_requires_exact_successful_assistant_completion[success]`
- `tests/test_app_integrations.py::test_invalid_temporal_configuration_rejected_without_network[settings0]`
- `tests/test_app_integrations.py::test_invalid_temporal_configuration_rejected_without_network[settings1]`
- `tests/test_app_integrations.py::test_invalid_temporal_configuration_rejected_without_network[settings2]`
- `tests/test_app_integrations.py::test_invalid_temporal_configuration_rejected_without_network[settings3]`
- `tests/test_app_integrations.py::test_invalid_temporal_configuration_rejected_without_network[settings4]`
- `tests/test_app_integrations.py::test_invalid_temporal_configuration_rejected_without_network[settings5]`
- `tests/test_app_integrations.py::test_invalid_temporal_configuration_rejected_without_network[settings6]`
- `tests/test_app_integrations.py::test_invalid_temporal_configuration_rejected_without_network[settings7]`
- `tests/test_app_integrations.py::test_invalid_temporal_configuration_rejected_without_network[settings8]`
- `tests/test_app_integrations.py::test_invalid_temporal_configuration_rejected_without_network[settings9]`
- `tests/test_app_integrations.py::test_invalid_temporal_configuration_rejected_without_network[settings10]`
- `tests/test_app_integrations.py::test_invalid_temporal_configuration_rejected_without_network[settings11]`
- `tests/test_app_integrations.py::test_invalid_temporal_configuration_rejected_without_network[settings12]`
- `tests/test_app_integrations.py::test_invalid_temporal_configuration_rejected_without_network[settings13]`
- `tests/test_app_integrations.py::test_invalid_temporal_configuration_rejected_without_network[settings14]`
- `tests/test_app_integrations.py::test_invalid_temporal_configuration_rejected_without_network[settings15]`
- `tests/test_app_integrations.py::test_temporal_auth_configuration_does_not_read_credentials_or_require_sdk`
- `tests/test_memory_integration_contracts.py::test_lcm_summary_contract_marks_derived_output_and_rejects_provider_drift`
- `tests/test_memory_integration_contracts.py::test_lcm_summary_provider_needs_matching_auth_profile`


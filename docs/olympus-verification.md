# Olympus engineering preview: verification and release gates

Evidence date: 2026-11-02.
This record owns implementation status and test evidence for the first Olympus fork delivery.
The [operator guide](olympus.md) owns installation and current behavior; the [action contract](olympus-api.md) owns the JSON API.

## What this delivery establishes

The implementation has a persistent local coordinator, a standalone terminal, a real Pi extension, scoped agents, portfolio state, bounded peer communication, requirement-bound review, context transfer and backup restoration.
Firstmate remains the runtime manager and is pinned at upstream commit `a4bf5f867bae63e0a44b72ab4a8a7c83c0dea2f8` for this development baseline.
The fork's main branch is not merged or rewritten by this delivery.

| Layer | Reproducible check | Scope |
|---|---|---|
| Domain and integration | `cargo test --workspace --locked` | Real SQLite transactions, concurrent admission, requirements, scopes, dependency cycles, messages, context, recovery, actual Git workspace snapshots and checksums |
| Compiler | `cargo fmt --all -- --check` and `cargo clippy --workspace --all-targets --locked -- -D warnings` | All three Rust crates and test targets |
| Terminal contracts | `node --test tests/olympus-ui.test.mjs` | Shared production view, histories, animation, stale state, safe inputs, resize layout, scroll and wake budgets |
| Process acceptance | `python3 tests/olympus-e2e.py` | Actual daemon, transport, CLI, process death, duplicate-owner refusal, messages, restore, auth and a real pseudo-terminal |
| Pi integration | `OLYMPUS_PI_LIVE=1 bash tests/olympus-pi-live.test.sh` | Pi 1.4.0, TypeBox 1.4.3 and TypeScript 5.9.3; compile and real token-free RPC extension loading |
| Installer | `bash bin/olympus-install.sh --source "$PWD" --prefix /absolute/test/prefix` | Release build, local installation, pinned Pi dependencies and installed CLI paths |
| Shell and workflows | `bash bin/fm-lint.sh` with changed shell paths, then `bash bin/fm-lint-workflows.sh` | Repository owner, ShellCheck 0.11.0 and actionlint 1.7.12 |
| Documentation | `bash bin/fm-doc-audience-check.sh` | Audience inventory, owner pointers and local links |
| Hosted portability | `.github/workflows/olympus.yml` | Linux/macOS matrix and downloadable checksummed build artifacts; a workflow definition is not a passing hosted run |

The original full Firstmate test suite was not rerun for this additive implementation.
No authenticated Claude, Codex or Muse lifecycle test, model quality benchmark, cost benchmark or 72-hour soak was performed in the delivery environment.
The repository's no-mistakes publishing pipeline needs a runnable authenticated pipeline agent and was not run here.
This work is a reviewable draft, not a merge-gate or production-readiness claim.

The slow-runtime process test uses explicitly synthetic scripts solely to prove broker responsiveness and no replay.
It does not count as a successful vendor-harness conformance run.
The Pi test loads the actual installed SDK without submitting a prompt.
The demonstration portfolio is labelled synthetic throughout.

## Local measured run

The final local contract run contains 37 Rust tests, 16 terminal/policy tests and nine process acceptance tests, including real Pi extension registration.
The [machine-readable evidence](verification/olympus-local-evidence.json) records the toolchain and the separate read-load probe.
On a 20-project simulated portfolio, 100 snapshot requests from eight concurrent clients had zero failures, a median of 162.34 ms and a 95th percentile of 165.96 ms in this container.
This is a small local read-path measurement, not a model-worker scaling result or production latency guarantee.

## Blueprint coverage

These IDs map to the previously delivered 29-feature, 41-screen blueprint.
Implemented means the described local behavior exists; it does not imply every production acceptance gate is met.

| ID | Current implementation | Remaining acceptance or expansion |
|---|---|---|
| F01 | Project IDs, aliases, charter, repo binding, completion state | Versioned charter amendment UI |
| F02 | Non-mutating correlation preview; model uses explicit candidates | Broader semantic evaluation set and ambiguous-choice wizard |
| F03 | Immutable requirement versions and stale-output rejection | Full impact visualization |
| F04 | Scoped captain/specialist teams, role instructions and hierarchy limits | Versioned editable role packages and automated team evaluation |
| F05 | Explicit dispatch shape rules and pre-reserved child capacity | Calibrated judgment across real tasks |
| F06 | Cycle-checked task dependencies and parent acceptance gates | Rich producer/consumer artifact-contract editor |
| F07 | Durable threaded messages and separate acknowledgement stages | Full mailbox terminal view and authenticated multi-vendor drills |
| F08 | Atomic global/project admission, child headroom, load/priority/age queue | Weighted fairness and per-host/account limits |
| F09 | Exact-version profiles, verified capabilities and evidence-based ranking | Real Claude/Codex/Muse profiles and account-specific routing |
| F10 | Canonical Firstmate observations, freshness and generation checks | Live backend coverage and measured observation latency |
| F11 | Deduplicated incidents, owner/reason/next-check queue | Timed operator notification connectors |
| F12 | Independent reviewer, current revision and version-bound acceptance | Automated execution of project-specific acceptance suites |
| F13 | Fenced bounded worker recovery, persistent obligations | Automatic chief/manager session restart and multi-host fault drills |
| F14 | Source-backed Markdown vault, scope and supersession | External second-brain sync and search index adapters |
| F15 | Exact manifests, per-record retrieval and verified ownership transfer | Automatic provider context-budget detection; semantic continuity evaluation |
| F16 | Consistent snapshots, worktree evidence, checksum verification and new-home restore | Scheduled snapshots, measured RPO/RTO, encrypted offsite restore drill |
| F17 | Pause/resume/cancel obligations and separate lifecycle effects | Scope-wide process interruption/resumption journey |
| F18 | Bounded chief briefing and explicit wake budget | Notification delivery/read cursors and quiet-period controls |
| F19 | Local repository and Firstmate bridge | GitHub, Drive and Make connector execution adapters |
| F20 | Seven terminal tabs, forms, full output detail, keyboard navigation, stale and reduced-motion views | All 41 designed screens and accessibility validation on target terminals |
| F21 | Source installer, pinned Pi lockfile, doctor and OS service definitions | Signed binary releases, migration automation and one-command rollback |
| F22 | 20-project simulator, concurrent broker reads and admission races | 16/32/64/100 real-worker trials and prolonged soak |
| F23 | Scoped API authority, operator-only service/recovery controls and audit | Enforced OS isolation, account-specific spend and action-policy engine |
| F24 | Operator-recorded outcomes and explicit minimum-sample routing | Automatic cost ingestion, task-class calibration and controlled experiments |
| F25 | Five latest responses, ten completions, cursor paging/search and full output | Rich project completion reports |
| F26 | Fleet-wide local animation independent of chief, fresh/stale handling | Real model stream granularity |
| F27 | Firstmate lifecycle bridge, exact versions, uncertainty before effects | Vendor-native child launch/message/stop interoperability implementation and proof |
| F28 | Mailbox capacity, critical reserve, coalescing and deadline incidents | Cross-host delivery transport and bulk event compaction |
| F29 | No-progress detection, one replan, two replacements, bounded chief wakes | Long-duration loop detection evaluation and automated manager replacement |

No production milestone is declared complete merely because its primitives have been implemented.
M1's Linux installation is locally testable; macOS requires the matrix result.
M2 has local portfolio contracts; M3-M6 have substantial implementation with the live and disaster-recovery gates above outstanding.
M7 remains the operating-release validation stage.

## Failure semantics to retain during review

1. A stale observation becomes unknown, never permission to kill or duplicate a worker.
2. A launcher returning zero does not accept a task.
3. A persisted message is not delivered, read or acted.
4. An old generation or requirement version cannot satisfy current acceptance.
5. An uncertain external effect is reconciled before replay.
6. A backup failure is visible and the destination is marked failed.
7. Restoring a database revokes agent capabilities and disables live dispatch.
8. A context manifest preserves recorded evidence; it does not promise retention of unrecorded reasoning.
9. A model session ending does not shut down the coordinator.
10. Same-user shell access is not an OS security boundary.

## Next operating-release gates

Run one isolated, authenticated project end to end for each proposed Claude, Codex and Muse profile, including peer communication, interrupt, resume, stop, stale output and a bounded replacement.
Prove native child control separately before setting that capability.
Complete a new-machine restore with offsite storage, reconstruct workspaces, reconcile still-running old workers and measure recovery objectives.
Run staged worker-count trials before increasing capacity, followed by a 72-hour 20-project soak with deliberately injected failures.
Finish the remaining terminal flows and connectors against the blueprint, then run the repository's full publishing pipeline before merging a production release.

# Olympus engineering preview: verification and release gates

Evidence date: 2026-11-03.
This record owns implementation status and test evidence for the Olympus automation update.
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

The final local contract run contains 61 Rust tests, 23 terminal/policy tests and fifteen process acceptance tests, including real Pi extension registration.
The [machine-readable evidence](verification/olympus-local-evidence.json) records the toolchain and the separate read-load probe.
The installed 0.3.0 read-path probe used a 20-project simulated portfolio; 100 snapshot requests from eight concurrent clients had zero failures, a median of 162.36 ms and a 95th percentile of 192.05 ms in this container.
This is a small local read-path measurement, not a model-worker scaling result or production latency guarantee.

## WezTerm widget verification (2026-11-04)

The real macOS WezTerm GUI guard passed with `wezterm 05343b387085842b434d267f91b6b0ec157e4331` and the pinned Pi 1.4.0 runtime.
It created an isolated Olympus simulator and a new owned WezTerm window, checked the compact task widget and all ten full dashboard tabs, inspected colored terminal cells, pasted `Draft 界 Ω`, opened and closed full detail, and verified that a real pane split narrowed the widget without losing that draft.
It closed only its newly created test panes and left pre-existing panes intact.
The GUI test copied no provider credentials and submitted no model prompts or model requests.
Physical macOS Option/Alt composition remains user-configured; the guard sends Meta's terminal byte sequence and also exercises slash-command navigation.
This establishes GUI behavior for the named installed build, not future WezTerm releases or authenticated model-worker lifecycle compatibility.

After building Olympus and installing the pinned Pi dependencies, run with an active WezTerm GUI:

```bash
OLYMPUS_WEZTERM_LIVE=1 bash tests/olympus-wezterm-live.test.sh
```

The guard's header owns optional installed-binary overrides.
The measured result was `test_real_wezterm_colors_unicode_resize_and_draft ... ok`, `Ran 1 test`, `OK`.
Without explicit opt-in the GUI test skips, so ordinary headless tests never open a terminal window.

## Installed authentication-path verification (2026-11-04)

The fresh-login startup guard passed with the installed Pi 1.4.0 runtime after the Olympus CLI was updated to honor `OLYMPUS_PI_COMMAND`.
The login-shell PATH command, its resolved executable and the separate user wrapper each selected `meta/muse-latest`, reported 34 available models, and registered the Olympus UI commands.
The interactive fresh-login PTY check also displayed the compact widget and selected model without the missing-model or self-update warning.
The GUI-only simulator test above does not establish authentication; the account-path guard is separate and opt-in.
No credential contents are printed by this check and it sends no model prompts.
Reproduce on an installed workstation with existing provider authentication:

```bash
OLYMPUS_AUTH_LIVE=1 bash tests/olympus-auth-live.test.sh
```

This verifies selected-model credential availability according to Pi's registry, not a paid model-response benchmark.

## Decision-first workbook verification (2026-11-04)

The decision-first workbook passed 36 UI/runtime tests plus the pinned Pi typecheck and real Pi interactive guard.
The tests cover aligned table cells at narrow and wide widths, priority operator actions, condensed outcomes, suppressed routine payloads, native risk conflicts, explicit chief-assessment provenance, stale-animation freezing, reduced motion and preservation of task acceptance authority.
The real WezTerm GUI guard was rerun with the same installed build above and passed all dashboard tabs, colored segments, Unicode paste, pane-split resizing and draft restoration.
The structured brief tool was exercised through the real pinned Pi module loader with synthetic coordinator reads; this is UI-contract evidence, not a paid model-quality benchmark.
The default tool renderers collapse coordinator JSON and reference text without removing those records from model context or expanded detail.
`node tests/olympus-snapshots.mjs` also produces `assets/olympus-workbook.svg` and its segment record from the production workbook renderer and a running, explicitly synthetic demo.
The preview is not concept art or evidence of real worker throughput.

## Blueprint coverage

These IDs map to the previously delivered 29-feature, 41-screen blueprint.
Implemented means the described local behavior exists; it does not imply every production acceptance gate is met.

| ID | Current implementation | Remaining acceptance or expansion |
|---|---|---|
| F01 | Project IDs, aliases, charter, repo binding, completion state | Versioned charter amendment UI |
| F02 | Correlation across active/completed projects, unmatched-project creation, contextual follow-ups and exact-request deduplication | Broader semantic evaluation set and ambiguous-choice wizard |
| F03 | Immutable requirement versions, durable change notifications and stale-output rejection | Full impact visualization |
| F04 | Scoped captain/specialist teams, role instructions and hierarchy limits | Versioned editable role packages and automated team evaluation |
| F05 | Explicit dispatch shape rules and pre-reserved child capacity | Calibrated judgment across real tasks |
| F06 | Cycle-checked task dependencies and parent acceptance gates | Rich producer/consumer artifact-contract editor |
| F07 | Durable threaded messages, mailbox terminal view, bounded automatic notifications and separate acknowledgement stages | Authenticated multi-vendor drills |
| F08 | Atomic global/project admission, child headroom, load/priority/age queue | Weighted fairness and per-host/account limits |
| F09 | Exact-version profiles, role/complexity/effort capabilities and automatically recorded quality ranking | Real Claude/Codex/Muse profiles and account-specific routing |
| F10 | Canonical Firstmate observations, freshness and generation checks | Live backend coverage and measured observation latency |
| F11 | Deduplicated incidents, owner/reason/next-check queue | Timed operator notification connectors |
| F12 | Independent reviewer, current revision and version-bound acceptance | Automated execution of project-specific acceptance suites |
| F13 | Automatic deadline-driven stop requests, exact-evidence fenced recovery, persistent obligations | Automatic chief/manager session restart and multi-host fault drills |
| F14 | Source-backed Markdown vault, automatic accepted-outcome capture, scope and supersession | External second-brain sync and search index adapters |
| F15 | Exact manifests, per-record retrieval and verified ownership transfer | Automatic provider context-budget detection; semantic continuity evaluation |
| F16 | Scheduled verified snapshots, worktree evidence, failure records and new-home restore | Measured RPO/RTO and encrypted offsite restore drill |
| F17 | Pause/resume/cancel obligations and separate lifecycle effects | Scope-wide process interruption/resumption journey |
| F18 | Bounded chief briefing and explicit wake budget | Notification delivery/read cursors and quiet-period controls |
| F19 | Local repository and Firstmate bridge | GitHub, Drive and Make connector execution adapters |
| F20 | Ten terminal tabs, calendar forms, project outcomes, mailbox detail, filtering, scrollback and animation | All 41 designed screens and accessibility validation on target terminals |
| F21 | Source installer, pinned Pi lockfile, doctor, user-service installation and additive schema migration | Signed binary releases and one-command rollback |
| F22 | 20-project simulator, concurrent broker reads and admission races | 16/32/64/100 real-worker trials and prolonged soak |
| F23 | Scoped API authority, operator-only service/recovery controls and audit | Enforced OS isolation, account-specific spend and action-policy engine |
| F24 | Automatically recorded reviewed outcomes, version-bound failure lessons and minimum-sample routing | Automatic cost ingestion, task-class calibration and controlled experiments |
| F25 | Five latest responses, ten completions, project outcome reports, filtered cursor paging/search and full output | Export styling and historical project-cycle reports |
| F26 | Fleet-wide local animation independent of chief, fresh/stale handling | Real model stream granularity |
| F27 | Firstmate lifecycle bridge, automatic portable child dispatch, exact versions, uncertainty before effects | Vendor-native child launch/message/stop interoperability implementation and proof |
| F28 | Mailbox capacity, critical reserve, coalescing and deadline incidents | Cross-host delivery transport and bulk event compaction |
| F29 | No-progress deadlines, alternate-profile recovery after repeat review failure, two replacements and deterministic coordination outside chief sessions | Long-duration loop detection evaluation and automated manager replacement |

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

## Automation acceptance additions

The coordinator calendar, automatic dispatch/review policy, cross-project durable messaging and recovery decisions are covered through the public action interface and real SQLite transactions.
Calendar tests include London daylight-saving gaps and repeated hours, missed occurrences, overlap prevention and process restart.
Process acceptance includes the Today/Week/Recurring terminal views, request deduplication after routing, scheduled backup verification and the actual installed Pi extension.
`node tests/olympus-snapshots.mjs` regenerates exact SVG views and their text record from a running isolated 20-project simulator.
The PNG previews are rasterizations of those SVGs, not generated concept art.

The added recurring calendar is implemented, rather than an external ChatGPT automation.
The coordinator’s deterministic automation does not require an active chief model conversation.
Interactive chief/manager model-session replacement, authenticated native subagent lifecycle tests, offsite restore drills and the broader operating-release gates remain incomplete.
New service installation code emits and activates standard user services, but this container does not establish real launchd behavior on the target Mac.

## Installed workflow verification for 0.3.0

The public installer was run in a fresh prefix for the published 0.2.0 baseline; all twelve baseline process tests passed against that installation.
The public installer was also downloaded and run in a fresh prefix for published 0.3.0 commit `8d6952c45b72aa1264f9c8f41e4cd5b12997ab7e`; all fifteen installed process tests passed with no skips.
The updated 0.3.0 release was then built and installed in a separate prefix using the same installer with `--source` and `--skip-build` after a successful release build.
All fifteen process tests passed again with `OLYMPUS_TEST_BIN` pointing to the installed executable and `OLYMPUS_PI_EXTENSION` pointing to its installed extension.
The 99 unique tests comprise 61 Rust, 23 terminal/policy and 15 process cases; rerunning the same cases against an installation is not counted as extra unique coverage.
Child tasks and recurring jobs exercise the same complexity rules, and the detached-start acceptance case starts a populated 20-project simulator.
A real 0.2.0 database was opened by the installed 0.3.0 coordinator: existing project/task requirements survived, schema 3 was installed and the old binary subsequently refused to open it.

| Requested behavior | Evidence exercised | What this establishes |
|---|---|---|
| Independent and unique requests | Installed CLI creates observatory and orchard projects, with distinct task/project IDs | Real intake persistence; no model-generated implementation claim |
| Similar and older requests | Identical request deduplicates; a named follow-up reopens the completed observatory project after a restart | Retained context and original accepted output survive |
| Ambiguous requests | Atlas and Atlas Mobile refuse automatic selection; explicit project choice succeeds | Similarity does not silently overwrite either project |
| Changed requirements and plans | Exact-task amendment increments the version, stale updates/results refuse, current owner gets a durable notice, dependency remains recorded | Versioned coordination and notification; unrelated human blockers remain blocked after an acknowledgement; not proof of arbitrary semantic replanning |
| Communication across projects | Unlinked peer send refuses; a dependency enables two-way messages and recipient acknowledgements | Scoped cross-project transport with separate outcomes |
| Model and effort choice | Role/complexity gates, minimum five matching outcomes, configuration-change invalidation and captured Firstmate `--model`/`--effort` arguments | Routing and argument plumbing; synthetic model profiles only |
| Repeated mistakes and replacement | Two failed reviews exclude that configuration; automatic recovery uses the old profile to stop, waits for matching stop evidence, then prepares the alternative at higher configured effort | Deterministic recovery contracts with injected runtime observations |
| Learning and context | Failed review followed by acceptance becomes one accepted-after-rework sample; exact evidence survives restart and scope checks; amended requirements retain old lessons without inheriting exclusion | Persistent harness learning and complete handover manifests, not model fine-tuning or demonstrated future quality improvement |
| Installation and upgrade | Fresh public baseline, separate updated release installation, full installed process suite, schema upgrade and downgrade refusal | Linux installation and durable-state compatibility |

The process tests use the actual coordinator, SQLite database, transport, CLI, terminal and installed Pi extension.
Workers in the journey tests are scripted callers; the runtime-effect fixture is explicitly synthetic.
`olympus doctor` found no installed Claude, Codex or Muse executable in this environment.
Consequently no independent real-model project execution, vendor-native child communication, reasoning-quality comparison or long-run improvement benchmark is reported as passed.
Hosted commit `8d6952c45b72aa1264f9c8f41e4cd5b12997ab7e` passed the Linux job, but its macOS installed-release readiness check failed after compilation, domain/process tests and real Pi loading passed.
The workflow now retains startup diagnostics and runs the complete process suite against installed release binaries and Pi assets on both platforms.
Hosted results for a newer commit must be inspected separately; the workflow definition alone does not establish a passing Mac installation.

Reproduction after a release build and installation:

```bash
bash tests/olympus.test.sh
OLYMPUS_TEST_BIN=/absolute/prefix/bin/olympus \
OLYMPUS_PI_CLI=/absolute/prefix/lib/olympus/pi-runtime/node_modules/@earendil-works/pi-coding-agent/dist/cli.js \
OLYMPUS_PI_EXTENSION=/absolute/prefix/lib/olympus/index.ts \
python3 tests/olympus-e2e.py
```

The first command needs the pinned Pi executable in `OLYMPUS_PI_CLI` to include the token-free extension case; without it that case explicitly skips.
The installed command above supplies the actual installed Pi paths and was run with no skips.

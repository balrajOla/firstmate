# Verification and release gates

This is an experimental implementation. No claim is made that every feature is complete, every edge case was tested, or hallucinations are impossible. A fixture pass is not a live-service pass.

The dated machine-readable report is in [evidence/verification.json](evidence/verification.json). Test source is committed beside the implementation. Actual headless terminal renders are under `docs/assets`; they are not Mac WezTerm screenshots.

## Checks performed here

- Concurrent/retried prompts, explicit and ambiguous routing, original preservation, amendment conflicts, grouping, cancellation and surviving grouped obligations.
- Scope checks, source hashes, original reopening, supersession, coverage, bounded passage selection, conflicting assertions and active-store revocation/purge.
- Plan dependency/child validation, profile selection, pinned effort-field mapping, fixture coordinator delivery/replay, uncertain creation, cancellation during delivery and pool admission.
- Checkpoint artifact integrity, stale generations, uncertain effects, no-progress stops, stop-acknowledgement requirements and fixture fresh-session wakeups.
- Immutable outcomes, quote presence, independent reviewer identity, required deliverables, stale review rejection and safe exports.
- HTTP token/origin checks, MCP stdio, subprocess timeout, argument safety, real CLI restarts, migration backups/Git history, tamper detection, recoverable retirement and restore conflict refusal.
- Headless Textual interactions at 80×24, 120×36 and 160×48, including log floods that do not replace the selected outcome.
- Real installed Basic Memory register/write/fresh-process retrieval, real QMD collection/exact retrieval, real Docling extraction and real LCM daemon capture/scoped recall with summarization disabled.
- Actual Vale clean/verbose fixtures and Promptfoo deterministic positive/seeded-false-claim controls. Promptfoo's expected failing negative fixture is evidence that its assertion caught the seeded error; it is not a failed live model benchmark.
- An independent installed package/prefix, including a path containing spaces, and the installed prompt/outcome/export/backup/restore scenario.

The initial local integration probe was blocked by automatic approval review because ONNX attempted external telemetry. Microsoft's documented process-start opt-out was added before retrying. The subsequent local-package probes passed with telemetry disabled. The initial block is retained in the evidence report.

## Measured limitations

The small lexical routing demonstration (`evals/routing.json`) now scores 10/10. “Report the architecture review” was classified as design in the earlier report; the lexical policy has since given the leading requested action precedence over subject nouns. The demonstration is not a held-out benchmark. On the 25 held-out cases in `evals/routing-heldout.json`, the optional semantic router (Claude Sonnet 5.5 at low effort, through the official CLI) chose the expected workflow and project in 25 of 25, with no errors and no confident wrong project, where the lexical policy managed 14/25 and 5/25. Mean latency was 15.8 seconds (2026-11-08, isolated Mac run). The same author wrote the labels and the router prompt, the suite is small, and it is not human-calibrated, so this shows the fix on these cases only. A second, adversarial held-out suite (`evals/routing-adversarial.json`, 20 cases written after the router prompt was fixed) adds shared terms with different meanings, misleading workflow words such as "research" or "investigate" in code requests, two pairs of similarly named projects, regressions of past work and unrelated new topics. On 2026-11-09 the semantic router chose the expected project in 20 of 20 cases and the expected workflow in 19 of 20, with no confident wrong project. The one miss, a one-word typo fix labelled simple, was answered as code at confidence 0.6, below the 0.7 threshold, so it would wait for the operator. The lexical policy scored 13/20 and 8/20. Mean latency was 16.0 seconds. The same caveats apply.

In the original build container a full Paperclip startup was blocked by its embedded PostgreSQL root restriction. The live macOS run below used the pinned Paperclip as a user service with signed-in Claude Code workers.

## Live macOS run (2026-11-09)

One Apple-silicon Mac, macOS 27, with the release installed into an independent prefix, the pinned Paperclip and the bridge as launchd user services, signed-in Claude Code workers (Sonnet), and local models for the optional services. Every check below ran through public CLI, HTTP or TUI interfaces. None of it is human review.

| Area | Observed | Result |
|---|---|---|
| Test suite | 584 unique tests, including the real Temporal server, TypeScript, Rust, installed CLI, Basic Memory, LCM and QMD (with local GGUF models) probes | all passed, none skipped |
| Workflows | simple, code, design, report and research requests each ran end to end with real workers and produced one unverified outcome | passed |
| Website | a repair-cafe site plus separate search and filter changes, built by workers, checked in a real browser (keyboard paths, empty states, URL state, malformed input, reset) | passed; the two changes need a manual merge |
| Research | a sourced report checked claim by claim: 20 quotes, 41 numbers and every cited section matched the converted papers | passed |
| Independent review | a worker asked only to review code against its spec found a deliberately seeded defect; its regression test fails on the original; a separate QA agent verified the fix | passed |
| Mid-run change | amend during a running worker, stale amendment refused, grouped demand withdrawn, request cancelled; earlier-revision run cancelled 2.2 s after the amendment | passed (after the fix in #34) |
| Stop and recovery | bridge killed with SIGKILL during delivery and restarted: no duplicate issues or runs; cancelling a running request stopped its run in 1.1 s and left no process | passed |
| Install lifecycle | fresh, repeat, upgrade, rollback, backup, uninstall, restore and reinstall at a new prefix (paths with spaces); generated launchd service ran and unloaded | passed |
| Schedules | 60 missed ticks coalesced to one request; restart, next interval, pause, and two concurrent bridges giving one request per tick | passed |
| Routing | 45 held-out cases over two author-labeled suites | project 45/45, workflow 44/45, no confident wrong project |
| Local services | Hindsight, OpenViking, Open Notebook (Ask with citations) and GPT Researcher with local models; Vale and Promptfoo procedures | passed; one GPT Researcher citation pointed at the wrong page |
| Terminal | native WezTerm at about 80x24, 120x36 and 160x48 with real screenshots | passed; real keystroke, reconnect, sleep and reboot not observed |
| Soak | 10 hours, 11 cycles, 220 intake prompts, 11 live requests | no lost prompts, no unhealthy cycles; stopped before 72 hours by the operator |

Defects found during this run were fixed with regression tests in #17–#35. Not observed: Codex workers through Paperclip (its CLI engine needs a file-based Codex sign-in that the test machine did not use), a mobile build, two physical hosts, human-calibrated judging, a 72-hour soak and login-time service start.

## Follow-up live checks (9-12 November 2026)

The same Mac and services, covering the one-command install, `awb open`, projects created by the router, and recovery from outages and restarts. Every check ran through public CLI, HTTP or TUI interfaces. None of it is human review.

| Area | Observed | Result |
|---|---|---|
| Installer | `install.sh` from scratch into a temporary prefix whose path contains spaces, with its own ports (3199, 8799) and service labels. It installed the pinned toolchain where missing, built and onboarded the pinned Paperclip, created the company, probed the Claude profiles and installed launchd services. A real code request reached review. The test installation was then removed. | passed; with the default grants the reviewer could not run the tests |
| `awb open` | With one of the configured services unloaded, it loaded it again and waited for the bridge and the coordinator. `--session herdr` started a persistent herdr session running the terminal UI; this was tested in disposable named sessions, which were removed afterwards. | passed |
| Routing | Three live prompts: one for an existing project, one for new work (the router created a project with its own Git repository) and a one-off question (assigned to General). | 3 of 3 as expected; a small sample |
| Worker grants | Account sign-in with an unscoped command grant in the local profile: the worker ran the project's tests. | passed |
| Connectivity | Drill: the probe host was made unreachable, then restored. The bridge reported offline after about 16 s and online about 18 s after the host was restored. Four real offline periods between 9 and 12 November, from about 5 minutes to about 2 days while the Mac slept or was off the network, were each detected and cleared automatically. No worker run failed during an outage, so that retry is covered by tests only. | passed |
| Restart after a worker closed its task | Paperclip restarted after the worker marked its task done but before its run ended. The run ended interrupted, without a report. | after the fix, the report was recovered from the worker's task comment and the request reached review |
| Restart early in a run | Paperclip restarted 3 s into a worker's run. Its recovery sweep ended the run as orphaned and left the task in progress with nothing queued, for 12 minutes. | after the fix, the bridge retried the task within seconds of starting. The retry found the work already committed, the reviewer ran the three tests, and the request reached review |
| Worker exit | On this machine the Claude CLI stays open about 12 s after its final message. Paperclip stops it after 5 s and recorded all four finished runs on 12 November as failed. | reports were still recovered; with `terminalResultCleanupGraceMs` set to 30000 in the local profiles, both runs of the next request succeeded. Claude workers now get 30 s by default |

Defects found during these checks were fixed with regression tests in #41-#46. They were: Paperclip's sign-in check mistaking a skill description for a failed login (#41); pausing workers during an outage, which stranded queued tasks; a task closed just before a restart, which waited forever for its report; a task left in progress by an orphaned run; and finished Claude runs failed by Paperclip's 5-second exit limit (#46). Not observed: Codex workers through Paperclip, a Pro or Max subscription sign-in (the test machine authenticates Claude through a managed `apiKeyHelper`), a worker run failing during a real outage, a real reboot and login-time service start.

## Required target-machine journeys

| Gate | Required observation |
|---|---|
| installed_cli | Fresh install, repeat install, customized paths, installed artifact tests, safe uninstall and upgrade rollback |
| coordinator | Actual pinned Paperclip with durable issues, permissions, idempotency and process reconciliation |
| real_workers | Two real signed-in workers overlap, exchange evidence, review an artifact and stop on cancellation |
| context_recovery | Five genuine fresh sessions, intervening amendment/cancellation, source recall and cumulative limits |
| terminal_gui | Actual WezTerm install, all views, keyboard/resize/fonts, log flood, reconnect, sleep/reboot and screenshots |
| migration_restore | Existing custom Olympus data backed up, changes reconciled, separate-location restore and independent new runtime |
| research | Real Open Notebook Ask and GPT Researcher generation in every advertised profile; verify sources and failure handling |
| quality | Human-calibrated judges, source-grounded reports, numeric/negation/uncertainty editing traps and inspected visuals |
| soak | 72-hour sustained mixed workload, capacity/backpressure, failures, restarts, storage limits and retained outcomes |

For the soak gate, `scripts/soak_live.py` drives an installed Workbench whose bridge and coordinator already run as services. Each cycle submits one small real request to a live project, a burst of synthetic intake to a paused project (checked for lost prompts, then cancelled), and samples service health, queue depth, storage and request latency. It writes one JSON line per cycle plus a state file, so a restarted runner continues the same run. Time it was not running is reported as gaps, not soaked time, and its summary says RUNNING until the real elapsed target is reached. `scripts/soak.py` remains a short durable-intake check without workers.

`awb qualify` returns exit code 3 while required evidence is missing. It does not create passed evidence. Operator attestations must retain artifacts, identify the observer and match this machine; hashes and age are checked. The target GitHub workflow validates that evidence on an explicitly configured private Mac runner. It does not simulate the GUI or mark a skipped check green.

The CI matrix runs core tests and an independently installed wheel on Linux/macOS. A successful CI macOS shell job is still not a logged-in WezTerm desktop test. Publication of source and tests does not satisfy the open live-release gates.

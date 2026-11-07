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

The small routing demonstration scores 9/10: “Report the architecture review” is classified as design by the current keyword policy. This is a known selection weakness, not a hidden pass. Explicit workflow selection and plan review remain necessary for ambiguous requests. This is not a held-out quality benchmark.

A local full Paperclip startup was blocked by its embedded PostgreSQL root restriction; this container also refused a non-root ownership setup. The bridge has been tested against contract fixtures and inspected pinned source, not a running full coordinator here. Genuine signed-in CLI agents, effective model/effort confirmation and native cancellation remain required.

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

`awb qualify` returns exit code 3 while required evidence is missing. It does not create passed evidence. Operator attestations must retain artifacts, identify the observer and match this machine; hashes and age are checked. The target GitHub workflow validates that evidence on an explicitly configured private Mac runner. It does not simulate the GUI or mark a skipped check green.

The CI matrix runs core tests and an independently installed wheel on Linux/macOS. A successful CI macOS shell job is still not a logged-in WezTerm desktop test. Publication of source and tests does not satisfy the open live-release gates.

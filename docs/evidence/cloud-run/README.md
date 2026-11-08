# Cloud verification — 8 November 2026

The combined run passed **467 tests, with no failures or skips**. The 30-instruction workload passed its bookkeeping/control assertions and saved 22 prompts. Native capture produced 37 hash-verified WezTerm screenshots. Product-owned live model execution remains blocked; no website, mobile binary or research report was accepted by the workload.

- [Exact summary and source fingerprint](verification.json)
- [Every test case](TEST_CASES.md), [JUnit](all-tests.xml) and [test log](all-tests.log)
- [Feature accounting](feature-coverage.json): all 71 automated mappings pass; full-feature qualification remains blocked
- [All native screenshots](GALLERY.md), [capture observations](native-manifest.json) and [30-step results](scenario-results.json)
- [Detailed snapshots and coordinator probes](scenario-traces.zip)
- [Codex canary diagnosis](codex-canary.json)

The release test enabled the actual Temporal dev server, Basic Memory 1.0.0, LCM 0.16.0, QMD 2.8.3 with local GGUF models, TypeScript 7.0.2 and Rust 1.99.1. The core tests also exercised real Python and Node processes and a separately installed wheel in a path containing spaces. Memory documents and compiler projects were synthetic test inputs. Temporal activity/subprocess doubles were labelled fixtures; they were not model agents.

Native pixels are unchanged from capture. JSON test paths were normalized for publication. Authentication files, databases, model weights, account information and raw provider logs are excluded. Screenshot/control evidence does not establish a user's desktop, live model quality, physical host deployment, prolonged recovery or a 72-hour soak.

The combined suite was rerun after macOS CI exposed an unsupported address-space limit. Explicit `memory_mb` now rejects unsupported platforms before execution, with regression cases for macOS and Windows. The native screenshots retain their earlier source revision in `verification.json`; terminal code did not change.

# Language checks and artifact evidence

Workbench can inspect and execute **real local test/build commands** in a private copy of a registered project. It can also record the files that a task produced and detect later changes. These operations do not call an AI model or approve an outcome's meaning.

The recorded cloud checks run Python, Node, TypeScript and Rust processes on synthetic projects. The tests deliberately include failed assertions, a TypeScript type error, empty suites, timeouts, excessive output, memory exhaustion, cancellation, changed files, path escapes, a fake mobile archive and a research statement that changes “excludes” to “includes.” They are not evidence that an AI autonomously created a website, shipped a mobile app or researched a topic.

## Workflow

1. Register a project with its repository directory.
2. Call `execution.create_workspace` to copy its current source into a unique private directory. The response names the copied files and the source digest.
3. Inspect the workspace. Resolve a language and an operation into a command plan.
4. Review the command and the plan digest. Execute that exact plan. A changed source tree requires a new plan.
5. Inspect the retained run result, then record the actual output files as artifacts.
6. Review the result independently. An artifact receipt is evidence to inspect, not acceptance.

The copy excludes `.git`, environment/key files, virtual environments, dependency folders, caches and build output directories. It preserves uncommitted source edits. A symlink, more than 10,000 source files or more than 100 MiB of source stops creation with an explicit error. The source project is not changed by tests in the copy.

Dependencies are **not automatically installed**. A package that needs dependencies must have them installed in its private workspace through a reviewed setup step. The execution runner does not turn a missing dependency, compiler or test suite into a pass.

## Operator actions

These methods are implemented by `ExecutionPacks(store)` and exposed through the `execution.*` operator actions when the application dispatch is wired to them.

| Action | Arguments | Result |
|---|---|---|
| `execution.create_workspace` | `project` | Private workspace ID/path and source digest |
| `execution.list_workspaces` | optional `project` | Registered copies, newest first |
| `execution.inspect` | `workspace_id` | Languages, file hashes, lockfiles, scripts and available runtimes |
| `execution.plan` | `workspace_id`, optional `language`, `goal`, `allow_package_scripts` | Explicit command, source digest, plan digest, readiness/blocker |
| `execution.run` | `plan_id`, `expected_sha256`, optional limits | Retained execution result; no semantic acceptance |
| `execution.list_runs` | optional `workspace_id`, `limit` | Bounded progress/status list without large output bodies |
| `execution.get_run` | `run_id` | Exact retained output and limits for one run |
| `execution.cancel` | `run_id` | Stop requested, or the already terminal state |
| `execution.artifacts` | `workspace_id`, explicit `paths`, `kind`; optional `platforms`, `claims` | Hashed files and mechanical checks, always needing review |
| `execution.verify_artifacts` | `manifest_id` | Rechecks files, referenced assets and source availability |

Example operator envelope:

```json
{"action":"execution.plan","args":{"workspace_id":"WORKSPACE_ID","language":"javascript","goal":"test"}}
```

Run only the digest returned by that plan:

```json
{"action":"execution.run","args":{"plan_id":"PLAN_ID","expected_sha256":"PLAN_SHA256","timeout":60,"max_output_bytes":1048576}}
```

## Language support

| Pack | Test | Build or typecheck | Boundaries |
|---|---|---|---|
| Python | Finds `test_*.py` / `*_test.py`; runs the installed Python's `pytest` | `python -m build --no-isolation` when a package manifest and build tool exist | No automatic environment/dependency installation. Ambient pytest plugin autoload is disabled; projects can explicitly declare required plugins. |
| JavaScript | Native `node:test` files, or an explicitly reviewed `test` package script | Explicit `build` or `typecheck` package script | Lockfiles and `packageManager` must agree. No `npx` download or guessed build framework. |
| TypeScript | Explicit reviewed `test` script | Explicit reviewed `typecheck` / `build` script | An installed compiler and project dependencies are required; the pack does not treat source presence as compilation. |
| Rust | `cargo test --locked --offline` | `cargo build/check --locked --offline` | Cargo and a reviewed `Cargo.lock` are required. A missing compiler or uncached dependency is a blocker/failure. |

Package scripts are executable project code. Inspect the script returned by `execution.inspect` before setting `allow_package_scripts: true`. Direct argv execution avoids shell interpolation by Workbench; the package manager itself may run the reviewed script through its normal shell.

A zero exit code is insufficient for a **test** pass: an inspected test runner must report executed tests. Declared package scripts must directly invoke Node's test runner, Jest or Vitest and have discoverable test files before their totals are interpreted; Jest/Vitest must also be declared dependencies. Unknown runners, shell pipelines and scripts that merely print test-like totals return `not_run`, not `passed`. These are mechanical receipts, not proof that project code or its test runner is trustworthy. Pytest's no-tests exit remains a failed command. Build/typecheck success means only that the selected command completed successfully.

## Limits and cancellation

| Limit | Enforcement |
|---|---|
| Wall time | 0.1–600 seconds, observed by the parent process |
| Captured output | 256 bytes–8 MiB combined stdout/stderr; excess stops the process group |
| CPU time | Per-process CPU limit based on the selected wall budget |
| File size/open files | Per-process 512-MiB file-size limit and 256 open descriptors |
| Memory | Optional 64–16,384 MiB virtual-address limit; Node also receives a 512-MiB old-space limit |
| Cancellation | Durable stop request; terminates the started process group and records its terminal result |

The process receives a small environment rather than the operator's provider keys, access tokens or arbitrary Python path. Package managers receive offline/ignore-lifecycle-script settings. Commands may still contain project code that accesses the network or filesystem directly: **these settings are not an OS security sandbox or a complete egress policy**. CPU limits are per process, not a global fleet CPU quota. A process that deliberately escapes its process group needs a real host/container boundary. Virtual-address limits can be incompatible with runtimes that reserve large address spaces, so they must be chosen for the actual runtime.

## Artifact checks

All explicit file paths must remain inside the registered workspace. Absolute paths, upward escapes, symlinks, secret filenames and oversized bundles are rejected. Receipts store content hashes and remain `needs_review` even when their mechanical checks pass.

| Kind | Checked | Still requires real observation |
|---|---|---|
| `files` | Files exist and retain their exact bytes | Whether they meet the request |
| `web` | HTML entry, title/body structure, local asset existence; referenced assets are hashed too | Browser behavior, accessibility, layout, dynamic routes, remote resources and design quality |
| `mobile` | Explicit Android/iOS targets; corresponding APK/AAB/IPA presence and basic ZIP structure | Valid signatures, installation, emulator/device behavior and platform-specific builds |
| `research` | Readable report, explicit claim/quote/source records, project scope, archived source integrity and quoted text presence | Source reliability, entailment, completeness, contradictions, current facts and reader usefulness |

Research claims use the same `{source_id, quote, statement}` shape as Workbench outcomes. The quote must occur in the archived source and the statement in the report. This catches a missing or changed citation, but does not prove that the source supports the statement. Source revocation or later artifact changes invalidate the integrity check.

No end-user application or report is marked delivered based on test fixtures. The module records actual files and preserves the distinction between command execution, mechanical checks, independent review and a working end product.

## Backup and recovery

State backups include registered private workspaces and their generated artifacts, including build output and empty directories. Workspace locations are stored relative to the state directory so a verified restore can relocate them. Original project repositories remain external references; this is not a backup of every repository, toolchain or model session on the machine.

The snapshot records every copied file's SHA-256 and every directory. Restore rejects missing files, changed content, extra unlisted entries, path traversal, links and special files. It copies only the verified inventory, validates the restored SQLite database, and removes incomplete copies on failure. Legacy absolute workspace locations are converted inside the backup database; active state is left unchanged.

Stop or reconcile active execution runs before backup. Concurrent database or filesystem changes cause the snapshot to fail and be removed rather than being presented as a consistent restore point. The copy is not a filesystem transaction or a signed authenticity proof: keep source writers quiescent and protect backup access. Dependency trees containing symlinks must be rebuilt or separately archived; the state backup does not follow those links. Restoring does not restart processes or replay commands. Discover the available runtimes and create fresh plans on the destination machine.

## Recorded runtime checks

On 8 November 2026, `tests/test_execution_packs.py` ran **23 cases with 23 passes and no skips** using Python 3.12.14, Node 24.22.1, TypeScript 7.0.2 and Rust/Cargo 1.99.1. The TypeScript and Rust cases use real compilers and contain both passing and deliberately failing source. Their expected failures are asserted; they are not hidden successful builds.

The combined execution, workspace-recovery and existing operations run passed **59 cases with no skips**. Its 24 workspace-recovery cases include a real post-restore Python execution, build-artifact recovery, active-process rejection, path and symlink escapes, unlisted entries, concurrent writes, corrupted databases and partial-copy cleanup.

TypeScript was installed into a private npm prefix. Rust was installed with private `RUSTUP_HOME` / `CARGO_HOME` paths and `--no-modify-path`; the user's home and shell configuration were not changed. The test command put the actual Rust toolchain binary directory and the private TypeScript `.bin` directory on that invocation's `PATH`. Those compiler tests explicitly skip when the required runtime is absent in a different environment; a skip must not qualify that language.

Reproduce with those tools installed and on `PATH`:

```sh
.venv/bin/pytest -q tests/test_execution_packs.py tests/test_workspace_backup.py tests/test_operations.py --junitxml=execution-artifacts-backup.xml
```

Primary setup references: [TypeScript installation](https://www.typescriptlang.org/download/), [TypeScript compiler options](https://www.typescriptlang.org/docs/handbook/compiler-options), [Rust installation](https://rust-lang.org/tools/install/) and [rustup's installation options](https://rust-lang.github.io/rustup/installation/other.html).

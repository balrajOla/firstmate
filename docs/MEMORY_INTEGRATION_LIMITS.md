# Memory integrations: calls and evidence

These adapters are available through `integration.basic_memory`,
`integration.history` and `integration.qmd`. They keep external memory scoped
to an explicitly selected project. Retrieval and summaries remain evidence to
review, not accepted conclusions.

| Feature | Callable behavior | Qualification boundary |
|---|---|---|
| Basic Memory concurrent editing | Local register, Markdown read/create, checksum-guarded replacement, text search | Real package tests check competing edits and separate projects; semantic indexing is separate |
| LCM capture | Structured messages are archived locally and sent to `/ingest` with explicit client/session identity | Real daemon test uses synthetic messages, not a live harness conversation |
| LCM summaries | `/compact` uses the daemon's explicitly declared provider; returns node identifiers and reported usage | Disabled/skipped/no-work returns `not_generated`; provider-backed summary quality needs a real model evaluation |
| LCM expansion | `/expand` uses a bounded depth and the current checkout's hashed project ID | Expanded summaries remain derived; truncation is preserved |
| LCM Codex hooks | Official connector prepares and merges project-local hooks after a checksum check | Installed files do not prove Codex trust, activation or turn capture |
| QMD semantic search | `exact` calls `search`, `semantic` calls `vsearch`, `hybrid` calls `query` | Actual CPU embedding/vector retrieval is tested separately from hybrid reranking |

## Basic Memory

Configure the pinned Basic Memory executable and authorize writes explicitly:

```json
{"enabled":true,"command":["/absolute/path/to/bm"],"auth":"local","allow_write":true}
```

Put that object under `integrations.basic-memory`. `register` creates a project
named after its Workbench ID beneath the private `knowledge` directory. The
adapter forces its own local config/home directories and disables unconditional
overwrite even if an optional environment setting requests it. No model API key
is needed for this adapter's text operations. Upstream may attempt optional
embedding maintenance; that is not text-search qualification.

Calls use a `project`, `action`, and the action's arguments:

- `register`: no extra arguments.
- `search`: `query`.
- `read`: an explicit relative Markdown `identifier`, such as `decisions/cache.md`.
- `write`: `identifier`, `content`, and `checksum` when replacing a note.

Read the full checksum from `data.checksum`, then supply it on the next write.
Stale checksums and existing-note writes without a checksum fail. The adapter
serializes its own project writes, passes the expected revision to Basic Memory,
and verifies the returned checksum against the local file. Concurrent direct
filesystem edits still require coordination; no software can guarantee a
conflict-free edit against arbitrary external writers. Content goes over stdin,
not a command-line argument. Notes and results are restricted to the selected
local project. Cloud and project-qualified `memory://` paths are not accepted by
this wrapper.

## LCM

Configure `integrations.lcm` with an origin, authentication profile, explicit
project mapping and the provider already configured in the daemon:

```json
{
  "enabled":true,
  "url":"http://127.0.0.1:3737",
  "auth":"subscription",
  "token_env":"AWB_LCM_TOKEN",
  "allow_write":true,
  "summary_provider":"codex-process",
  "command":["/absolute/path/to/node","/absolute/path/to/lcm/dist/bin/lcm.js"],
  "projects":{"p_replace_me":{"cwd":"/absolute/project/directory"}}
}
```

The mapping must match the project's repository. Shared or nested mappings are
rejected. Read calls are also scoped. Search filters out any grouped-checkout
results whose project provenance differs. Caller arguments cannot override
`cwd` or `projectId`.

`capture` needs `session_id`, `client` (`claude`, `codex` or `omp`) and messages
containing `role`, text `content`, and nonnegative `tokenCount`. Optional
`timestamp` is preserved. The limits are 1,000 messages and 1 MiB per request.
Capture returns an archived `source_id` and hash. It does not infer that a hook
ran. `search` needs `query` and optional `limit`; `expand` needs `nodeId` and
optional `depth` from 1–8.

`summarize` needs `session_id` and `client`. It compacts already-ingested content
with `skip_ingest:true`; it never discovers private harness transcripts on its
own. The configured `summary_provider` must be explicit rather than `auto`.
Process/session providers require the `subscription` profile; API-compatible
providers require `api` or `local`. The adapter does not configure the daemon's
provider, authenticate a harness or extract credentials. A returned provider
mismatch is an error. A service token is distinct from a model API key.

For `install_hooks`, set `allow_hook_install:true` and pass `harness:"codex"`.
If `.codex/hooks.json` exists, pass its current full SHA-256 as
`expected_sha256`. The official pinned installer runs in a staging directory;
Workbench checks the existing file again before applying the merge and retains
its previous bytes. Unrelated hooks are preserved. Restart Codex, review/trust
the hooks through `/hooks`, and launch it with the same `LCM_HOME` as the daemon.
Activation is reported as `unknown` until a real harness test establishes it.
Global installs and Claude plugin configuration remain explicit upstream setup;
this action does not edit them.

## QMD

Configure `integrations.qmd` with a command and distinct project collections:

```json
{"enabled":true,"command":["/absolute/path/to/qmd"],"auth":"local","collections":{"p_replace_me":"project"},"allow_model_inference":true}
```

Create and update the collection with upstream QMD, then run `qmd embed` before
vector retrieval. Use private `QMD_CONFIG_DIR`, `INDEX_PATH` and `XDG_CACHE_HOME`
values in `env` when separating installations. QMD uses local model files;
initial model downloads need network access and disk space. `vsearch` in the
pinned release also performs query expansion, so it can load a generation model
as well as an embedding model. Hybrid search additionally reranks results.

The action accepts `project`, `query`, `mode` and optional `limit` (1–100).
`semantic` and `hybrid` require `allow_model_inference:true`; failure never
silently retries as lexical search. Results must have `qmd://` URIs in the
configured collection. Malformed output, another collection's URI and traversal
paths fail closed. Ranking scores are not factual confidence probabilities.

## Reproducible checks

Run `python -m pytest -q tests/test_memory_integration_contracts.py` for the
deterministic contract tests. Opt-in real tests require:

- `AWB_TEST_BASIC_MEMORY`: Basic Memory 1.0.0 executable.
- `AWB_TEST_LCM_PACKAGE`: installed `@lossless-claude/lcm` 0.16.0 package directory.
- `AWB_TEST_QMD_BINARY`: QMD 2.8.3 launcher.
- `AWB_TEST_QMD_EMBED_MODEL` and `AWB_TEST_QMD_GENERATE_MODEL`: local GGUF files.

The real tests use isolated state and synthetic content. They exercise actual
Basic Memory revision conflicts, a real LCM daemon with summarization disabled,
the real Codex connector installer, and QMD CPU embeddings/vector retrieval.
They do not certify live Claude/Codex capture, an LCM model-generated summary,
hybrid reranking, arbitrary languages or production-scale retrieval quality.

Pinned primary sources reviewed on 2026-11-08:

- [Basic Memory CLI source](https://github.com/basicmachines-co/basic-memory/blob/c9bd187b83baba549d5cd16bacc180f8023d51c8/src/basic_memory/cli/commands/tool.py)
- [Basic Memory revision guards](https://docs.basicmemory.com/reference/mcp-tools-reference)
- [LCM Codex connector guide](https://github.com/lossless-claude/lcm/blob/0c17dbffda452cf7fae46929af427dff355aeaa3/docs/vscode-codex.md)
- [LCM capture route](https://github.com/lossless-claude/lcm/blob/0c17dbffda452cf7fae46929af427dff355aeaa3/src/daemon/routes/ingest.ts)
- [LCM compaction route](https://github.com/lossless-claude/lcm/blob/0c17dbffda452cf7fae46929af427dff355aeaa3/src/daemon/routes/compact.ts)
- [QMD commands and model setup](https://github.com/tobi/qmd/blob/facd35e01359e59d938bc9418e93fb9318addee3/README.md)

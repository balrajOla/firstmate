# Local service stack

The bridge runs without any of these services. This guide sets up the optional
ones on a single machine so that research, external memory and the default
coordinator run locally, with no provider API key. Use local models or a
provider you are entitled to use. A signed-in Workbench harness does not
authenticate any of these services, and nothing here turns a subscription
login into an API key.

Everything binds to `127.0.0.1`. Pick your own directories. The examples use
`/home/you/awb/...`; on macOS use `/Users/you/...`.

## Versions verified together

| Component | Version | Notes |
|---|---|---|
| Paperclip | pinned in `assets/dependencies.json` | `awb deps install paperclip` |
| llama.cpp `llama-server` | release b12211 | official release archive; check its published SHA-256 |
| Chat model | any GGUF chat model with tool calling | verified with Qwen3-8B Q4_K_M (Apache-2.0) |
| Embedding model | any GGUF embedding model | verified with embeddinggemma-300M Q8_0 (768 dimensions) |
| Hindsight | `hindsight-api` 1.2.0 | embedded PostgreSQL (pg0) |
| OpenViking | `openviking` 0.5.3 | local file and vector storage |
| SurrealDB | 2.7.0 | the v2 line that Open Notebook's compose file uses |
| Open Notebook | pinned in `assets/dependencies.json` | install from its `uv.lock` |
| GPT Researcher | pinned in `assets/dependencies.json` | plus `ddgs` for the keyless DuckDuckGo retriever |

## 1. Install

Create one virtual environment per Python service. Behind a TLS-inspecting
proxy, point `SSL_CERT_FILE` at your CA bundle for pip and model downloads.

```sh
python3 -m venv ~/awb/venvs/hindsight && ~/awb/venvs/hindsight/bin/pip install "hindsight-api==1.2.0"
python3 -m venv ~/awb/venvs/openviking && ~/awb/venvs/openviking/bin/pip install "openviking==0.5.3"
awb --home ~/awb/deps deps install paperclip open-notebook gpt-researcher
```

Open Notebook must be installed from its lockfile. An unlocked install pulled
SurrealDB SDK 3.0.0 where the lock pins 1.0.8, and startup then failed with
`Database record _sbl_migrations:1 already exists`. Export exact pins with uv,
which does not modify the source tree:

```sh
cd ~/awb/deps/dependencies/open-notebook/REVISION/source
uv export --frozen --no-dev --no-hashes --no-emit-project -o ~/awb/open-notebook-requirements.txt
python3 -m venv ~/awb/venvs/open-notebook
~/awb/venvs/open-notebook/bin/pip install -r ~/awb/open-notebook-requirements.txt
~/awb/venvs/open-notebook/bin/pip install --no-deps .
```

GPT Researcher's DuckDuckGo retriever imports `ddgs`, which its
`requirements.txt` lists but its package metadata does not install:

```sh
python3 -m venv ~/awb/venvs/gpt-researcher
~/awb/venvs/gpt-researcher/bin/pip install ~/awb/deps/dependencies/gpt-researcher/REVISION/source "ddgs>=9.0.0"
```

Put SurrealDB's release binary and `llama-server` wherever you like. Create
the two secrets once, readable only by you:

```sh
umask 077
python3 -c "import secrets; print(secrets.token_urlsafe(24))" > ~/awb/state/open-notebook/surreal.password
python3 -c "import secrets; print(secrets.token_urlsafe(32))" > ~/awb/state/open-notebook/encryption.key
```

## 2. Generate and start the services

Copy `examples/local-services.json`, replace every path, and remove the
services you do not want. Then:

```sh
python3 scripts/local_services.py --spec my-services.json --kind launchd --output ~/awb/services
launchctl bootstrap gui/$(id -u) ~/awb/services/com.assistant-workbench.llm.plist
```

To replace a running agent, wait until `launchctl print gui/$(id -u)/<label>` fails after
`launchctl bootout` before bootstrapping the new file; bootout finishes asynchronously and an early
bootstrap fails with `Input/output error`.

Use `--kind systemd` on Linux, copy the units to `~/.config/systemd/user/`, then
run `systemctl --user daemon-reload` and `systemctl --user enable --now <unit>`.
The script only writes files. Definitions are mode 600 because launchd agents
carry the SurrealDB password; systemd units read secrets from a separate
mode-600 environment file. It also writes OpenViking's `ov.conf` and creates
Open Notebook's run directory.

What the generated definitions handle for you:

- **Embedding server**: the batch size equals the context. With llama.cpp's
  default of 512, a 569-token chunk was rejected, OpenViking returned HTTP 500 on
  a later delete, and Workbench recorded the deletion as uncertain until
  `backend.reconcile` confirmed it.
- **Hindsight**: its embedded PostgreSQL stores data under `HOME`, so `HOME`
  is set to the service's own directory.
- **Open Notebook**: upstream resolves migrations, prompts and commands
  relative to the working directory and stores data in `./data`. The API and
  its `surreal-commands-worker` both run from `run_dir`, which links to the
  pinned source. Without the worker, sources stay at status `new`.
- **Soak runner**: restarts only after a failure, never after a finished run.

Start order: the model servers, then SurrealDB, Open Notebook, Hindsight and
OpenViking, then Paperclip, then the bridge (`awb service launchd`).

## 3. Configure the services

Open Notebook takes models through its API. Its `openai_compatible` provider
reads `base_url` and ignores the per-modality endpoint fields, so create one
credential per server:

```sh
B=http://127.0.0.1:5055/api
curl -X POST $B/credentials -H 'Content-Type: application/json' \
  -d '{"name":"local chat","provider":"openai_compatible","modalities":["language"],"api_key":"local","base_url":"http://127.0.0.1:8080/v1"}'
curl -X POST $B/credentials -H 'Content-Type: application/json' \
  -d '{"name":"local embeddings","provider":"openai_compatible","modalities":["embedding"],"api_key":"local","base_url":"http://127.0.0.1:8081/v1"}'
```

Then create a `language` model and an `embedding` model on those credentials
(`POST $B/models`), set them as defaults (`PUT $B/models/defaults`), check them
with `POST $B/models/{id}/test`, and create a notebook.

GPT Researcher reads a JSON config file. For the local servers and keyless search:

```json
{"RETRIEVER": "duckduckgo", "REPORT_SOURCE": "web",
 "FAST_LLM": "openai:local-chat", "SMART_LLM": "openai:local-chat", "STRATEGIC_LLM": "openai:local-chat",
 "EMBEDDING": "custom:local-embed", "CONTEXT_FILTER": "keyword"}
```

## 4. Connect Workbench

Merge entries like these into `integrations` in the Workbench `config.json`,
using your project IDs from `awb project list`:

```json
{
  "hindsight": {"enabled": true, "url": "http://127.0.0.1:8888", "auth": "local", "allow_write": true,
                "timeout_seconds": 120, "projects": {"PROJECT_ID": {"bank_id": "awb-example"}}},
  "openviking": {"enabled": true, "url": "http://127.0.0.1:1933", "auth": "local", "allow_write": true,
                 "timeout_seconds": 120, "projects": {"PROJECT_ID": {"root_uri": "viking://resources/workbench/example"}}},
  "open-notebook": {"enabled": true, "url": "http://127.0.0.1:5055", "api_prefix": "/api", "auth": "local",
                    "timeout": 300, "notebooks": {"PROJECT_ID": "notebook:ID"},
                    "ask_models": {"strategy_model": "model:ID", "answer_model": "model:ID", "final_answer_model": "model:ID"}},
  "gpt-researcher": {"enabled": true, "command": ["/home/you/awb/venvs/gpt-researcher/bin/python"], "auth": "local",
                     "config_path": "/home/you/awb/gpt-researcher.json",
                     "env": {"OPENAI_BASE_URL": "http://127.0.0.1:8080/v1", "OPENAI_API_KEY": "local"}}
}
```

Backend timeouts are limited to 120 seconds. See
[context backend configuration](CONTEXT_BACKENDS.md) for the actions.

## 5. Check each service

Make one source in a scratch project with `context.ingest`, then:

- `backend.ingest`, `backend.recall`, `backend.read`, `backend.delete` and
  `backend.status` for `hindsight` and `openviking`. Read-back must match the
  archived hash, and recall from a project without a binding must be refused.
- `integration.notebook` with `"action":"sources"`, then `"action":"ask"`,
  including a question the sources cannot answer.
- `integration.researcher`, then check each claim against the page it cites.

Observed with the verified versions and an 8B local model:

- Hindsight returned fact-level memories.
- OpenViking returned a document-level abstract. Its directory overview can
  summarize a deleted source until it is regenerated.
- Open Notebook answered with citations and said when the sources did not
  cover a question.
- A GPT Researcher report cited a correct fact to the wrong page and invented
  an acronym expansion.

Research and recall output is derived material. Review it before relying on
it.

# Setup

Install Python 3.11 or later and work from the reviewed repository checkout. The default coordinator is Paperclip. Temporal is an optional alternative; select one backend for an installation.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install '.[tui]'
awb init
awb doctor
```

Use `awb --home /absolute/private/state ...` when keeping separate installations. Configuration, installed tools, authenticated models, running workers and accepted outcomes are separate readiness states. A green configuration check does not establish the others.

## Default Paperclip backend

Use an isolated Paperclip installation and its pinned upstream setup. `awb deps install paperclip` fetches the recorded commit and runs its build. Node 24.11 or later, pnpm and a Rust toolchain (`cargo`, for Paperclip's runner binary) must already be on `PATH`. The upstream stack can require substantial disk space and PostgreSQL. Each dependency step writes its output to a private `install.log`; a failed attempt is moved aside as `REVISION.failed-*` with that log, so you can read the cause and retry without manual cleanup.

Complete Paperclip onboarding and create a company through its supported interface. Set `coordinator.kind` to `paperclip`, with the actual `url`, `company_id` and, where required, `token_env` in the Workbench state `config.json`. Keep the token in the named environment variable through your credential manager, never in a prompt, repository or URL.

The bridge needs permission to manage that company's projects, agent pool and issues. An authorization or board-approval failure remains a blocker. Existing agent adapter timeouts must match their plan budget before reuse. Changing a profile or runtime limit may require explicitly reconciling an old managed agent.

Before dispatching work for a project bound to a repository, enable isolated workspaces in Paperclip (Instance settings > Experimental). Without that setting, the pinned release runs "isolated" tasks in the registered checkout itself. The bridge then refuses to dispatch, and it cancels any repository issue that Paperclip returns without isolation. Paperclip creates each task worktree under the repository's `.paperclip/` directory. Add `.paperclip/` to `.git/info/exclude` to keep those worktrees out of `git status`.

## Models with or without provider API keys

Install and sign into the unmodified official Claude Code or Codex CLI on the worker machine. Choose a model and effort supported by that installed CLI/account. Replace the model value in this example with your actual supported model ID:

```json
{"action":"profile.add","args":{"identity":"my-codex","harness":"codex","model":"MODEL_ID_FROM_YOUR_CLI","effort":"high","auth":"subscription","classes":["simple","code","design","report","research"],"priority":10}}
```

Send the JSON using `awb request -`, then probe the configured profile:

```sh
awb request '{"action":"profile.probe","args":{"identity":"my-codex"}}'
```

A probe consumes a small amount of the account's allowance. It requires the exact assistant canary response and successful completion, records the CLI version and expires after seven days or a profile change. A failed reprobe invalidates the old qualification. Login must be completed by the account owner in the official CLI. Workbench does not extract subscription credentials or manufacture an API key from a sign-in.

For provider API or local inference, use the matching `auth` mode and configure a supported provider through the official CLI. There is no silent paid-provider fallback. The profile probe and Temporal CLI activity remove provider API-key environment variables in subscription mode and rely on the CLI's existing sign-in. For Paperclip, configure and qualify the actual adapter's service environment; the declared auth mode alone does not establish how a provider authenticated a run. A Claude probe records the model identity reported in the CLI's result (`modelUsage`) and fails when the requested model is not among them; Codex does not report it, so its receipt keeps the identity unknown. CLI qualification does not prove effort level, coordinated team behavior or output quality.

Paperclip receives `claude_local` or `codex_local` on its legacy CLI runner, so tasks run through the official CLI and its existing sign-in. The pinned native runner would substitute its bundled provider binary and approve every tool. Codex effort maps to `modelReasoningEffort`, Claude to `effort`. Permission and sandbox bypass keys are always sent as false. A profile that enables a bypass key or flag in `adapter_config` is refused before any agent is created, and the bridge refuses to reuse an agent that a coordinator converted to another runner. A changed profile, flag or instruction gets a new agent instead of reusing an older one.

Claude workers run with `--bare`, which keeps your personal plugins, hooks and CLAUDE.md out of product work, and with `--permission-mode acceptEdits`. Apart from editing files, they may run only `git status`, `git diff`, `git add` and `git commit`. They never push. A profile can grant narrower extra tools through `adapter_config.extraArgs`, for example `["--allowedTools", "Bash(/absolute/venv/bin/python -m pytest:*)"]`. Other commands are refused rather than prompted. Claude Code also refuses commands that contain environment variables.

A worker's final message is its report. Because bypass stays off, workers cannot close their own Paperclip issues. Instead, the bridge reads the earliest successful run on each finished issue. It records that run's final message, agent, hash and reported token usage, then marks the issue done. When every node has reported, it proposes one unverified outcome made of those reports, for independent review. Paperclip still starts disposition-repair runs for those issues: one immediately and another a minute later. In the recorded live run, each task used two such runs, which together took about five times the input tokens of the task run itself. That is the current cost of keeping bypass off. A task with a single predecessor starts in a worktree based on that predecessor's task branch, so a reviewer sees the committed implementation. Nodes with several predecessors keep their own workspace.

Temporal workers invoke the same official CLIs using their own locally qualified profiles.

## Optional Temporal backend and fleet

Install the optional SDK in the Workbench environment:

```sh
python -m pip install '.[tui,temporal]'
```

Install the official Temporal CLI and start a persistent local development server:

```sh
temporal server start-dev --ip 127.0.0.1 --port 7233 \
  --db-filename /absolute/private/state/temporal.sqlite --headless
```

Replace the `coordinator` object in the controller's configuration, retaining the rest of the configuration:

```json
{
  "kind": "temporal",
  "target": "127.0.0.1:7233",
  "namespace": "default",
  "task_queue": "awb-control",
  "host_id": "local"
}
```

Stop and reconcile old work before changing backends. The selection fence rejects changes while active or uncertain work remains; it does not migrate an existing Paperclip run into Temporal. A development server is not a hardened production deployment. Non-loopback Temporal targets require TLS; optional mTLS paths and `api_key_env` configure service access separately from model authentication.

Register a project, then register the worker's actual absolute workspace path using the returned project ID:

```json
{"action":"fleet.register","args":{"host_id":"local","name":"Local workstation","task_queue":"awb-local","capacity":4,"interactive_reserve":1,"capabilities":["codex"],"workspaces":{"PROJECT_ID":"/absolute/path/to/project"}}}
```

Registration returns a host token once. Save it in a private regular file with mode `0600`. Do not copy it into command arguments, logs or screenshots. Start a local worker:

```sh
awb --home /absolute/state temporal-worker \
  --host-id local --token-file /absolute/private/host-token
```

A remote worker requires its own Workbench state, qualified CLI profile, unique queue and checkout. Public profile ID, harness, model, effort and auth mode must match the dispatched profile. Use a host-scoped control connection rather than sharing the controller database:

```sh
awb --home /absolute/worker-state temporal-worker \
  --host-id remote-a --token-file /absolute/private/host-token \
  --control-endpoint http://127.0.0.1:18765
```

That loopback endpoint is the worker-side end of a secure tunnel. Direct remote control requires HTTPS; Temporal TLS is a separate connection. Workbench does not provision hosts, tunnels or shared-filesystem locks. Registration and heartbeat do not prove model readiness. Check `coordinator.health`, `fleet.status` and actual execution results separately. See [runtime setup and tests](RUNTIME_BACKENDS.md).

## Context, research and writing integrations

```sh
awb deps list
awb deps install basic-memory docling lcm superpowers elements-of-style humanizer
awb request '{"action":"capabilities.activate","args":{"names":["superpowers","elements-of-style","humanizer"]}}'
```

Inspect dependency receipts for actual paths. Command configuration uses argv arrays, not shell strings. Installation, configuration, procedure activation, successful execution and accepted outputs remain separate facts.

| Integration | Required configuration and boundary |
|---|---|
| Basic Memory | Enabled executable command, `auth: "local"`, explicit project registration through `integration.basic_memory` |
| Docling | Python executable command; call `integration.docling` for conversion |
| LCM | Service URL/token settings and a declared project repository binding; capture hooks need separate installation |
| QMD | Executable command and project-to-collection mapping; exact `search` and semantic `query` are separately qualified |
| Hindsight / OpenViking | Independently installed service, enabled URL/auth settings, explicit bank/root binding and `allow_write: true` for copies; use `backend.*` |
| Open Notebook | Service URL, `/api` prefix, token, notebook/model mapping and explicit provider auth mode |
| GPT Researcher | Python command, reviewed configuration path and explicit provider auth mode |
| Vale / Promptfoo | Executable command and reviewed configuration; provider-backed evaluations require their own credentials and approval |

Hindsight/OpenViking adapters are integrated, but their services and inference providers are not installed by the dependency command. A service may support a local model or an official subscription provider; that must be configured and tested in the service itself. A signed-in Workbench harness does not automatically authenticate another application. Service access tokens are distinct from provider API keys. There is no automatic upload or fallback. Use [context backend configuration](CONTEXT_BACKENDS.md) for mappings, limits, readback and deletion.

QMD's inspected release had a lockfile mismatch; a pinned source fetch is not a reproducible build claim. Open Notebook and GPT Researcher source fetches do not provision their full stacks or qualify generation. LCM capture/recall with summarization disabled does not qualify semantic summarization or complete cross-session capture. See the [compatibility matrix](COMPATIBILITY.md) for retained evidence and pending checks.

Named procedures are loaded with paths and hashes. Bundled short procedures have different provenance. An explicit Superpowers request blocks dispatch before its named procedures are activated. Keep the pinned checkout available where companion files are referenced. Clear-writing/Humanizer/Vale checks assist editing; they cannot guarantee meaning preservation or remove hallucinations.

## Local language and artifact checks

Register the repository, call `execution.create_workspace`, inspect it, and create an `execution.plan` for Python, JavaScript, TypeScript or Rust. Review the command and execute exactly its returned digest with `execution.run`. These actions are wired through the CLI/operator API. They run actual local commands without invoking an AI model.

Dependencies are not installed automatically. Package scripts require explicit opt-in; Rust checks use locked offline Cargo commands. Workspace copies preserve uncommitted source edits while excluding secrets, dependency directories and build outputs. The current copy limit is 10,000 files / 100 MiB; process runs are bounded to 0.1–600 seconds and 256 bytes–8 MiB captured output. A passing command or artifact hash does not certify a working website, signed mobile app or well-supported report. See [execution pack examples](EXECUTION_PACKS.md).

## WezTerm and service

Install the official WezTerm build for your platform and record its version. A native WezTerm UI has been run in the cloud environment. That evidence does not qualify the user's Mac fonts, shortcuts, configuration, reconnect, sleep or reboot behavior.

For a conventional configuration ending in `return config`:

```sh
awb terminal install --config-file "$HOME/.wezterm.lua" --executable "$HOME/.local/bin/awb"
```

Use your actual installed absolute executable path. The installer backs up the Lua file, installs a pinned local module and inserts one managed include. It preserves existing settings and reports a conflicting Ctrl+Shift+A shortcut. Complex Lua layouts need manual placement of the documented include. Use `--status` only when Workbench should own the right status area.

Ctrl+Shift+A opens `awb tui`. The view separates prompt destination from project filtering, outcomes from logs, and accepted deliverables from overall request completion. F1 shows controls; F2 creates a project; F3 amends; F4 confirms cancellation; F5 pauses new dispatch; F6 exports. Ctrl+B pins, Ctrl+D marks read, Ctrl+O reopens and Ctrl+Q closes the view. Closing the terminal does not stop coordinator-owned agents. See [terminal scenarios](TERMINAL_SCENARIOS.md).

`awb serve` runs the loopback operator/control service. `awb service launchd --executable /absolute/path/to/awb` or `awb service systemd --executable /absolute/path/to/awb` prints a service definition; it does not silently install or start it. Verify PATH, profile authentication, coordinator connectivity and restart behavior in the real service environment before cutover.

## Harness connection and qualification

Configure an MCP host with the installed absolute executable and arguments `["--home","/absolute/state/path","mcp"]`. Typed MCP tools cover requests/plans, capabilities, scoped context and backend recall, local execution, outcomes, checkpoints/rollover and writing checks. See [the exact MCP tool list](API.md). Use `awb request` or authenticated HTTP for the broader operator API. Do not give an operator token to untrusted workers.

The current defaults are six agent slots, eight checkpoint generations, one hour of task time and a 24,000-character execution packet. These are finite policy bounds, not unlimited context or a throughput guarantee. Run the [feature evaluation plan](FEATURE_TEST_PLAN.md) with actual configured services, models and desktop before claiming full readiness. Follow [the replacement procedure](INSTALL_AND_REPLACE_OLYMPUS.md) to preserve local changes and qualify the user machine before retiring Olympus.

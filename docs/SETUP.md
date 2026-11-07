# Setup

## Core and Paperclip

Install Workbench using the README. Run `awb doctor` before starting execution. Use an isolated Paperclip installation, following its pinned upstream instructions. `awb deps install paperclip` fetches the recorded commit and runs its build; Node and pnpm must already be available. The upstream package can require substantial disk space and PostgreSQL.

Complete Paperclip's onboarding and create a company using its supported interface. In the Workbench state `config.json`, set `coordinator.url`, `coordinator.company_id` and, where required, `coordinator.token_env`. Put a token in that environment variable through your normal credential manager, never inside a prompt, repository or URL. Local-trusted and authenticated deployments have different authorization behavior; preserve the mode you actually selected.

The bridge needs permission to manage the selected company's projects, agent pool and issues. A board approval or authorization failure is a blocker, not permission to disable the coordinator's policy.

## Models without API keys

Install and sign into the unmodified official Claude Code or Codex CLI on the target machine. Choose a model and effort supported by that installed CLI/account; Workbench does not invent universal model IDs.

```json
{"action":"profile.add","args":{"identity":"my-codex","harness":"codex","model":"MODEL_ID_FROM_YOUR_CLI","effort":"high","auth":"subscription","classes":["simple","code","design","report","research"],"priority":10}}
```

Send this JSON to `awb request -`, then run a probe:

```sh
awb request '{"action":"profile.probe","args":{"identity":"my-codex"}}'
```

This can consume a small amount of your normal account allowance. It requires an actual assistant canary event, records the CLI version, and expires after seven days or a profile change. It does not prove provider-side effective model/effort, team execution or output quality. Those are additional release checks. A failed login must be completed by the account owner in the official CLI; Workbench never extracts subscription tokens.

For API/local profiles, select the corresponding `auth` mode explicitly and configure the provider through the official CLI. There is no silent paid-provider fallback. Paperclip receives `claude_local` or `codex_local`; Codex effort maps to `modelReasoningEffort`, Claude to `effort`. Sandbox/permission bypass flags default to false.

## Optional context and writing integrations

```sh
awb deps list
awb deps install basic-memory docling lcm superpowers elements-of-style humanizer
awb request '{"action":"capabilities.activate","args":{"names":["superpowers","elements-of-style","humanizer"]}}'
```

Inspect the installation receipts for exact paths. Configure command arrays in `config.json`; do not use shell command strings. Dependencies remain separate private environments. Installation, configuration, activation, successful execution and accepted outputs are separate states.

| Integration | Configuration |
|---|---|
| Basic Memory | `enabled`, `command: ["/absolute/path/to/bm"]`, `auth: "local"`; register each project with `integration.basic_memory`, action `register` |
| Docling | `command: ["/absolute/path/to/venv/bin/python"]`; convert with `integration.docling` |
| LCM | `url`, `token_env`, root routes; bind a project to its declared repository directory |
| QMD | `command`, `collections: {"PROJECT_ID": "COLLECTION_NAME"}`; exact mode uses `search`, semantic mode uses `query` |
| Open Notebook | `url`, `api_prefix: "/api"`, `token_env`, `notebooks`, `ask_models`, explicit `auth: "api"` or `"local"` |
| GPT Researcher | Python command, reviewed `config_path`, explicit `auth: "api"` or `"local"` |
| Vale | Executable command and optional `--config=...`; inspect JSON warnings, not just exit code |
| Promptfoo | Executable command; a reviewed configuration determines whether an evaluation uses a provider |

QMD's inspected release had a lockfile mismatch. Its dependency command fetches pinned source but does not claim a reproducible build. Open Notebook and GPT Researcher source fetches do not install their service stacks or qualify generation. See the compatibility matrix before enabling either.

The LCM dependency installer copies required packaged YAML assets without running upstream's global plugin-cache sync. Harness capture hooks still need installation and target-host qualification. Workbench's explicit capture adapter does not establish that every turn in every host is captured.

Named upstream procedures are loaded with paths and hashes. The bundled short procedures are fallbacks with different provenance. If a prompt explicitly requests Superpowers before activation, dispatch blocks rather than claiming the named workflow ran. Procedure files may refer to companion files in their checkout; keep the pinned checkout available to the worker.

## WezTerm

Install the official WezTerm release for your platform and record its actual version. On macOS, follow [the official installation instructions](https://wezterm.org/install/macos.html). The current build does not install a GUI into a Linux container and call that a Mac test.

For a conventional config ending in `return config`:

```sh
awb terminal install --config-file "$HOME/.wezterm.lua" --executable "$HOME/.local/bin/awb"
```

This backs up the file, installs a pinned local module, and inserts one managed include. Existing theme/settings remain. Complex Lua layouts require the documented include to be placed manually. A conflicting Ctrl+Shift+A shortcut is reported rather than overwritten. Add `--status` only if Workbench should own the right status area.

Ctrl+Shift+A opens `awb tui` in a new tab. Inside it: Ctrl+N creates a prompt; Ctrl+B pins an outcome; Ctrl+D marks it read; Ctrl+O reopens a request; Ctrl+Q exits the view. Worker execution belongs to Paperclip, so closing the view is not evidence that a job stopped or resumed.

`awb serve` runs the local operator bridge. `awb service launchd --executable /absolute/path/to/awb` prints a service definition for review/installation. It does not start a service behind your back. The target installation agent must verify the service environment, official CLI PATH and authentication after login/reboot.

## MCP

Configure your host's MCP server entry with the installed absolute executable and arguments `["--home","/absolute/state/path","mcp"]`. The included stdio server offers prompt submission, scoped search, context packets, outcomes and checkpointing. Other actions use `awb request` or the authenticated loopback operator API. Do not publish the operator API token to untrusted workers.

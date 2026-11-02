# Olympus: a durable command room for Firstmate

Olympus adds a Rust coordinator and a Pi terminal to this Firstmate fork.
The coordinator retains projects, requirements, attempts, messages, evidence and recovery obligations independently of model conversations.
The current release is an installable engineering preview, with live model dispatch disabled in a new home.

## Install and try it

Supported platforms are Linux and macOS, with WSL2 as the Windows path.
The source installer requires Git, curl, tar and a native C build toolchain, bootstraps an isolated Rust toolchain if needed, installs private Node 24 when Node 22+ is absent, and installs Pi 1.4.0 plus TypeBox 1.4.3 under the chosen prefix.
It does not require sudo, alter shell startup files, create provider accounts or copy provider credentials.
Allow several minutes for the first source build.

```bash
curl -fsSL https://raw.githubusercontent.com/balrajOla/firstmate/codex/olympus-control-room/bin/olympus-install.sh | bash
export PATH="$HOME/.local/bin:$PATH"
olympus init
olympus start
olympus ui
```

To install a reviewed local checkout, run `bash bin/olympus-install.sh --source "$PWD" --prefix "$HOME/.local"`.
For a reproducible revision, check out the desired commit before running the local installer.
An upgrade replaces application binaries and assets; it does not migrate, erase or silently restart an existing coordinator.
Take a verified backup, stop the coordinator, install, then start it with the matching schema version.
The first released schema is version 1; no previous development databases are supported.

Use a separate home for the sample portfolio.

```bash
olympus --home "$HOME/.local/share/olympus-demo" demo
olympus --home "$HOME/.local/share/olympus-demo" start
olympus --home "$HOME/.local/share/olympus-demo" ui
```

The demo creates 20 clearly labelled synthetic projects, 12 working tasks, one recovery, three blockers, five recent responses and ten recent completions.
It makes no model calls and does not establish real harness reliability.
Closing the terminal leaves the coordinator running.

## Work through Pi

```bash
olympus chief
# In Pi: /olympus
# Optional: /olympus-auto on
```

The command creates a durable chief identity once, loads the extension, and gives its tool a scoped credential.
Pi uses your existing provider configuration or its own login flow.
Extra Pi arguments follow `--`, for example `olympus chief -- --help`.
The extension never falls back to the operator credential for model tool requests.
`olympus chief` exposes only the broker and action-reference tools to its model and blocks other tool calls, so routine coordination cannot issue a direct shell command to shut down its own service.
Workers still require separate OS isolation for a strict system-wide security boundary.
The interactive dashboard uses the operator connection for your own manual actions.

Automatic chief wakes are explicit: `/olympus-auto on`, or `olympus chief --auto`.
They coalesce unchanged conditions, wait at least 30 seconds between turns, never interrupt a busy turn, and stop after ten automatic turns until you renew the budget.
The daemon continues when the chief pauses, hits a budget, exits or loses context.
This preview does not restart a crashed Pi chief session automatically; an OS service keeps the coordinator alive and a reopened `olympus chief` reuses the durable identity.

## Your daily workflow

| You do | System behavior | Where to inspect |
|---|---|---|
| Give a request | `intake.preview` identifies explicit IDs or project aliases and returns candidate matches with reasons | `:` opens a non-mutating routing preview |
| Add related work | Chief creates a task in the selected project or proposes a versioned amendment | Projects; task detail |
| Change requirements | `task.amend` requires `expected_version`; old output becomes stale until the owner acknowledges the new version at a safe checkpoint | Task detail and requirement history |
| Ask for a team | `team.create` registers a captain and up to eight role-specific identities without launching idle workers | Crew hierarchy |
| Run independent work | Claim atomically reserves capacity; `dispatch.plan` recommends a verified role-capable profile; `operation.prepare` records a runtime effect | Live; Health; operations API |
| Ask a bounded sub-question | Reserve the child first; native launch requires separately verified child support, otherwise use an independent Firstmate worker | Crew; child dependency in task detail |
| Discuss work between agents | Scoped durable mailboxes distinguish persisted, delivered, read and acted receipts | Message API; incidents for overdue delivery |
| Receive output | Preserve full result and code revision; independent quality review accepts only current evidence | Five recent responses; scrollable History |
| Find a blocker | Numbered queue includes owner, reason and next check; other admitted work continues | Queue |
| Resume after failure | Inspect canonical process evidence, reconcile effects, fence the old attempt, preserve its workspace and create the replacement generation | Queue; task attempts |
| Renew context | Pause, create an exact record manifest, fetch records individually, verify hashes and transfer to a successor after stopping the old runtime | Context API |
| Build a second brain | Record source-backed candidate or verified knowledge; superseded records remain on disk and in SQLite | Memory; local `vault/` Markdown |

The routing preview never silently merges projects or overwrites requirements based on similarity.
The chief makes the semantic decision using the evidence and your charter.
It can form sub-captains with `agent.create`, subject to scope, a maximum depth of four and bounded management spans.
Native children have a separate maximum depth of six and consume the same admission capacity as independent workers.

## Current terminal views

These images render the shared production view from the running installed simulator.
They are exact text/layout examples with synthetic tasks, not evidence of live model work.

![Olympus live terminal](assets/olympus-live.png)

[Projects](assets/olympus-projects.svg) · [Crew](assets/olympus-crew.svg) · [Queue](assets/olympus-queue.svg) · [Memory](assets/olympus-memory.svg) · [Health](assets/olympus-health.svg) · [History](assets/olympus-history.svg)

## Live terminal controls

| Key | Action |
|---|---|
| `1` - `7` | Live, Projects, Crew, Queue, Memory, Health, History |
| `Tab` | Focus live work, recent responses or completions |
| `j` / `k`, arrow keys | Select records or scroll a full response |
| `Enter` | Open the full response or structured record |
| `PgUp` / `PgDn` | Page detail; load older history |
| `t` | Cycle response, completed-task and completed-project history |
| `/` | Search retained history |
| `End` | Return to live; clear new-response badge |
| `N` / `n` | New project / new task wizard |
| `a` | Amend the selected task with optimistic version checking |
| `p` / `r` | Pause / resume the selected task obligation |
| `:` | Preview a new request's project correlation |
| `Esc` / `q` | Back / detach |

Any fresh working task animates the fleet indicator even when the chief is idle.
Recovery has a separate indicator, and `--reduced-motion` retains textual status without motion.
The UI redraws locally at 250 ms and requests a snapshot once per second; animation does not call a model.
Cached state remains visible under disconnection but is explicitly marked stale and stops animating.
A heartbeat establishes contact, not useful progress.
The terminal sanitizes escape sequences from model output and adapts to an 80-column view.

## Enable real harnesses deliberately

Firstmate remains the owner of actual Claude, Codex, Muse and Pi process lifecycle.
Olympus uses `fm-brief.sh`, `fm-spawn.sh`, `fm-send.sh`, `fm-control.sh` and `fm-fleet-snapshot.sh`; it does not infer process truth from arbitrary pane text.
Use your configured Firstmate home, selected backend, installed harnesses and existing account permissions.
The fork's normal Firstmate lock, dispatch-profile and backend rules continue to apply and may refuse a command.

```bash
olympus doctor --firstmate-root /absolute/path/to/firstmate
olympus connect --firstmate-root /absolute/path/to/firstmate \
  --firstmate-home /absolute/path/to/your-firstmate-home
olympus observe
```

Connection initially enables observation only.
A registered profile is unverified until the operator records a real launch, message, interrupt, resume and stop conformance run for its exact harness version.
Native-child capability additionally requires child launch, peer messaging, stop and budget evidence.
Register and verify through the [action contract](olympus-api.md), then enable the coordinator's `dispatch_enabled` setting and reconnect with `--enable-execution`.
The bridge checks the installed version again before every effect.
A profile's exact version string must match the harness's complete `--version` output, as reported by `olympus doctor`.
Uncertain effects are never replayed automatically.

Claude, Codex and Muse have not been exercised with authenticated live sessions in this delivery environment.
Muse's exact installed product and CLI must be confirmed locally before a profile can be used.
Native-child interoperability is capability-gated, not claimed to work uniformly across vendors.

## Recovery and context

| Failure | Bound and response |
|---|---|
| Coordinator crash | SQLite WAL, full synchronization, durable request receipts and OS restart; accepted writes survive process restart |
| Two coordinators | Exclusive lock prevents two local daemon owners |
| Missed heartbeat | After 30 seconds, state becomes unknown; ownership and capacity stay reserved |
| Exact fresh runtime observation | Working state and matching Firstmate spawn generation can renew contact; endpoint existence alone cannot |
| Repeated no-progress checkpoint | Three repeats block the attempt; one changed-plan resume is permitted before escalation |
| Worker replacement | Operator confirms stopped runtime and reconciled effects; at most two replacements per task |
| Lost runtime-operation reply | Persist uncertainty before execution; require reconciliation instead of replay |
| Overloaded mailbox | 128 pending ordinary messages, 32 extra critical slots, coalesced persisted progress updates |
| Missed message deadline | Durable dead-letter record and visible incident |
| Chief overload | Bounded 20-project briefing and five exceptions, direct peer messages, scoped captains and wake budget |
| Context renewal | Required-record hashes, current version, workspace identity and all pending obligations must match before ownership transfer |

The system preserves recorded source context, not unrecorded model thoughts.
Hash completeness proves record fidelity; it cannot prove a successor understood every nuance.
No implementation can promise zero failures or zero semantic context loss.
The [verification record](olympus-verification.md) separates tested behavior from remaining release gates.

## Back up, restore and supervise

```bash
olympus backup create /absolute/path/to/new-snapshot
olympus backup verify /absolute/path/to/new-snapshot
olympus backup restore /absolute/path/to/new-snapshot /absolute/path/to/new-home
olympus --home /absolute/path/to/new-home start
```

Quiesce recorded workspaces before a full backup.
The backup includes a consistent SQLite snapshot, Markdown vault, Git bundles, binary patches and tracked plus nonignored untracked files for recorded workspaces.
A changing, active, unreadable, oversized or symlinked workspace refuses the snapshot rather than silently omitting it.
Gitignored files and external provider secrets require a separately configured backup.
Checksums detect corruption; they are not an authenticity signature against a malicious backup author.

Restore requires a new destination, disables dispatch, revokes agent credentials, invalidates profile verification and marks active ownership unknown.
Inspect archived workspaces and reconcile old machines before reconstructing them in a new location and resuming.
The preview does not automatically rewrite workspace paths, log into providers or resume restored external effects.
For encrypted offsite retention, configure Restic and run `olympus backup offsite /absolute/path/to/new-snapshot`.
Scheduled backups and automatic disaster-recovery failover remain release work.

To survive login or host restarts, print and inspect a service definition, then install it with your OS service manager.

```bash
olympus service systemd
olympus service launchd
```

Service installation and enablement are explicit operator actions.
The preview is one local coordinator with SQLite, not a distributed consensus system.
A same-user unrestricted shell can read same-user files or kill same-user processes; strict agent isolation requires a dedicated OS account, container or VM policy.
The broker's scoped credentials enforce its API boundaries but do not create an OS sandbox.

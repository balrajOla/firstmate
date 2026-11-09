# Terminal controls and evaluation scenarios

The terminal is an operator view of saved Workbench records. It does not start a model merely because a team row exists. Agent execution remains with the selected coordinator.

## Controls

| Control | Behavior |
|---|---|
| **Send** selector | Chooses the destination of new prompts. `Send: auto` uses the recorded routing rules. |
| **View** selector | Filters requests, outcomes, teams and context. It does not change the destination of a new prompt. Health and Logs remain global diagnostics. |
| Enter on a table row | Opens the complete request ID, requirements, result, deliverables or agent observation. |
| Ctrl+N | Focuses the prompt field. |
| F1 | Opens the action guide. |
| F2 | Creates a named project, optionally bound to an existing repository directory. |
| F3 | Adds an amendment to the selected request, preserving the original and checking the revision captured when the form opened. |
| F4 | Confirms cancellation of the selected request. The message distinguishes saving cancellation from observing that a worker stopped. |
| F5 | Pauses or resumes new dispatch for the viewed project, preserving its configured priority. |
| F6 | Exports the selected request/outcome to a new directory containing HTML, Markdown and JSON. Existing exports are not overwritten. |
| Ctrl+B / Ctrl+D | Pins an outcome / marks it read. Pinning also updates the visible order. |
| Ctrl+O | Reopens the selected request as a new saved request. |
| Ctrl+R / Ctrl+Q | Refreshes / closes the terminal view. Closing the view does not stop agents. |

Ticket columns show a short stable prefix. The selected detail contains the full request ID; all actions use that full ID. Request rows show current-revision deliverables as accepted/total. An accepted outcome card does not imply every deliverable in a request is complete.

Project filters and text search are applied before the 200-row display limit, so an older request or outcome can still be found. The header reports the number of request rows shown relative to the selected project's total. Tables scroll horizontally on narrow terminals. At 80×24, the detail area becomes shorter and remains independently scrollable.

A team without coordinator observation says `Planned; not observed`. Deterministic process profiles are labelled `Fixture`, and a failed observation refresh marks retained state stale. A recorded running status is still only a coordinator observation, not proof of output correctness. Malformed diagnostic JSON appears in Health instead of crashing the inbox. A bridge heartbeat older than 30 seconds is shown as stale.

Health also shows registered worker capacity, interactive reserves and active leases. Registration, heartbeat, model authentication and observed agent execution are separate facts. Expired leases are marked uncertain and keep occupying capacity until reconciled; the view does not imply that a timed-out process stopped.

## Executable journeys

These tests use Textual's real headless driver and the application's SQLite state. They exercise keyboard input, dialogs, filters and exported files. They do not qualify a native WezTerm desktop or real model execution.

| Test | Scenario and observable acceptance |
|---|---|
| `test_tui_project_amend_cancel_and_export_journey` | At 80×24, 120×36 and 160×48: create a project, submit a website request, amend it, inspect a clearly labelled fixture outcome, export all three formats, then cancel. Opening the cancel dialog alone must not cancel. |
| `test_view_filter_does_not_change_prompt_destination` | View Alpha while composing into Beta. Only Alpha's saved work is shown; the new prompt belongs to Beta. Changing views clears an outcome selection that disappeared. |
| `test_tui_pin_reorders_preserves_selection_and_log_flood` | Pin an older outcome, retain selection when it moves to the top, add 300 log events, and verify the selected result stays unchanged. An empty search clears stale actions safely. |
| `test_tui_stale_amendment_is_rejected_without_clobbering` | Another session changes a request after the amendment dialog opens. Saving the stale amendment must report the revision conflict and preserve the newer request. |
| `test_tui_planned_and_fixture_agents_are_not_claimed_live` | Display a team plan before an observation; then load an explicitly synthetic running observation and a disconnect. All three evidence states remain visible and correctly labelled. |
| `test_tui_deliverables_blockers_and_scoped_pause` | Show a request with two deliverables and no authenticated worker. Display 0/2 and the blocker, then pause and resume its project through the keyboard. |
| `test_tui_new_external_project_and_older_request_search` | Save more than 200 requests, find an older one by text, and make a project created by another session available in the selector. |
| `test_tui_corrupt_diagnostics_remain_visible_and_recoverable` | Start with invalid observation and health files. Keep requests usable, show the errors, repair the files and recover on refresh. |
| `test_tui_fleet_capacity_keeps_uncertain_work_visible` | Register a worker, distinguish registration from heartbeat, expire an admitted lease and show that its capacity remains occupied. Credentials must not appear in details. |

The existing `test_tui_submit_outcomes_and_log_flood` journeys remain in `tests/test_surfaces.py` and cover the same three terminal sizes.

```sh
.venv/bin/pytest -q tests/test_tui_scenarios.py tests/test_surfaces.py \
  --junitxml=.local-evidence/scenarios/tui.xml
```

The main scenario runner records screenshots of actual state during its 30-prompt exercise. Captures produced by the headless driver must remain labelled headless. A complete native-desktop qualification still requires opening this build in WezTerm, inspecting font/keyboard/resize behavior and testing reconnect, sleep and reboot on the target operating system.

## Native macOS WezTerm

`scripts/capture_wezterm_macos.py` runs the TUI inside the installed WezTerm application on a Mac. For each size (default 80×24, 120×36 and 160×48) it does the following:

1. Starts a separate WezTerm GUI process. Its config is a copy of `~/.wezterm.lua` plus the managed include, installed by the given release `awb`; the operator's own config file is not changed.
2. Reaches the tab strip with real key input through `wezterm cli` (Ctrl+N, then Tab) and walks every view with the arrow keys, then opens F1.
3. Records the pane geometry WezTerm reports, the text WezTerm rendered for each view and, when the login session is unlocked, a PNG of that window only. WezTerm also owns hidden helper windows, so the script captures the largest normal-layer window it owns.
4. Reports whether `wezterm show-keys` lists Ctrl+Shift+A opening the TUI (through `awb open`, or `awb tui` in older installs). WezTerm prints that binding as key `A` with `CTRL`. The script does not press the shortcut itself; a real keystroke needs macOS Accessibility permission for the sending app.

If the display cannot fit the requested rows at the configured font, the size is retried with a slightly smaller font, and the font used is recorded. A locked screen composites no windows, so screenshots are then recorded as not captured. The script shows whatever the given state holds and never manufactures outcomes. It needs macOS, WezTerm and `swiftc` (Xcode command line tools).

```sh
.venv/bin/python scripts/capture_wezterm_macos.py --home /absolute/demo-state \
  --executable "/absolute/release prefix/bin/awb" \
  --work /absolute/private/wezterm-work --output /absolute/private/wezterm-evidence
```


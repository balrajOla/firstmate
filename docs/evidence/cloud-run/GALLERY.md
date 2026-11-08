# Native WezTerm scenario gallery

Actual WezTerm windows on an authenticated Xvfb display, captured on 8 November 2026. The terminal plugin opens another real Workbench tab. The 30 controls pass; model work remains blocked, so these are not completed website/mobile/research outputs.

[Scenario results](scenario-results.json) · [Detailed traces](scenario-traces.zip) · [Image hashes and native observations](native-manifest.json) · [All 30 instructions](../../../evals/scenarios-30.json) · [Codex canary](codex-canary.json)

## 00-start

Real application before scenario prompts

![00-start: actual WezTerm; control evidence only](../../assets/cloud-run/00-start.png)

## 00-plugin-shortcut

View: inbox

![00-plugin-shortcut: actual WezTerm; control evidence only](../../assets/cloud-run/00-plugin-shortcut.png)

## S01

Create HarborWeb, a repair-cafe website with an accessible event list, booking form and volunteer signup. Keep tracking disabled and do not send personal data to third parties. Supply working source, tests and a short handover.

![S01: actual WezTerm; control evidence only](../../assets/cloud-run/S01.png)

## S02

Create PocketTasks, an offline-first mobile task app with local task storage, due dates and accessible controls. No account should be required. Provide source, tests and an installable app only after a real mobile build.

![S02: actual WezTerm; control evidence only](../../assets/cloud-run/S02.png)

## S03

Research long-context prompting versus retrieval-augmented generation for a daily assistant. Start with Lost in the Middle (https://arxiv.org/abs/2307.03172), RULER (https://arxiv.org/abs/2404.06654), and the comparative study (https://arxiv.org/abs/2407.16833). Use primary sources, compare methods and limits, and include citations and a concise recommendation without promising perfect recall.

![S03: actual WezTerm; control evidence only](../../assets/cloud-run/S03.png)

## S04

Add booking confirmation, keyboard navigation and clear error messages to HarborWeb. Preserve the event list and volunteer signup.

![S04: actual WezTerm; control evidence only](../../assets/cloud-run/S04.png)

## S05

Add recurring tasks and dark mode to PocketTasks. Keep local data usable without a network and cover recurrence boundaries in tests.

![S05: actual WezTerm; control evidence only](../../assets/cloud-run/S05.png)

## S06

Expand EvidenceReview with evidence that challenges using long context by default. Include cases where retrieval or a hybrid approach helps, and distinguish experimental findings from your inference.

![S06: actual WezTerm; control evidence only](../../assets/cloud-run/S06.png)

## S07

Change the analytics requirement: allow an optional aggregate usage report, but leave tracking off by default. Explain how this changes my original no-third-party-data constraint before implementing anything that sends data.

![S07: actual WezTerm; control evidence only](../../assets/cloud-run/S07.png)

## S08

Consider optional encrypted cloud sync for PocketTasks. Flag the tension with my earlier no-account requirement, and preserve a fully offline mode even if sync is added later.

![S08: actual WezTerm; control evidence only](../../assets/cloud-run/S08.png)

## S09

Extend EvidenceReview to separate advertised context size from demonstrated retrieval and reasoning performance. Add a small architecture diagram, a comparison table and a short executive summary.

![S09: actual WezTerm; control evidence only](../../assets/cloud-run/S09.png)

## S10

As a separate HarborWeb change, add event search with keyboard-friendly results and a useful empty state.

![S10: actual WezTerm; control evidence only](../../assets/cloud-run/S10.png)

## S11

As another HarborWeb change, add date and category filters that preserve selections in the URL.

![S11: actual WezTerm; control evidence only](../../assets/cloud-run/S11.png)

## S12

Combine the HarborWeb search and filter changes into one execution plan while keeping both original requests and each acceptance criterion visible.

![S12: actual WezTerm; control evidence only](../../assets/cloud-run/S12.png)

## S13

For the grouped HarborWeb search request, also ensure an empty search does not hide all events and that keyboard focus survives filtering.

![S13: actual WezTerm; control evidence only](../../assets/cloud-run/S13.png)

## S14

Withdraw the date/category filters for now. Keep event search, its accessibility criteria and the main HarborWeb website work active.

![S14: actual WezTerm; control evidence only](../../assets/cloud-run/S14.png)

## S15

Reopen the withdrawn HarborWeb filters as a new request linked to the old one; restore its original filter acceptance criteria.

![S15: actual WezTerm; control evidence only](../../assets/cloud-run/S15.png)

## S16

Create a separate PocketTasks export feature: export tasks as JSON and import them back without losing completed status or due dates.

![S16: actual WezTerm; control evidence only](../../assets/cloud-run/S16.png)

## S17

Create a separate PocketTasks reminder feature using local notifications; handle denied notification permission without breaking task editing.

![S17: actual WezTerm; control evidence only](../../assets/cloud-run/S17.png)

## S18

Group PocketTasks export/import and local reminders into one release plan, preserving each original request and acceptance criterion.

![S18: actual WezTerm; control evidence only](../../assets/cloud-run/S18.png)

## S19

Change the grouped reminder requirement: reminders must remain local and must not require cloud sync. Keep this requirement visible alongside the earlier optional-sync discussion.

![S19: actual WezTerm; control evidence only](../../assets/cloud-run/S19.png)

## S20

Retry my PocketTasks export/import submission after a lost acknowledgement, using exactly its original client key and payload.

![S20: actual WezTerm; control evidence only](../../assets/cloud-run/S20.png)

## S21

Try to add a paid booking option using the website revision I saw before the latest amendments. If it is stale, keep the newer requirements and tell me to refresh.

![S21: actual WezTerm; control evidence only](../../assets/cloud-run/S21.png)

## S22

Compare HarborWeb and PocketTasks and create a shared rollout checklist. I have not decided which project should own this work.

![S22: actual WezTerm; control evidence only](../../assets/cloud-run/S22.png)

## S23

Put the ambiguous HarborWeb/PocketTasks rollout comparison in EvidenceReview. This assignment does not grant it permission to read private project sources.

![S23: actual WezTerm; control evidence only](../../assets/cloud-run/S23.png)

## S24

Pause new EvidenceReview work while the website and mobile projects remain available.

![S24: actual WezTerm; control evidence only](../../assets/cloud-run/S24.png)

## S25

While EvidenceReview is paused, queue a counterargument memo defending direct long-context prompting where the evidence supports it. Include costs, latency and cases where retrieval can fail.

![S25: actual WezTerm; control evidence only](../../assets/cloud-run/S25.png)

## S26

Resume EvidenceReview and retain the original queue order and all pending research requests.

![S26: actual WezTerm; control evidence only](../../assets/cloud-run/S26.png)

## S27

Use only EvidenceReview sources when comparing context strategies. Keep HarborWeb and PocketTasks private notes out unless I explicitly grant access, and show any omissions rather than inventing coverage.

![S27: actual WezTerm; control evidence only](../../assets/cloud-run/S27.png)

## S28

Add the attached large acceptance checklist to HarborWeb. Preserve every original line, but block execution if the compiled packet exceeds the configured context budget instead of silently truncating it.

![S28: actual WezTerm; control evidence only](../../assets/cloud-run/S28.png)

## S29

After restarting the controller, prepare a concise EvidenceReview progress brief: what was requested, what is still queued or blocked, and which research claims have actually been checked. Keep the pending website and mobile work visible.

![S29: actual WezTerm; control evidence only](../../assets/cloud-run/S29.png)

## S30

Show me the saved outcomes for HarborWeb, PocketTasks and EvidenceReview separately from diagnostic logs. Identify missing website builds, mobile binaries, research citations and independent reviews instead of calling unfinished work complete.

![S30: actual WezTerm; control evidence only](../../assets/cloud-run/S30.png)

## 31-final-requests

Final native application state; absent outcomes or agents are not synthesized

![31-final-requests: actual WezTerm; control evidence only](../../assets/cloud-run/31-final-requests.png)

## 31-final-inbox

Final native application state; absent outcomes or agents are not synthesized

![31-final-inbox: actual WezTerm; control evidence only](../../assets/cloud-run/31-final-inbox.png)

## 31-final-teams

Final native application state; absent outcomes or agents are not synthesized

![31-final-teams: actual WezTerm; control evidence only](../../assets/cloud-run/31-final-teams.png)

## 31-final-outcomes

Final native application state; absent outcomes or agents are not synthesized

![31-final-outcomes: actual WezTerm; control evidence only](../../assets/cloud-run/31-final-outcomes.png)

## 31-final-health

Final native application state; absent outcomes or agents are not synthesized

![31-final-health: actual WezTerm; control evidence only](../../assets/cloud-run/31-final-health.png)

# Dependency decisions

The earlier source comparison informed this implementation; it did not establish a production winner through a complete live deployment. The current selection minimizes overlapping ownership.

| Concern | Selected component | Why / overlap avoided |
|---|---|---|
| Agent execution | Paperclip | One coordinator owns issues, agents and runs. No Olympus, Gas Town or Agent Orchestrator scheduler is stacked beside it. |
| Durable user intent and outcomes | Workbench SQLite/archive | Product-specific original prompts, grouped demands, review versions and evidence receipts remain independent of terminal scrollback. |
| Permitted conversation history | LCM | Capture and expandable history; summary generation is a separate qualified provider concern. |
| Editable durable knowledge | Basic Memory | Human-editable notes, scoped retrieval and optimistic writes; separate from raw history. |
| Optional search acceleration | QMD | Exact and semantic retrieval over bound collections; not a second authoritative memory store. |
| Document extraction | Docling | Format conversion with original byte preservation and reviewable coverage. |
| Brainstorming and execution procedures | Superpowers | Pinned upstream procedure activation, not another scheduler. |
| Editorial workflow | Elements of Style + optional Humanizer + Vale | Instructions and style checks inside one editing/review flow. No AI-detector optimization or fabricated anecdotes. |
| Evaluation | Promptfoo + local measured checks | Seeded controls, judge calibration and explicit missing evidence. A judge does not replace originals or human acceptance. |
| Research | Optional Open Notebook / GPT Researcher | Existing source-workspace/research capabilities behind bounded adapters; no invented subscription inference proxy. |
| Terminal | WezTerm module + Textual app | WezTerm handles tabs/navigation; the app reads durable records. Status callbacks do no model or network work. |
| Context continuation | Workbench checkpoint protocol through Paperclip | Fresh sessions consume a shared budget and require stop evidence. No independent Ralph process can duplicate work. |

Agent Orchestrator, Gas Town and Ralph Orchestrator remain comparison candidates, not installed runtime dependencies. Hindsight, OpenViking, Temporal and multi-host fleets remain optional future scope. Adding one requires a measured capability gap and a replacement/ownership decision.

All selected upstream source revisions are in `assets/dependencies.json`. The registry deliberately distinguishes source fetch, installed package, enabled integration and actual execution evidence. Upstream test counts from the comparison are not reused as tests of this combined system.

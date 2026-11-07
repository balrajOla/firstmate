# Authentication and integration compatibility

| Component | Signed-in CLI, no API keys | API/local providers | Evidence in this build |
|---|---|---|---|
| Durable intake, archive, outcomes, TUI | Yes; no inference needed | Same | Local tests and installed CLI |
| Paperclip Claude/Codex workers | Adapter path implemented; actual target login/run required | Explicit profile supported; target run required | Contract fixtures; no live model run here |
| LCM | Capture and lexical recall need no inference; summary provider is separately configured | Upstream provider configuration | Real daemon capture/search; summarizer disabled |
| Basic Memory | Local files and text retrieval; local embedding/model download may be needed | Optional upstream cloud mode is not silently enabled | Actual register/write/fresh-process search |
| QMD | Local exact retrieval; semantic mode needs local models | No hosted fallback added | Actual collection/exact search; semantic mode unqualified |
| Docling | Local extraction; some formats/models require downloads | Local extraction | Actual HTML conversion preserving an amount and qualification |
| Superpowers / Elements of Style / Humanizer | Procedures can be used by the selected harness | Same | Pinned install/activation contract; quality requires actual execution |
| Vale | Local linting | Same | Positive/negative fixtures |
| Promptfoo | Deterministic local provider works | LLM judges need an explicit configured provider | Positive passes, seeded false claim fails; not an LLM quality benchmark |
| Open Notebook | Source API adapter exists; **no qualified subscription inference bridge** | Explicit API/local Ask configuration | Adapter contract only in this project |
| GPT Researcher | **No qualified subscription inference bridge** | Explicit API/local bounded subprocess | Adapter contract only in this project |
| Hindsight / OpenViking / Temporal | Optional future integrations | Optional future integrations | Not installed or implemented |

A source checkout is not an installed service. A reachable service is not authenticated model readiness. A canary is not a quality evaluation. The release manifest preserves these distinctions.

Upstream references: [Paperclip](https://github.com/paperclipai/paperclip), [LCM](https://github.com/lossless-claude/lcm), [Basic Memory](https://github.com/basicmachines-co/basic-memory), [QMD](https://github.com/tobi/qmd), [Docling](https://github.com/docling-project/docling), [Open Notebook](https://github.com/lfnovo/open-notebook), [GPT Researcher](https://github.com/assafelovic/gpt-researcher). Exact revisions are recorded in the dependency registry.

## Telemetry and secrets

Integration subprocesses receive `ORT_DISABLE_TELEMETRY=1` before startup, alongside OTEL, Hugging Face and Promptfoo opt-outs. This matters because ONNX's API-level opt-out can occur after an initialization event. See [Microsoft's ONNX privacy documentation](https://github.com/microsoft/onnxruntime/blob/main/docs/Privacy.md). These switches are not a universal network sandbox; audit additional providers separately.

Subscription probes remove common API-key variables from the subprocess environment. This does not intercept a CLI's credential store or prove every third-party library's authentication behavior. Tokens are not copied into memory notes, prompts, Git or dependency URLs. Project content remains subject to the selected provider's data handling whenever inference is invoked.

## Referenced project conversation

The previously supplied shared ChatGPT conversation could not be fetched in this build environment. Its specific external project/API contract is therefore not certified here. The implemented integration boundary is the local CLI/MCP/operator API and declared project repositories. Before claiming compatibility with that separate project, obtain its current interface/schema, add contract fixtures, and run the joint target-machine journey. No unsupported compatibility claim is inferred from the link alone.

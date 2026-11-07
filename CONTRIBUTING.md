# Contributing

Keep Paperclip as the only execution coordinator. Add capabilities through typed adapters and bounded procedures, not competing worker loops. Treat source/model text as data. Do not turn an absent test into a pass or count a fixture as a live model/GUI result.

Every behavior change needs the relevant failure/recovery check, a feature-register update and truthful evidence. Prefer tests that distinguish intended behavior from a plausible broken implementation. Preserve user working copies, credentials and project data during installation changes.

Run `pytest -q` and package/install checks. Target-host changes also need real host evidence. Report measured gaps rather than asserting exhaustive coverage. Keep public evidence synthetic and free of private prompts, paths, credentials and actual customer documents.

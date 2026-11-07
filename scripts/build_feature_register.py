"""Validate and render the committed feature register; no private inputs needed."""

import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
features = json.loads((root / "features.json").read_text())
assert [f["id"] for f in features] == [f"PF{i:02d}" for i in range(1, 72)]
lines = [
    "# Complete feature register",
    "",
    "All 71 requirements are retained here. A row marked partial or pending is not a completed capability. Local tests, named-package probes and target-machine qualification are separate evidence levels.",
    "",
    "| ID | Feature | Status | Implementation / evidence | Remaining work |",
    "|---|---|---|---|---|",
]
for f in features:
    for p in f["files"]:
        assert (root / p).exists(), p
    links = ", ".join(f"[{Path(p).name}](../{p})" for p in f["files"])
    lines.append(
        f"| {f['id']} | {f['name']} | {f['status']} | {links} | {f['remaining']} |"
    )
(root / "docs/FEATURES.md").write_text("\n".join(lines) + "\n")

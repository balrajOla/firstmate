from __future__ import annotations
import json
from pathlib import Path
from .config import config
from .util import WorkbenchError, atomic, canonical, digest, now, read_json

SKILLS = {
    "superpowers": {
        "brainstorm": ["skills/brainstorming/SKILL.md"],
        "write-plan": ["skills/writing-plans/SKILL.md"],
        "test-and-review": [
            "skills/test-driven-development/SKILL.md",
            "skills/verification-before-completion/SKILL.md",
            "skills/requesting-code-review/SKILL.md",
        ],
    },
    "elements-of-style": {
        "clear-writing": ["skills/writing-clearly-and-concisely/SKILL.md"]
    },
    "humanizer": {"clear-writing": ["SKILL.md"]},
}


def activate(home, names):
    registry = json.loads(
        (Path(__file__).parent / "assets/dependencies.json").read_text()
    )
    settings = config(home)
    receipts = []
    for name in names:
        if name not in SKILLS:
            raise WorkbenchError(
                "Only reviewed procedure bundles can be activated by this command"
            )
        spec = registry[name]
        root = home / "dependencies" / name / spec["revision"] / "source"
        if not root.is_dir():
            raise WorkbenchError("Install the pinned dependency first: " + name)
        from .integrations import run_process

        revision = run_process(["git", "-C", str(root), "rev-parse", "HEAD"])[
            "stdout"
        ].strip()
        if revision != spec["revision"]:
            raise WorkbenchError("Procedure checkout is not at the pinned revision")
        for workflow, files in SKILLS[name].items():
            paths = []
            for relative in files:
                path = root / relative
                if not path.is_file():
                    raise WorkbenchError("Upstream procedure is missing: " + relative)
                paths.append(str(path))
                receipts.append(
                    {
                        "bundle": name,
                        "workflow": workflow,
                        "path": str(path),
                        "revision": revision,
                        "sha256": digest(path.read_bytes()),
                    }
                )
            existing = settings["skills"].get(workflow, [])
            if isinstance(existing, str):
                existing = [existing]
            settings["skills"][workflow] = list(dict.fromkeys([*existing, *paths]))
    atomic(home / "config.json", canonical(settings) + "\n")
    atomic(
        home / "skill-activation.json",
        canonical({"created": now(), "receipts": receipts}) + "\n",
    )
    return {
        "activated": receipts,
        "notice": "Activation records source versions. Workflow execution and output quality require their own evidence.",
    }


def registry(home):
    settings = config(home)
    dependencies = json.loads(
        (Path(__file__).parent / "assets/dependencies.json").read_text()
    )
    result = []
    for name, spec in dependencies.items():
        receipt = read_json(
            home / "dependencies" / name / spec["revision"] / "installed.json"
        )
        integration = settings["integrations"].get(name, {})
        result.append(
            {
                "name": name,
                "revision": spec["revision"],
                "installed": bool(receipt and receipt.get("state") == "installed"),
                "installation_state": receipt.get("state") if receipt else "absent",
                "enabled": integration.get("enabled", False),
                "auth": integration.get("auth"),
                "role": (
                    "procedure"
                    if name in SKILLS
                    else "coordinator" if name == "paperclip" else "integration"
                ),
                "verified": "Check retained execution evidence",
            }
        )
    return result

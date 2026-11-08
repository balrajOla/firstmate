"""Run selected real integrations with synthetic inputs and telemetry disabled.
No user corpus, API key, model invocation or cloud service is required.
Unrequested integrations are not qualified by this probe.
"""

import argparse, json, tempfile
from pathlib import Path
from assistant_workbench.app import Application
from assistant_workbench.integrations import Integrations
from assistant_workbench.util import canonical

p = argparse.ArgumentParser()
p.add_argument("--basic-memory")
p.add_argument("--docling-python")
p.add_argument("--qmd-js")
p.add_argument("--output", required=True)
args = p.parse_args()
root = Path(tempfile.mkdtemp(prefix="awb-local-probe-"))
app = Application(root / "state")
project = app.store.add_project("Integration")
settings = json.loads((app.home / "config.json").read_text())
if args.basic_memory:
    settings["integrations"]["basic-memory"] = {
        "enabled": True,
        "allow_write": True,
        "command": [str(Path(args.basic_memory).absolute())],
        "auth": "local",
        "env": {
            "BASIC_MEMORY_ENV": "test",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        },
    }
if args.docling_python:
    settings["integrations"]["docling"] = {
        "enabled": True,
        "command": [str(Path(args.docling_python).absolute())],
        "auth": "local",
        "env": {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
    }
if args.qmd_js:
    settings["integrations"]["qmd"] = {
        "enabled": True,
        "command": ["node", str(Path(args.qmd_js).absolute())],
        "auth": "local",
        "collections": {project["id"]: "workbench"},
        "env": {
            "QMD_CONFIG_DIR": str(root / "qmd-config"),
            "INDEX_PATH": str(root / "qmd.sqlite"),
        },
    }
(app.home / "config.json").write_text(canonical(settings))
checks = []
i = Integrations(app.store)


def test(name, fn):
    try:
        ok = bool(fn())
        checks.append({"check": name, "passed": ok})
        print(name, ok, flush=True)
    except Exception as exc:
        checks.append({"check": name, "passed": False, "error": str(exc)})
        print(name, str(exc), flush=True)


if args.basic_memory:
    test(
        "basic_memory_register",
        lambda: i.basic_memory(project["id"], "register")["exit_code"] == 0,
    )
    test(
        "basic_memory_write",
        lambda: i.basic_memory(
            project["id"],
            "write",
            identifier="decision.md",
            content="# Test decision\n\nZEPHYR-AWB-471 excludes refunds.",
        )["exit_code"]
        == 0,
    )
    test(
        "basic_memory_fresh_process_search",
        lambda: "ZEPHYR-AWB-471"
        in i.basic_memory(project["id"], "search", query="ZEPHYR-AWB-471")["stdout"],
    )
if args.docling_python:
    file = root / "facts.html"
    file.write_text(
        "<html><body><h1>Decision</h1><p>Revenue 17.50 GBP excludes refunds.</p></body></html>"
    )

    def docling():
        source = i.docling(project["id"], str(file))
        text = app.dispatch(
            "context.source", {"project": project["id"], "source_id": source["id"]}
        )["text"]
        return "17.50 GBP" in text and "excludes refunds" in text

    test("docling_preserves_quantity_and_qualification", docling)
if args.qmd_js:
    from assistant_workbench.integrations import run_process

    docs = root / "docs"
    docs.mkdir()
    (docs / "decision.md").write_text("# Decision\n\nQUARTZ-AWB-472 excludes VAT.")
    cmd, item = i.command("qmd")
    test(
        "qmd_register_collection",
        lambda: run_process(
            [*cmd, "collection", "add", str(docs), "--name", "workbench"],
            env=i._env(item),
        )["exit_code"]
        == 0,
    )
    test(
        "qmd_exact_retrieval",
        lambda: "QUARTZ-AWB-472" in i.qmd(project["id"], "QUARTZ-AWB-472")["stdout"],
    )
report = {
    "classification": "real local-package integration; synthetic corpus; no LLM or GUI evidence",
    "telemetry": "ORT_DISABLED before startup; OTEL/HF/Promptfoo opt-outs enabled",
    "checks": checks,
}
Path(args.output).write_text(json.dumps(report, indent=2) + "\n")
raise SystemExit(0 if checks and all(c["passed"] for c in checks) else 1)

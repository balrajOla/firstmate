#!/usr/bin/env python3
"""Launch pinned Pi through an already configured account bridge.
PI_GATEWAY_BRIDGE_DIR names the existing directory containing with-gateway.py
and extension.ts. OLYMPUS_PREFIX selects the installed Olympus prefix.
PI_NODE_COMMAND optionally selects Node. No provider code or credentials copied.
"""
import json
import os
from pathlib import Path
import shutil
import sys

bridge_value = os.environ.get("PI_GATEWAY_BRIDGE_DIR")
if not bridge_value:
    raise SystemExit("PI_GATEWAY_BRIDGE_DIR must name an existing account bridge")
bridge = Path(bridge_value).resolve()
prefix = Path(os.environ.get("OLYMPUS_PREFIX", str(bridge.parent / "olympus")))
cli = prefix / "lib/olympus/pi-runtime/node_modules/@earendil-works/pi-coding-agent/dist/cli.js"
node = os.environ.get("PI_NODE_COMMAND") or shutil.which("node")
if not node or not cli.is_file():
    raise SystemExit("The pinned Pi runtime and Node must be installed")
arguments = sys.argv[1:]
if arguments in (["--version"], ["-v"], ["-V"]):
    # Pi's VERSION comes from this same package.json, without loading its SDK.
    print(json.loads((cli.parent.parent / "package.json").read_text())["version"])
    raise SystemExit(0)
if arguments in (["--help"], ["-h"]):
    os.execv(node, [node, str(cli), *arguments])
environment = os.environ.copy()
environment.setdefault("PI_CODING_AGENT_DIR", str(bridge / "agent"))
environment["PI_GATEWAY_OLYMPUS_PREFIX"] = str(prefix)
resources = ["--no-extensions", "--no-context-files", "--no-skills", "--no-mcp", "--no-prompt-templates"]
if environment.get("OLYMPUS_CHIEF_MODE") == "1":
    resources = ["--no-extensions", "-e", "builtin:mcp", "-e", "builtin:codemode", "-e", "builtin:tool-search"]
os.execve(str(bridge / "with-gateway.py"), [str(bridge / "with-gateway.py"), "--", node, str(cli),
    *resources, "-e", str(bridge / "extension.ts"), *arguments], environment)

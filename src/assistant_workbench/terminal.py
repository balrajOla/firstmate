from __future__ import annotations
import json
from pathlib import Path
from .util import WorkbenchError, atomic, canonical, digest, now, read_json

START = "-- BEGIN ASSISTANT WORKBENCH MANAGED INCLUDE"
END = "-- END ASSISTANT WORKBENCH MANAGED INCLUDE"


def install(home, config_file, executable, status=False):
    path = Path(config_file).expanduser().absolute()
    exe = Path(executable).expanduser().resolve(strict=True)
    if path.is_symlink():
        raise WorkbenchError(
            "Config is a symlink; explicitly select and back up its resolved target"
        )
    old = (
        path.read_text()
        if path.exists()
        else "local wezterm = require 'wezterm'\nlocal config = wezterm.config_builder()\nreturn config\n"
    )
    receipt_file = home / "terminal-install.json"
    receipt = read_json(receipt_file)
    if START in old:
        if not receipt or digest(old) != receipt["config_after"]:
            raise WorkbenchError(
                "Managed config changed; review the saved backup and current file before reinstalling"
            )
        return receipt
    lines = old.splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines) if line.strip() == "return config"]
    if len(matches) != 1:
        raise WorkbenchError(
            "Complex Lua config: add the documented include manually before its return; no automatic rewrite was made"
        )
    plugin = home / "terminal/wezterm.lua"
    atomic(plugin, (Path(__file__).parent / "assets/wezterm.lua").read_bytes())
    q = lambda v: json.dumps(str(v), ensure_ascii=False)
    block = (
        START
        + "\nlocal awb = dofile("
        + q(plugin)
        + ")\nawb.apply_to_config(config, {home="
        + q(home)
        + ", executable="
        + q(exe)
        + ", status="
        + ("true" if status else "false")
        + "})\n"
        + END
        + "\n"
    )
    lines.insert(matches[0], block)
    new = "".join(lines)
    backup = home / "terminal" / ("wezterm-before-" + str(int(now())) + ".lua")
    atomic(backup, old)
    atomic(path, new, path.stat().st_mode & 0o777 if path.exists() else 0o600)
    receipt = {
        "config_file": str(path),
        "config_before": digest(old),
        "config_after": digest(new),
        "backup": str(backup),
        "block": block,
        "plugin": str(plugin),
        "created": now(),
        "gui_qualification": "not_run",
    }
    atomic(receipt_file, canonical(receipt))
    return receipt


def uninstall(home):
    receipt = read_json(home / "terminal-install.json")
    if not receipt:
        raise WorkbenchError("No owned terminal installation receipt")
    path = Path(receipt["config_file"])
    current = path.read_text()
    if current.count(receipt["block"]) != 1:
        raise WorkbenchError("Managed include changed; preserving local edits")
    # Remove only the exact include, preserving unrelated edits made afterward.
    atomic(path, current.replace(receipt["block"], ""), path.stat().st_mode & 0o777)
    return {
        "removed": "managed include",
        "preserved": "all other terminal configuration and recovery copies",
    }


def status(store):
    snapshot = store.snapshot()
    return {
        "generated_at": snapshot["generated_at"],
        "active": sum(
            r["status"] in {"queued", "running"} for r in snapshot["requests"]
        ),
        "unread": sum(not o["is_read"] for o in snapshot["outcomes"]),
        "blocked": len(snapshot["attention"]),
    }

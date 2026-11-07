from __future__ import annotations

import os
from pathlib import Path
from .util import WorkbenchError, atomic, canonical, read_json


def home_path(value=None) -> Path:
    return (
        Path(
            value
            or os.environ.get(
                "AWB_HOME", Path.home() / ".local/share/assistant-workbench"
            )
        )
        .expanduser()
        .resolve()
    )


DEFAULT = {
    "schema": 1,
    "coordinator": {
        "kind": "paperclip",
        "url": "http://127.0.0.1:3100",
        "company_id": None,
        "token_env": "AWB_PAPERCLIP_TOKEN",
    },
    "limits": {
        "max_agents": 6,
        "max_iterations": 8,
        "max_seconds": 3600,
        "context_chars": 24000,
        "max_child_depth": 2,
    },
    "models": [],
    "integrations": {},
    "skills": {},
    "audience": {"reader": "project owner", "style": "concise", "max_words": 600},
}


def config(home: Path):
    value = read_json(home / "config.json", DEFAULT)
    if (
        value.get("schema") != 1
        or value.get("coordinator", {}).get("kind") != "paperclip"
    ):
        raise WorkbenchError(
            "Unsupported configuration; Paperclip must be the single coordinator"
        )
    limits = value.get("limits", {})
    for key in DEFAULT["limits"]:
        if not isinstance(limits.get(key), int) or limits[key] < 1:
            raise WorkbenchError(f"limits.{key} must be a positive integer")
    for model in value.get("models", []):
        required = {
            "id",
            "harness",
            "model",
            "effort",
            "efforts",
            "auth",
            "classes",
            "verified",
        }
        if required - model.keys() or model["harness"] not in {
            "claude",
            "codex",
            "process",
        }:
            raise WorkbenchError(
                "Model profiles require explicit harness, model, supported efforts, auth and verification"
            )
        if model["effort"] not in model["efforts"] or model["auth"] not in {
            "subscription",
            "api",
            "local",
        }:
            raise WorkbenchError("Unsupported effort or authentication profile")
    return value


def initialize(home: Path):
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(home, 0o700)
    if not (home / "config.json").exists():
        atomic(home / "config.json", canonical(DEFAULT) + "\n")
    return config(home)

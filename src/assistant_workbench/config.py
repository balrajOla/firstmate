from __future__ import annotations

import os
import re
import urllib.parse
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
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise WorkbenchError("Unsupported configuration schema")
    coordinator = value.get("coordinator")
    if (
        not isinstance(coordinator, dict)
        or not isinstance(coordinator.get("kind"), str)
        or coordinator["kind"] not in {"paperclip", "temporal"}
    ):
        raise WorkbenchError("Select exactly one coordinator: paperclip or temporal")
    if coordinator["kind"] == "temporal":
        _validate_temporal(coordinator)
    integrations = value.get("integrations", {})
    if not isinstance(integrations, dict) or any(
        not isinstance(item, dict) for item in integrations.values()
    ):
        raise WorkbenchError("Integrations must map names to configuration objects")
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


def _validate_temporal(spec):
    """Validate configuration without importing an optional SDK or reading keys."""
    target = spec.get("target", "127.0.0.1:7233")
    if not isinstance(target, str) or any(c.isspace() for c in target):
        raise WorkbenchError("Temporal target must be host:port")
    try:
        parsed = urllib.parse.urlsplit("//" + target)
        port = parsed.port
    except ValueError as exc:
        raise WorkbenchError("Invalid Temporal target or port") from exc
    if (
        parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
        or not parsed.hostname
        or port is None
        or not 1 <= port <= 65535
        or any(c in target for c in "\\\x00")
    ):
        raise WorkbenchError(
            "Temporal target must be host:port without credentials or a path"
        )
    tls = spec.get("tls", False)
    if type(tls) is not bool:
        raise WorkbenchError("Temporal tls must be a boolean")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"} and not tls:
        raise WorkbenchError("A remote Temporal service requires TLS")
    for key, default in {
        "namespace": "default",
        "task_queue": "awb-control",
        "host_id": "local",
    }.items():
        item = spec.get(key, default)
        if not isinstance(item, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", item
        ):
            raise WorkbenchError(f"Temporal {key} must be a safe nonempty identifier")
    has_cert, has_key = bool(spec.get("client_cert")), bool(spec.get("client_key"))
    if has_cert != has_key or (has_cert and not tls):
        raise WorkbenchError(
            "Temporal mTLS requires TLS and both certificate and private-key paths"
        )
    for key in ("client_cert", "client_key"):
        if key in spec and (
            not isinstance(spec[key], str)
            or not spec[key]
            or any(c in spec[key] for c in "\x00\r\n")
            or not Path(spec[key]).is_absolute()
        ):
            raise WorkbenchError(
                "Temporal certificate and key paths must be explicit absolute paths"
            )
    env_name = spec.get("api_key_env")
    if env_name is not None and (
        not isinstance(env_name, str)
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env_name)
    ):
        raise WorkbenchError("Temporal api_key_env must name an environment variable")


def initialize(home: Path):
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(home, 0o700)
    if not (home / "config.json").exists():
        atomic(home / "config.json", canonical(DEFAULT) + "\n")
    return config(home)

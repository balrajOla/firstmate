"""Official CLI probes. Never extracts subscription credentials or proxies them."""

from __future__ import annotations
import json
import os
import shutil
from pathlib import Path
from .config import config
from .integrations import run_process
from .util import WorkbenchError, atomic, canonical, digest, now, uid


def add(home, identity, harness, model, effort, auth, classes, priority=100):
    if harness not in {"claude", "codex"} or auth not in {
        "subscription",
        "api",
        "local",
    }:
        raise WorkbenchError(
            "Choose an explicit supported harness and authentication profile"
        )
    settings = config(home)
    if any(m["id"] == identity for m in settings["models"]):
        raise WorkbenchError(
            "Profile already exists; make an explicit versioned change instead"
        )
    item = {
        "id": identity,
        "harness": harness,
        "model": model,
        "effort": effort,
        "efforts": [effort],
        "auth": auth,
        "classes": classes,
        "priority": priority,
        "verified": False,
    }
    settings["models"].append(item)
    atomic(home / "config.json", canonical(settings) + "\n")
    return item


def probe(home, identity):
    settings = config(home)
    model = next((m for m in settings["models"] if m["id"] == identity), None)
    if not model:
        raise WorkbenchError("Unknown profile")
    if Path(identity).name != identity or identity in {".", ".."}:
        raise WorkbenchError("Profile ID must be a plain filename component")
    # A failed re-probe must not leave last week's successful login active.
    model["verified"] = False
    atomic(home / "config.json", canonical(settings) + "\n")
    try:
        return _probe(home, identity)
    except (WorkbenchError, OSError, ValueError) as exc:
        atomic(
            home / "profiles" / (identity + ".json"),
            canonical(
                {
                    "schema": 1,
                    "result": "failed",
                    "created": now(),
                    "profile": {
                        k: model[k]
                        for k in ("id", "harness", "model", "effort", "auth")
                    },
                    "reason": str(exc),
                    "scope": "No successful official CLI canary; model remains unqualified.",
                }
            )
            + "\n",
        )
        raise


def _probe(home, identity):
    settings = config(home)
    model = next((m for m in settings["models"] if m["id"] == identity), None)
    if not model:
        raise WorkbenchError("Unknown profile")
    executable = shutil.which(model["harness"])
    if not executable:
        raise WorkbenchError(
            "Install and sign into the official "
            + model["harness"]
            + " CLI on this machine"
        )
    if model["harness"] not in {"claude", "codex"}:
        raise WorkbenchError("A process fixture cannot qualify as a language model")
    marker = uid("awb_probe_")
    prompt = (
        "Do not use tools or modify files. Reply with exactly this marker: " + marker
    )
    if model["harness"] == "claude":
        argv = [
            executable,
            "-p",
            prompt,
            "--model",
            model["model"],
            "--output-format",
            "json",
            "--max-turns",
            "1",
        ]
        if model["effort"] != "default":
            argv += ["--effort", model["effort"]]
    else:
        argv = [
            executable,
            "exec",
            "--json",
            "--model",
            model["model"],
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
        ]
        if model["effort"] != "default":
            argv += ["-c", "model_reasoning_effort=" + json.dumps(model["effort"])]
        argv.append(prompt)
    env = os.environ.copy()
    if model["auth"] == "subscription":
        for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AZURE_OPENAI_API_KEY"):
            env.pop(key, None)
    result = run_process(argv, env=env, timeout=120, cwd=home)
    if marker not in result["stdout"]:
        raise WorkbenchError(
            "CLI did not return the required canary marker; profile remains unqualified"
        )
    # Inspect the assistant/result event, not an echoed input, wherever the CLI exposes one.
    events = []
    for line in result["stdout"].splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    effective = []
    if model["harness"] == "claude":
        results = [
            e
            for e in events
            if e.get("type") == "result"
            and not e.get("is_error")
            and str(e.get("result", "")).strip() == marker
        ]
        valid = bool(results)
        # The CLI reports which models served the turn; the request alone
        # does not prove a gateway did not substitute another model.
        effective = sorted(
            {name for e in results for name in (e.get("modelUsage") or {})}
        )
        requested = model["model"]
        alias = not any(c.isdigit() for c in requested)
        if effective and requested not in effective and not (
            alias and any(requested in name for name in effective)
        ):
            raise WorkbenchError(
                "CLI answered with a different model ("
                + ", ".join(effective)
                + ") than the profile requested"
            )
    else:
        valid = any(
            e.get("type") == "item.completed"
            and (e.get("item") or {}).get("type") == "agent_message"
            and (e["item"].get("text") or "").strip() == marker
            for e in events
        )
        valid = (
            valid
            and any(e.get("type") == "turn.completed" for e in events)
            and not any(e.get("type") in {"turn.failed", "error"} for e in events)
        )
    if not valid:
        raise WorkbenchError(
            "No successful assistant event containing the canary; an echoed prompt is not execution evidence"
        )
    version = run_process([executable, "--version"], timeout=15)["stdout"].strip()
    receipt = {
        "schema": 1,
        "result": "passed",
        "created": now(),
        "profile": {k: model[k] for k in ("id", "harness", "model", "effort", "auth")},
        "cli_version": version,
        "effective_models": effective,
        "effective_model_source": (
            "CLI result modelUsage" if effective else "not reported by CLI"
        ),
        "argv": argv,
        "output_sha256": digest(result["stdout"]),
        "scope": "One official CLI canary with the CLI-reported model identity where available; not model ranking, coordinator execution, effort verification or semantic quality.",
    }
    path = home / "profiles" / (identity + ".json")
    if Path(identity).name != identity or identity in {".", ".."}:
        raise WorkbenchError("Profile ID must be a plain filename component")
    atomic(path, canonical(receipt) + "\n")
    model.update(verified=True, qualification=str(path))
    atomic(home / "config.json", canonical(settings) + "\n")
    return receipt


def readiness(model):
    from .util import read_json

    if not model.get("verified"):
        return False, "Profile has not passed a probe"
    # Process profiles are permitted only for explicitly labeled deterministic integration fixtures.
    if model["harness"] == "process":
        return (
            model.get("fixture_only") is True,
            "Deterministic fixture; never language-model qualification",
        )
    receipt = (
        read_json(Path(model.get("qualification", "")))
        if model.get("qualification")
        else None
    )
    if not receipt or receipt.get("result") != "passed":
        return False, "Missing qualification receipt"
    if not 0 <= now() - receipt.get("created", 0) < 7 * 86400:
        return False, "Qualification expired; rerun the official CLI probe"
    if receipt.get("profile") != {
        k: model[k] for k in ("id", "harness", "model", "effort", "auth")
    }:
        return False, "Profile changed since qualification"
    return (
        True,
        "Official CLI canary passed; live coordinator and quality checks remain separate",
    )

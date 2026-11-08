"""Generate user-service definitions for the optional local service stack.

Workbench's bridge has `awb service launchd|systemd`. The services it can
talk to (Paperclip, a local OpenAI-compatible model server, Hindsight,
OpenViking, SurrealDB and Open Notebook) are installed separately; this
script writes their launchd agents (macOS) or systemd user units (Linux)
from a JSON spec of absolute paths, so the same setup can be reproduced on
any machine. It never starts or stops anything.

Secrets are read from the files the spec names and are written only into
the generated definitions, which are created with mode 600. systemd units
read them from a separate mode-600 environment file. Nothing secret is
printed. See docs/LOCAL_SERVICES.md and examples/local-services.json.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import sys

SYSTEM_PATH = ["/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
# Telemetry opt-outs honoured by the upstream services.
QUIET = {"DO_NOT_TRACK": "1", "ANONYMIZED_TELEMETRY": "false", "HF_HUB_DISABLE_TELEMETRY": "1"}


class SpecError(ValueError):
    pass


def absolute(spec, key, where):
    value = spec.get(key)
    if not isinstance(value, str) or not os.path.isabs(value):
        raise SpecError(where + "." + key + " must be an absolute path")
    return value


def port(spec, key, default):
    value = spec.get(key, default)
    if not isinstance(value, int) or not 0 < value < 65536:
        raise SpecError(key + " must be a TCP port")
    return value


def secret(spec, key, where):
    path = Path(absolute(spec, key, where))
    if path.stat().st_mode & 0o077:
        raise SpecError(where + "." + key + " must not be readable by other users (chmod 600)")
    value = path.read_text().strip()
    if not value or "\n" in value:
        raise SpecError(where + "." + key + " must hold one non-empty line")
    return value


def llama(spec, where, embedding):
    argv = [absolute(spec, "server", where), "-m", absolute(spec, "model", where),
            "--alias", spec.get("alias", where), "--host", "127.0.0.1",
            "--port", str(port(spec, "port", 8081 if embedding else 8080)),
            "-c", str(spec.get("context", 2048 if embedding else 32768)), "-ngl", str(spec.get("gpu_layers", 99))]
    if embedding:
        # The batch must hold a whole chunk; a smaller one rejects long inputs.
        batch = str(spec.get("batch", spec.get("context", 2048)))
        argv += ["--embedding", "-b", batch, "-ub", batch]
    else:
        argv += ["--jinja"]
    return {"argv": argv + [str(a) for a in spec.get("args", [])], "env": {}, "secrets": {}}


def paperclip(spec, where):
    env = {"PAPERCLIP_HOME": absolute(spec, "home", where),
           "PAPERCLIP_INSTANCE_ID": spec.get("instance", "default"),
           "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0"}
    if spec.get("ca_file"):
        env["NODE_EXTRA_CA_CERTS"] = absolute(spec, "ca_file", where)
    if spec.get("corepack_home"):
        env["COREPACK_HOME"] = absolute(spec, "corepack_home", where)
    return {"argv": [absolute(spec, "pnpm", where), "paperclipai", "run"],
            "cwd": absolute(spec, "source", where), "env": env, "secrets": {}}


def hindsight(spec, where):
    # Its embedded PostgreSQL keeps data under HOME; give it its own.
    home = absolute(spec, "home", where)
    return {"argv": [absolute(spec, "executable", where), "--host", "127.0.0.1",
                     "--port", str(port(spec, "port", 8888))],
            "cwd": home,
            "env": {"HOME": home, "HINDSIGHT_API_LLM_PROVIDER": "openai",
                    "HINDSIGHT_API_LLM_BASE_URL": spec.get("llm_url", "http://127.0.0.1:8080/v1"),
                    "HINDSIGHT_API_LLM_MODEL": spec.get("llm_model", "llm"),
                    # A local server accepts any key; the client requires one.
                    "HINDSIGHT_API_LLM_API_KEY": "local-server-needs-no-key"},
            "secrets": {}}


def openviking(spec, where, files):
    home = absolute(spec, "home", where)
    config = Path(home) / "ov.conf"
    files[config] = json.dumps({
        "storage": {"workspace": str(Path(home) / "data")},
        "vlm": {"provider": "openai", "api_base": spec.get("vlm_url", "http://127.0.0.1:8080/v1"),
                "api_key": "local-server-needs-no-key", "model": spec.get("vlm_model", "llm")},
        "embedding": {"dense": {"provider": "openai", "api_base": spec.get("embedding_url", "http://127.0.0.1:8081/v1"),
                                "api_key": "local-server-needs-no-key", "model": spec.get("embedding_model", "embeddings"),
                                "dimension": spec.get("embedding_dimension", 768)}},
    }, indent=1) + "\n"
    return {"argv": [absolute(spec, "executable", where), "--config", str(config),
                     "--host", "127.0.0.1", "--port", str(port(spec, "port", 1933))],
            "cwd": home, "env": {"HOME": home}, "secrets": {}}


def surrealdb(spec, where):
    return {"argv": [absolute(spec, "executable", where), "start", "--log", "info",
                     "rocksdb:" + absolute(spec, "data", where)],
            "env": {"SURREAL_USER": spec.get("user", "root"),
                    "SURREAL_BIND": "127.0.0.1:" + str(port(spec, "port", 8000))},
            "secrets": {"SURREAL_PASS": secret(spec, "password_file", where)}}


def open_notebook(spec, where, links):
    source, run = absolute(spec, "source", where), absolute(spec, "run_dir", where)
    # Upstream reads migrations, prompts and commands relative to the working
    # directory and keeps data in ./data, so it runs from its own directory.
    for name in ("open_notebook", "prompts", "commands"):
        links[Path(run) / name] = Path(source) / name
    env = {"HOME": run, "PYTHONPATH": source, "API_HOST": "127.0.0.1",
           "API_PORT": str(port(spec, "port", 5055)), "API_RELOAD": "false",
           "SURREAL_URL": spec.get("surreal_url", "ws://127.0.0.1:8000/rpc"),
           "SURREAL_USER": spec.get("surreal_user", "root"),
           "SURREAL_NAMESPACE": spec.get("namespace", "open_notebook"),
           "SURREAL_DATABASE": spec.get("database", "open_notebook")}
    secrets = {"SURREAL_PASSWORD": secret(spec, "surreal_password_file", where),
               "OPEN_NOTEBOOK_ENCRYPTION_KEY": secret(spec, "encryption_key_file", where)}
    api = {"argv": [absolute(spec, "python", where), str(Path(source) / "run_api.py")],
           "cwd": run, "env": env, "secrets": secrets}
    worker = {"argv": [absolute(spec, "worker", where), "--import-modules", "commands",
                       "--max-tasks", str(spec.get("max_tasks", 2))],
              "cwd": run, "env": env, "secrets": secrets}
    return {"open-notebook": api, "open-notebook-worker": worker}


def soak(spec, where):
    argv = [absolute(spec, "python", where), "-I", absolute(spec, "script", where),
            "--awb", absolute(spec, "awb", where), "--home", absolute(spec, "home", where),
            "--live-project", spec["live_project"], "--intake-project", spec["intake_project"],
            "--hours", str(spec.get("hours", 72)), "--interval-minutes", str(spec.get("interval_minutes", 60)),
            "--output", absolute(spec, "output", where)]
    if spec.get("health_url"):
        argv += ["--health-url", spec["health_url"]]
    # A finished soak exits 0 and must not be restarted.
    return {"argv": argv, "env": {}, "secrets": {}, "restart_on_failure_only": True}


def plan(spec):
    """Every service definition, plus files and links to create."""
    if not isinstance(spec.get("services"), dict) or not spec["services"]:
        raise SpecError("services must name at least one service")
    known = {"paperclip", "llm", "embeddings", "hindsight", "openviking", "surrealdb", "open-notebook", "soak"}
    unknown = set(spec["services"]) - known
    if unknown:
        raise SpecError("Unknown services: " + ", ".join(sorted(unknown)))
    files, links, services = {}, {}, {}
    for name, item in spec["services"].items():
        if name == "llm":
            services[name] = llama(item, name, False)
        elif name == "embeddings":
            services[name] = llama(item, name, True)
        elif name == "paperclip":
            services[name] = paperclip(item, name)
        elif name == "hindsight":
            services[name] = hindsight(item, name)
        elif name == "openviking":
            services[name] = openviking(item, name, files)
        elif name == "surrealdb":
            services[name] = surrealdb(item, name)
        elif name == "open-notebook":
            services.update(open_notebook(item, name, links))
        elif name == "soak":
            services[name] = soak(item, name)
    extra = [absolute({"p": p}, "p", "path") for p in spec.get("path", [])]
    path = ":".join(dict.fromkeys(extra + SYSTEM_PATH))
    for definition in services.values():
        definition["env"] = {"PATH": path, **QUIET, **definition["env"]}
        if spec.get("ca_file"):
            ca = absolute(spec, "ca_file", "spec")
            definition["env"].update(SSL_CERT_FILE=ca, REQUESTS_CA_BUNDLE=ca)
    return services, files, links


def launchd(label, definition, logs):
    plist = {"Label": label, "ProgramArguments": definition["argv"],
             "EnvironmentVariables": {**definition["env"], **definition["secrets"]},
             "RunAtLoad": True,
             "KeepAlive": {"SuccessfulExit": False} if definition.get("restart_on_failure_only") else True,
             "ThrottleInterval": 10,
             "StandardOutPath": str(Path(logs) / (label + ".log")),
             "StandardErrorPath": str(Path(logs) / (label + ".log"))}
    if definition.get("cwd"):
        plist["WorkingDirectory"] = definition["cwd"]
    return plistlib.dumps(plist).decode()


def systemd_quote(value):
    if any(c in value for c in "\n\r\x00"):
        raise SpecError("Service values cannot contain control characters")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$") + '"'


def systemd(label, definition, env_file):
    lines = ["[Unit]", "Description=" + label, "[Service]",
             "ExecStart=" + " ".join(systemd_quote(a) for a in definition["argv"])]
    if definition.get("cwd"):
        lines.append("WorkingDirectory=" + systemd_quote(definition["cwd"]))
    lines += ["Environment=" + systemd_quote(k + "=" + v) for k, v in sorted(definition["env"].items())]
    if definition["secrets"]:
        lines.append("EnvironmentFile=" + systemd_quote(str(env_file)))
    lines += ["Restart=" + ("on-failure" if definition.get("restart_on_failure_only") else "always"),
              "RestartSec=10", "[Install]", "WantedBy=default.target"]
    return "\n".join(lines) + "\n"


def write_private(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(text)
    os.chmod(path, 0o600)


def generate(spec, kind, output):
    if kind not in ("launchd", "systemd"):
        raise SpecError("kind must be launchd or systemd")
    prefix = spec.get("label_prefix", "com.assistant-workbench")
    logs = absolute(spec, "logs", "spec")
    services, files, links = plan(spec)
    written = []
    for name, definition in services.items():
        label = prefix + "." + name
        if kind == "launchd":
            target = output / (label + ".plist")
            write_private(target, launchd(label, definition, logs))
        else:
            target = output / (label + ".service")
            env_file = output / (label + ".env")
            if definition["secrets"]:
                write_private(env_file, "".join(k + "=" + v + "\n" for k, v in definition["secrets"].items()))
            write_private(target, systemd(label, definition, env_file))
        written.append(str(target))
    for path, text in files.items():
        write_private(path, text)
        written.append(str(path))
    for link, target in links.items():
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink() and Path(os.readlink(link)) == target:
            continue
        if link.exists() or link.is_symlink():
            raise SpecError(str(link) + " exists and is not the expected link")
        link.symlink_to(target)
        written.append(str(link))
    return {"kind": kind, "written": written,
            "next": ("launchctl bootstrap gui/$(id -u) <plist>; stop with launchctl bootout gui/$(id -u)/<label>"
                     if kind == "launchd" else
                     "copy units to ~/.config/systemd/user/, then systemctl --user daemon-reload && systemctl --user enable --now <unit>")}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--kind", choices=["launchd", "systemd"], required=True)
    parser.add_argument("--output", type=Path, required=True, help="directory for the generated definitions")
    args = parser.parse_args()
    try:
        print(json.dumps(generate(json.loads(args.spec.read_text()), args.kind, args.output), indent=1))
    except (SpecError, OSError, KeyError, ValueError) as exc:
        raise SystemExit("local_services: " + str(exc))


if __name__ == "__main__":
    sys.exit(main())

from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path
from . import __version__
from .app import Application
from .config import home_path, initialize
from .util import WorkbenchError, canonical, uid


def parser():
    p = argparse.ArgumentParser(
        prog="awb",
        description="Assistant Workbench: persistent context, agents and outcomes",
    )
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--home")
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("doctor")
    commands.add_parser("snapshot")
    commands.add_parser("tui")
    commands.add_parser("mcp")
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=8765)
    worker = commands.add_parser("temporal-worker")
    worker.add_argument("--host-id", required=True)
    worker.add_argument("--token-file", required=True)
    worker.add_argument("--control-endpoint")
    project = commands.add_parser("project")
    ps = project.add_subparsers(dest="operation", required=True)
    ps.add_parser("list")
    pa = ps.add_parser("add")
    pa.add_argument("name")
    pa.add_argument("--repository")
    pa.add_argument("--alias", action="append", default=[])
    submit = commands.add_parser("submit")
    submit.add_argument("text")
    submit.add_argument("--project")
    submit.add_argument("--key")
    submit.add_argument("--reply-to")
    submit.add_argument("--expected-revision", type=int)
    request = commands.add_parser("request")
    request.add_argument("json", help="JSON action envelope or '-' for stdin")
    commands.add_parser("tick")
    export = commands.add_parser("export")
    export.add_argument("request_id")
    export.add_argument("directory")
    migration = commands.add_parser("migrate")
    ms = migration.add_subparsers(dest="operation", required=True)
    discover = ms.add_parser("discover")
    discover.add_argument("--account-home")
    discover.add_argument("--prefix")
    backup = ms.add_parser("backup")
    backup.add_argument("destination")
    backup.add_argument("roots", nargs="+")
    backup.add_argument("--account-home")
    for name in ["verify", "restore", "retire"]:
        m = ms.add_parser(name)
        m.add_argument("destination")
        if name == "retire":
            m.add_argument("--qualification", required=True)
    install = commands.add_parser("install")
    install.add_argument("--source", required=True)
    install.add_argument("--prefix", required=True)
    uninstall = commands.add_parser("uninstall")
    uninstall.add_argument("--prefix", required=True)
    rollback = commands.add_parser("rollback")
    rollback.add_argument("--prefix", required=True)
    deps = commands.add_parser("deps")
    deps.add_argument("operation", choices=["list", "install"])
    deps.add_argument("names", nargs="*")
    service = commands.add_parser("service")
    service.add_argument("kind", choices=["launchd", "systemd"])
    service.add_argument("--executable", required=True)
    service.add_argument("--label", default="com.assistant-workbench.bridge",
                         help="launchd label; give each additional Workbench home its own")
    service.add_argument("--port", type=int, help="bridge port; give each additional home its own")
    backup = commands.add_parser("backup")
    backup.add_argument("destination")
    restore = commands.add_parser("restore")
    restore.add_argument("source")
    restore.add_argument("destination")
    terminal = commands.add_parser("terminal")
    terminal.add_argument("operation", choices=["install", "uninstall"])
    terminal.add_argument("--config-file")
    terminal.add_argument("--executable")
    terminal.add_argument("--status", action="store_true")
    qualify = commands.add_parser("qualify")
    qualify.add_argument("--profile", default="subscription")
    qualify.add_argument("--output")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    home = home_path(args.home)
    try:
        if args.command == "migrate":
            from . import migration

            data = vars(args)
            data.pop("command")
            data.pop("home")
            operation = data.pop("operation")
            result = getattr(migration, operation)(**data)
        elif args.command in {"install", "uninstall", "rollback", "restore"}:
            from . import operations

            data = vars(args)
            command = data.pop("command")
            data.pop("home")
            result = getattr(
                operations, command if command != "restore" else "restore_state"
            )(**data)
        else:
            app = Application(home)
            if args.command == "init":
                from .config import config

                result = {
                    "home": str(home),
                    "coordinator": config(home)["coordinator"]["kind"],
                    "next": "Configure one coordinator and qualified model profiles in config.json; run awb doctor",
                }
            elif args.command in {"doctor", "snapshot"}:
                result = app.dispatch(args.command)
            elif args.command == "project":
                result = app.dispatch(
                    "project." + args.operation,
                    (
                        {
                            "name": args.name,
                            "repository": args.repository,
                            "aliases": args.alias,
                        }
                        if args.operation == "add"
                        else {}
                    ),
                )
            elif args.command == "submit":
                result = app.dispatch(
                    "prompt.submit",
                    {
                        "text": args.text,
                        "client_key": args.key or uid("cli_"),
                        "project": args.project,
                        "reply_to": args.reply_to,
                        "expected_revision": args.expected_revision,
                    },
                )
            elif args.command == "request":
                value = json.loads(sys.stdin.read() if args.json == "-" else args.json)
                result = app.dispatch(value["action"], value.get("args", {}))
            elif args.command == "tick":
                result = app.dispatch("coordinator.tick")
            elif args.command == "temporal-worker":
                import asyncio
                import stat
                from .config import config
                from .temporal_runtime import serve_worker

                token_path = Path(args.token_file)
                if token_path.is_symlink() or not token_path.is_file():
                    raise WorkbenchError("Host token must be a private regular file")
                if stat.S_IMODE(token_path.stat().st_mode) & 0o077:
                    raise WorkbenchError(
                        "Host token file must not be accessible to other users"
                    )
                token = token_path.read_text().strip()
                if len(token) < 32:
                    raise WorkbenchError("Host token is invalid")
                try:
                    asyncio.run(
                        serve_worker(
                            app.store,
                            config(home),
                            args.host_id,
                            token,
                            control_endpoint=args.control_endpoint,
                        )
                    )
                except KeyboardInterrupt:
                    pass
                return 0
            elif args.command == "serve":
                from .server import make_server

                server = make_server(home, args.port, background=True)
                print(
                    canonical(
                        {
                            "listening": "127.0.0.1",
                            "port": server.server_port,
                            "credentials_file": str(home / "service.token"),
                        }
                    ),
                    flush=True,
                )
                try:
                    server.serve_forever(poll_interval=0.2)
                except KeyboardInterrupt:
                    pass
                finally:
                    server.workbench_stop.set()
                    server.server_close()
                return 0
            elif args.command == "tui":
                from .tui import WorkbenchApp

                WorkbenchApp(home).run()
                return 0
            elif args.command == "mcp":
                from .mcp import serve_stdio

                serve_stdio(home)
                return 0
            elif args.command == "export":
                from .reports import export_report

                result = export_report(app.store, args.request_id, Path(args.directory))
            elif args.command == "backup":
                from .operations import backup_state

                result = backup_state(app.store, args.destination)
            elif args.command == "service":
                from .operations import service_definition

                print(service_definition(home, args.executable, args.kind, args.label, args.port))
                return 0
            elif args.command == "deps":
                from .operations import install_dependencies

                result = (
                    json.loads(
                        (Path(__file__).parent / "assets/dependencies.json").read_text()
                    )
                    if args.operation == "list"
                    else install_dependencies(home, args.names)
                )
            elif args.command == "terminal":
                from . import terminal

                if args.operation == "install":
                    if not args.config_file or not args.executable:
                        raise WorkbenchError(
                            "Terminal install requires --config-file and --executable"
                        )
                    result = terminal.install(
                        home, args.config_file, args.executable, args.status
                    )
                else:
                    result = terminal.uninstall(home)
            elif args.command == "qualify":
                from .qualification import qualification

                result = qualification(app, args.profile, args.output)
                print(json.dumps(result, indent=2))
                return 0 if result["qualified"] else 3
            else:
                raise WorkbenchError("Unsupported command")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (WorkbenchError, ValueError, KeyError, TypeError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

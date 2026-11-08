"""Capture S01–S30 in a real WezTerm window on an authenticated Xvfb display.

This is a native terminal rendering check, not a substitute for authenticated
model, artifact or user-desktop qualification. No outcome is manufactured.
Xvfb, WezTerm and ImageMagick must be installed explicitly by the operator.
"""

from __future__ import annotations

import argparse
import ctypes as C
import ctypes.util
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid

from assistant_workbench.app import Application
from assistant_workbench.server import make_server
from assistant_workbench.terminal import install, status as terminal_status
from assistant_workbench.util import atomic, canonical, now

from run_scenarios import coordinator_probe, run_scenarios, save_json


class XKeyEvent(C.Structure):
    _fields_ = [
        ("type", C.c_int), ("serial", C.c_ulong), ("send_event", C.c_int),
        ("display", C.c_void_p), ("window", C.c_ulong), ("root", C.c_ulong),
        ("subwindow", C.c_ulong), ("time", C.c_ulong), ("x", C.c_int),
        ("y", C.c_int), ("x_root", C.c_int), ("y_root", C.c_int),
        ("state", C.c_uint), ("keycode", C.c_uint), ("same_screen", C.c_int),
    ]


class XButtonEvent(C.Structure):
    _fields_ = [(name, typ) for name, typ in XKeyEvent._fields_]
    # The X keycode and button fields have identical ABI layout.


class XEvent(C.Union):
    _fields_ = [("key", XKeyEvent), ("button", XButtonEvent), ("pad", C.c_long * 24)]


class XWindow:
    """Minimal Xlib input/capture target; no model state is drawn by this code."""

    def __init__(self, display, xtst=None):
        self.x = C.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
        definitions = {
            "XOpenDisplay": ([C.c_char_p], C.c_void_p),
            "XDefaultRootWindow": ([C.c_void_p], C.c_ulong),
            "XQueryTree": ([C.c_void_p, C.c_ulong, C.POINTER(C.c_ulong), C.POINTER(C.c_ulong), C.POINTER(C.POINTER(C.c_ulong)), C.POINTER(C.c_uint)], C.c_int),
            "XGetGeometry": ([C.c_void_p, C.c_ulong, C.POINTER(C.c_ulong), C.POINTER(C.c_int), C.POINTER(C.c_int), C.POINTER(C.c_uint), C.POINTER(C.c_uint), C.POINTER(C.c_uint), C.POINTER(C.c_uint)], C.c_int),
            "XSetInputFocus": ([C.c_void_p, C.c_ulong, C.c_int, C.c_ulong], C.c_int),
            "XRaiseWindow": ([C.c_void_p, C.c_ulong], C.c_int),
            "XWarpPointer": ([C.c_void_p, C.c_ulong, C.c_ulong, C.c_int, C.c_int, C.c_uint, C.c_uint, C.c_int, C.c_int], C.c_int),
            "XStringToKeysym": ([C.c_char_p], C.c_ulong),
            "XKeysymToKeycode": ([C.c_void_p, C.c_ulong], C.c_uint),
            "XSendEvent": ([C.c_void_p, C.c_ulong, C.c_int, C.c_long, C.POINTER(XEvent)], C.c_int),
            "XFlush": ([C.c_void_p], C.c_int),
            "XFree": ([C.c_void_p], C.c_int),
            "XCloseDisplay": ([C.c_void_p], C.c_int),
        }
        for name, (args, result) in definitions.items():
            function = getattr(self.x, name)
            function.argtypes, function.restype = args, result
        self.display = self.x.XOpenDisplay(display.encode())
        if not self.display:
            raise RuntimeError("XOpenDisplay failed; check the Xauthority cookie and display address")
        self.root = self.x.XDefaultRootWindow(self.display)
        self.window = None
        self.xtst = C.CDLL(xtst) if xtst else None
        if self.xtst:
            self.xtst.XTestFakeKeyEvent.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_ulong]
            self.xtst.XTestFakeButtonEvent.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_ulong]

    def geometry(self, window):
        root, x, y = C.c_ulong(), C.c_int(), C.c_int()
        width, height, border, depth = C.c_uint(), C.c_uint(), C.c_uint(), C.c_uint()
        ok = self.x.XGetGeometry(self.display, window, C.byref(root), C.byref(x), C.byref(y), C.byref(width), C.byref(height), C.byref(border), C.byref(depth))
        return (width.value, height.value) if ok else (0, 0)

    def discover(self, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            root, parent, count = C.c_ulong(), C.c_ulong(), C.c_uint()
            children = C.POINTER(C.c_ulong)()
            self.x.XQueryTree(self.display, self.root, C.byref(root), C.byref(parent), C.byref(children), C.byref(count))
            candidates = []
            try:
                for i in range(count.value):
                    window = children[i]
                    width, height = self.geometry(window)
                    if width >= 500 and height >= 300:
                        candidates.append((width * height, window))
            finally:
                if children:
                    self.x.XFree(children)
            if candidates:
                self.window = max(candidates)[1]
                self.focus()
                return self.window
            time.sleep(.1)
        raise RuntimeError("WezTerm did not create a captureable X window")

    def focus(self):
        self.x.XRaiseWindow(self.display, self.window)
        self.x.XSetInputFocus(self.display, self.window, 2, 0)
        self.x.XFlush(self.display)

    def key(self, keysym, modifiers=0):
        code = self.x.XKeysymToKeycode(self.display, self.x.XStringToKeysym(keysym.encode()))
        if not code:
            raise ValueError("X keyboard has no key for " + keysym)
        if self.xtst:
            held = []
            for bit, name in [(1, "Shift_L"), (4, "Control_L")]:
                if modifiers & bit:
                    modifier = self.x.XKeysymToKeycode(self.display, self.x.XStringToKeysym(name.encode()))
                    self.xtst.XTestFakeKeyEvent(self.display, modifier, True, 0)
                    held.append(modifier)
            self.xtst.XTestFakeKeyEvent(self.display, code, True, 0)
            self.xtst.XTestFakeKeyEvent(self.display, code, False, 0)
            for modifier in reversed(held):
                self.xtst.XTestFakeKeyEvent(self.display, modifier, False, 0)
            self.x.XFlush(self.display)
            return
        for kind, mask in [(2, 1), (3, 2)]:
            event = XEvent()
            event.key = XKeyEvent(type=kind, send_event=1, display=self.display, window=self.window, root=self.root, state=modifiers, keycode=code, same_screen=1)
            self.x.XSendEvent(self.display, self.window, False, mask, C.byref(event))
        self.x.XFlush(self.display)

    def text(self, text):
        for character in text:
            if character == "_":
                self.key("minus", 1)
            elif character.isascii() and character.isalnum():
                self.key(character.lower(), 1 if character.isupper() else 0)
            else:
                raise ValueError("Native search input only accepts ASCII request identifiers")

    def click(self, x, y):
        self.x.XWarpPointer(self.display, 0, self.window, 0, 0, 0, 0, int(x), int(y))
        if self.xtst:
            self.xtst.XTestFakeButtonEvent(self.display, 1, True, 0)
            self.xtst.XTestFakeButtonEvent(self.display, 1, False, 0)
            self.x.XFlush(self.display)
            return
        for kind, mask in [(4, 4), (5, 8)]:
            event = XEvent()
            event.button = XButtonEvent(type=kind, send_event=1, display=self.display, window=self.window, root=self.root, x=int(x), y=int(y), x_root=int(x), y_root=int(y), keycode=1, same_screen=1)
            self.x.XSendEvent(self.display, self.window, False, mask, C.byref(event))
        self.x.XFlush(self.display)

    def close(self):
        if self.display:
            self.x.XCloseDisplay(self.display)
            self.display = None


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_port(port, process, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Service exited before accepting connections; inspect native-processes.log")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.2):
                return
        except OSError:
            time.sleep(.1)
    raise RuntimeError("Service startup exceeded its bounded deadline")


def executable(value, name):
    found = str(Path(value).expanduser().resolve()) if value else shutil.which(name)
    if not found or not Path(found).is_file():
        raise ValueError("Supply an installed executable with --" + name)
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wezterm")
    parser.add_argument("--xtst", help="Optional explicit libXtst library for native XTEST keyboard and pointer input")
    parser.add_argument("--xvfb")
    parser.add_argument("--awb")
    parser.add_argument("--library-path", help="Extra native shared-library directory for explicitly installed tools")
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--display", type=int, default=93)
    parser.add_argument("--columns", type=int, default=140)
    parser.add_argument("--rows", type=int, default=42)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--temporal", help="Optional Temporal CLI: start an isolated real development service for bounded coordinator probes")
    parser.add_argument("--unqualified-codex-model", help="Explicitly add an unqualified subscription profile; it must remain blocked, never treated as a working model")
    parser.add_argument("--live", action="store_true", help="Probe the configured coordinator; this does not provide model credentials")
    parser.add_argument("--settle-seconds", type=float, default=.65)
    args = parser.parse_args(argv)
    if not 1 <= args.display <= 499 or args.columns < 80 or args.rows < 24 or not .1 <= args.settle_seconds <= 5:
        parser.error("Use a display in 1–499, at least 80×24 cells and a 0.1–5 second settle interval")
    home, output = args.home.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "native-manifest.json"
    if manifest_path.exists() or (output / "scenario-results.json").exists():
        parser.error("Use a new output directory; retained native evidence is immutable")
    wezterm = executable(args.wezterm, "wezterm")
    xvfb = executable(args.xvfb, "Xvfb")
    awb = executable(args.awb or (Path(sys.executable).parent / "awb" if (Path(sys.executable).parent / "awb").exists() else None), "awb")
    image_import = executable(None, "import")
    app = Application(home)
    run_id = args.run_id or "native-" + uuid.uuid4().hex[:12]
    xtst = str(Path(args.xtst).resolve()) if args.xtst else ctypes.util.find_library("Xtst")
    receipt = {"schema": 1, "run_id": run_id, "started_at": datetime.now(timezone.utc).isoformat(), "capture_kind": "native WezTerm on authenticated Xvfb", "notice": "Actual native terminal pixels. Scenario bookkeeping and blocked-state observations do not certify generated products, model execution or a user desktop.", "home": str(home), "tools": {"wezterm": wezterm, "awb": awb, "xvfb": xvfb, "image_import": image_import, "xtst": xtst}, "captures": [], "plugin_shortcut": {"status": "not_run"}}
    save_json(manifest_path, receipt)
    processes, window, server, server_thread = [], None, None, None
    env = os.environ.copy()
    # Give this GUI and its CLI one private mux namespace. An inherited outer
    # terminal's socket/pane variables must never redirect these controls.
    for variable in ("WEZTERM_UNIX_SOCKET", "WEZTERM_PANE", "WEZTERM_EXECUTABLE", "WEZTERM_EXECUTABLE_DIR"):
        env.pop(variable, None)
    runtime_directory = output / "runtime"
    runtime_directory.mkdir(mode=0o700)
    env["XDG_RUNTIME_DIR"] = str(runtime_directory)
    if args.library_path:
        env["LD_LIBRARY_PATH"] = str(Path(args.library_path).resolve()) + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
    cookie_file = output / "capture.Xauthority"
    fields = [b"", str(args.display).encode(), b"MIT-MAGIC-COOKIE-1", secrets.token_bytes(16)]
    atomic(cookie_file, struct.pack(">H", 65535) + b"".join(struct.pack(">H", len(field)) + field for field in fields))
    env.update(DISPLAY=f"127.0.0.1:{args.display}", XAUTHORITY=str(cookie_file))
    # Xlib initializes in this process; the tools use the same authenticated display.
    original_authority = os.environ.get("XAUTHORITY")
    os.environ["XAUTHORITY"] = str(cookie_file)
    try:
        with (output / "native-processes.log").open("w") as log:
            settings = json.loads((home / "config.json").read_text())
            if args.temporal:
                temporal = executable(args.temporal, "temporal")
                port = free_port()
                process = subprocess.Popen([temporal, "server", "start-dev", "--ip", "127.0.0.1", "--port", str(port), "--headless", "--db-filename", str(output / "temporal.sqlite")], env=env, stdout=log, stderr=log)
                processes.append(process)
                wait_port(port, process, timeout=30)
                settings["coordinator"] = {"kind": "temporal", "target": "127.0.0.1:" + str(port), "namespace": "default", "task_queue": "native-capture", "host_id": "native-local"}
                receipt["temporal"] = {"binary": temporal, "target": settings["coordinator"]["target"], "status": "running development server", "no_worker_or_model_implied": True}
            if args.unqualified_codex_model:
                if settings.get("models"):
                    raise ValueError("Refusing to replace existing model profiles; use a fresh scenario home")
                settings["models"] = [{"id": "native-unqualified-codex", "harness": "codex", "model": args.unqualified_codex_model, "effort": "default", "efforts": ["default"], "auth": "subscription", "classes": ["all"], "verified": False, "priority": 1}]
            if args.temporal or args.unqualified_codex_model:
                atomic(home / "config.json", canonical(settings))
            server = make_server(home, background=False)
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            config_file = output / "wezterm-native.lua"
            atomic(config_file, "local wezterm = require 'wezterm'\nlocal config = {enable_wayland=false, front_end='Software', check_for_updates=false, font=wezterm.font('DejaVu Sans Mono'), font_size=11.0, initial_cols=" + str(args.columns) + ", initial_rows=" + str(args.rows) + ", window_decorations='NONE', window_padding={left=0,right=0,top=0,bottom=0}, use_fancy_tab_bar=false, enable_tab_bar=true, hide_tab_bar_if_only_one_tab=false, color_scheme='Builtin Solarized Dark'}\nreturn config\n")
            launch_log = output / "tui-launches.jsonl"
            launcher = output / "capture-awb"
            # A transparent test launcher records launches then replaces itself
            # with the real installed CLI. It draws no terminal content.
            atomic(launcher, "#!" + sys.executable + "\nimport json, os, sys, time\n"
                   + "fd = os.open(" + repr(str(launch_log)) + ", os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)\n"
                   + "os.write(fd, (json.dumps({'pid': os.getpid(), 'at': time.time(), 'args': sys.argv[1:], 'executable': " + repr(awb) + "}) + '\\n').encode())\nos.close(fd)\n"
                   + "os.execv(" + repr(awb) + ", [" + repr(awb) + ", *sys.argv[1:]])\n", 0o700)
            receipt["terminal_install"] = install(home, config_file, launcher, status=True)
            receipt["launcher"] = {"path": str(launcher), "launch_log": str(launch_log), "sha256": hashlib.sha256(launcher.read_bytes()).hexdigest(), "notice": "Test-only logger immediately execs the real awb executable with unchanged arguments."}
            xvfb_process = subprocess.Popen([xvfb, ":" + str(args.display), "-screen", "0", "1800x1200x24", "-nolisten", "unix", "-nolisten", "local", "-listen", "tcp", "-auth", str(cookie_file)], env=env, stdout=log, stderr=log)
            processes.append(xvfb_process)
            wait_port(6000 + args.display, xvfb_process)
            window = XWindow(env["DISPLAY"], xtst)
            window_class = "awb-native-" + uuid.uuid4().hex[:12]
            wezterm_process = subprocess.Popen([wezterm, "--config-file", str(config_file), "start", "--always-new-process", "--class", window_class, "--", str(launcher), "--home", str(home), "tui"], env=env, stdout=log, stderr=log)
            processes.append(wezterm_process)
            window.discover()
            time.sleep(2)

            def publish_status():
                atomic(home / "snapshot.json", canonical(app.dispatch("snapshot")))
                atomic(home / "terminal-status.json", canonical(terminal_status(app.store)))
                atomic(home / "bridge-health.json", canonical({"status": "running", "at": now(), "reason": "Native capture HTTP bridge; coordinator probes are recorded per step"}))

            def capture(name, **metadata):
                if wezterm_process.poll() is not None:
                    raise RuntimeError("WezTerm exited before capture")
                window.focus()
                time.sleep(args.settle_seconds)
                path = output / (name + ".png")
                subprocess.run([image_import, "-window", str(window.window), str(path)], env=env, check=True, capture_output=True, timeout=15)
                data = path.read_bytes()
                width, height = struct.unpack(">II", data[16:24])
                item = {"file": str(path), "sha256": hashlib.sha256(data).hexdigest(), "width": width, "height": height, "window_id": window.window, "captured_at": datetime.now(timezone.utc).isoformat(), **metadata}
                receipt["captures"].append(item)
                save_json(manifest_path, receipt)
                return item

            publish_status()
            capture("00-start", view="inbox", notice="Real application before scenario prompts")
            before = [json.loads(line) for line in launch_log.read_text().splitlines()]
            window.key("a", 4 | 1)
            time.sleep(1.5)
            after = [json.loads(line) for line in launch_log.read_text().splitlines()]
            receipt["plugin_shortcut"] = {"status": "passed" if len(after) == len(before) + 1 else "failed", "keys": "CTRL+SHIFT+A", "launches_before": before, "launches_after": after, "notice": "The real plugin must launch another real CLI; inspect the paired native image for the second rendered tab."}
            capture("00-plugin-shortcut", view="inbox", plugin_shortcut=receipt["plugin_shortcut"])

            tab_columns = {"inbox": 3, "requests": 12, "outcomes": 22, "teams": 31, "context": 39, "health": 48, "logs": 55}

            def navigate(view, request_id=None):
                window.focus()
                if view in {"outcomes", "teams"}:
                    # Empty outcome/team views should not retain the preceding
                    # request's detail in their independent inspector pane.
                    window.key("a", 4 | 1)
                    time.sleep(.8)
                window.key("r", 4)
                width, height = window.geometry(window.window)
                cell_width, cell_height = width / args.columns, height / (args.rows + 1)
                window.click(tab_columns[view] * cell_width, 7.5 * cell_height)
                time.sleep(.12)
                if view == "requests":
                    window.click(20 * cell_width, 10.5 * cell_height)
                    time.sleep(.12)
                    window.key("Home")
                    window.key("k", 4)
                    if request_id:
                        window.text(request_id)
                    time.sleep(.15)
                    window.key("Tab")
                    window.key("Return")
                    time.sleep(.15)
                else:
                    # Select the first row in nonempty views so detail matches
                    # the inspected tab rather than a preceding request.
                    row = 13 if view == "outcomes" else 10
                    window.click(20 * cell_width, row * cell_height)
                    window.key("Return")

            def on_step(step_id, result):
                probes = []
                if args.live or args.temporal:
                    for action in ("coordinator.tick", "coordinator.observe"):
                        probe_path = output / "coordinator-probes" / (step_id + "-" + action + ".json")
                        probes.append(coordinator_probe(home, action, probe_path, timeout=12))
                    observed = probes[-1]
                    atomic(home / "observation-health.json", canonical({"status": "observed" if observed["status"] == "observed" else "unavailable", "at": now(), "reason": observed.get("reason", "Actual selected coordinator queried"), "records": len(observed.get("result") or {})}))
                publish_status()
                details = result.get("details", {})
                request_id = details.get("request_id") or details.get("receipt", {}).get("request_id")
                view = "context" if result["action"] in {"context_scope", "oversized"} else "outcomes" if result["action"] == "outcomes" else "health" if result["action"] == "restart" else "requests"
                navigate(view, request_id)
                # Save the actual state after coordinator probes as well as the
                # runner's immutable pre-callback assertion snapshot.
                snapshot = output / "native-snapshots" / (step_id + ".json")
                save_json(snapshot, app.dispatch("snapshot"))
                item = capture(step_id, step_id=step_id, action=result["action"], instruction=result["instruction"], control_status=result["status"], view=view, request_id=request_id, state_snapshot=str(snapshot), assertion_snapshot=result["snapshot"], coordinator_probes=probes)
                print(json.dumps({"step": step_id, "control": result["status"], "native_capture": item["file"], "view": view}), flush=True)
                return {"status": "captured", "kind": receipt["capture_kind"], "screenshot": item["file"], "sha256": item["sha256"], "state_snapshot": str(snapshot)}

            report = run_scenarios(home, output / "scenario-results.json", manifest_path=args.manifest, live=False, on_step=on_step, run_id=run_id)
            for view in ("requests", "inbox", "teams", "outcomes", "health"):
                publish_status()
                navigate(view)
                capture("31-final-" + view, view=view, notice="Final native application state; absent outcomes or agents are not synthesized")
            receipt.update(bookkeeping_status=report["bookkeeping_status"], control_counts=report["counts"], delivery=report["delivery"], capture_failures=report["capture_failures"], durable_prompts=report["durable_prompts"], native_step_captures=sum("step_id" in item for item in receipt["captures"]), finished_at=datetime.now(timezone.utc).isoformat())
            receipt["status"] = "passed" if receipt["native_step_captures"] == 30 and not report["capture_failures"] and report["bookkeeping_status"] == "passed" and receipt["plugin_shortcut"]["status"] == "passed" else "failed"
            save_json(manifest_path, receipt)
            print(json.dumps({"native_status": receipt["status"], "native_step_captures": receipt["native_step_captures"], "control_counts": report["counts"], "delivery_status": report["delivery"]["status"], "manifest": str(manifest_path)}), flush=True)
            return 0 if receipt["status"] == "passed" else 1
    except Exception as exc:
        receipt.update(status="failed", error=str(exc), error_type=type(exc).__name__)
        save_json(manifest_path, receipt)
        raise
    finally:
        if window:
            window.close()
        if server:
            server.workbench_stop.set()
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=3)
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        if original_authority is None:
            os.environ.pop("XAUTHORITY", None)
        else:
            os.environ["XAUTHORITY"] = original_authority


if __name__ == "__main__":
    raise SystemExit(main())

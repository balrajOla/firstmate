"""Exercise the Workbench TUI in the native macOS WezTerm application.

For each requested size this launches a separate WezTerm GUI process whose
config is a copy of the operator's config plus the managed Workbench include,
installed by the release executable. It then walks every TUI view with real
key input through ``wezterm cli`` and records:

* the pane geometry WezTerm reports (columns, rows, pixels);
* the text WezTerm rendered for each view (``wezterm cli get-text``);
* a PNG of that window only, when the login session is unlocked.

A locked screen composites no windows, so screenshots are then recorded as
not captured instead of being faked. The script never manufactures outcomes;
it shows whatever the given Workbench state holds. It needs macOS, WezTerm and
the Xcode command line tools (``swiftc``) for the window lookup.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

WEZTERM = "/Applications/WezTerm.app/Contents/MacOS/wezterm"
TABS = ["inbox", "requests", "outcomes", "teams", "context", "health", "logs"]
KEYS = {"compose": "\x0e", "tab": "\t", "right": "\x1b[C", "f1": "\x1bOP", "escape": "\x1b"}

WINDOW_TOOL = r"""
import CoreGraphics
import Foundation
let args = CommandLine.arguments
if args[1] == "locked" {
    let d = CGSessionCopyCurrentDictionary() as? [String: Any] ?? [:]
    print((d["CGSSessionScreenIsLocked"] as? Int) ?? 0)
    exit(0)
}
let pid = Int32(args[2])!
let list = CGWindowListCopyWindowInfo([.optionAll], kCGNullWindowID) as! [[String: Any]]
// WezTerm also owns hidden helper windows; the terminal is the largest one.
var found: [(Double, Int)] = []
for w in list where (w[kCGWindowOwnerPID as String] as? Int32) == pid
    && (w[kCGWindowLayer as String] as? Int) == 0 {
    let b = w[kCGWindowBounds as String] as! [String: Any]
    found.append((((b["Width"] as? Double) ?? 0) * ((b["Height"] as? Double) ?? 0), w[kCGWindowNumber as String] as! Int))
}
for (area, id) in found.sorted(by: { $0.0 > $1.0 }) where area > 0 { print(id) }
"""


def run(argv, **kwargs):
    return subprocess.run(argv, capture_output=True, text=True, timeout=60, **kwargs)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def shortcut_bound(keys):
    """Whether `wezterm show-keys --lua` lists Ctrl+Shift+A opening the TUI,
    through `awb open` or, in older installs, `awb tui`.

    WezTerm normalizes Ctrl+Shift+A to key 'A' with mods 'CTRL'.
    """
    for line in keys.splitlines():
        match = re.search(r"key = '([Aa])', mods = '([A-Z|]+)'", line)
        if match and ("'open'" in line or "'tui'" in line):
            mods = set(match.group(2).split("|"))
            if "CTRL" in mods and (match.group(1) == "A" or "SHIFT" in mods):
                return True
    return False


def window_tool(work):
    source = work / "windows.swift"
    binary = work / "windows"
    if not binary.exists():
        source.write_text(WINDOW_TOOL)
        built = run(["swiftc", "-O", str(source), "-o", str(binary)])
        if built.returncode:
            raise SystemExit("swiftc failed: " + built.stderr[-400:])
    return binary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True, help="Workbench state to display")
    parser.add_argument("--executable", type=Path, required=True, help="release awb launcher")
    parser.add_argument("--config-source", type=Path, default=Path.home() / ".wezterm.lua")
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sizes", default="80x24,120x36,160x48")
    args = parser.parse_args()
    if sys.platform != "darwin" or not Path(WEZTERM).exists():
        raise SystemExit("This capture needs macOS and /Applications/WezTerm.app")
    work, out = args.work.resolve(), args.output.resolve()
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    tool = window_tool(work)
    config = work / "wezterm.lua"
    if args.config_source.exists():
        shutil.copy2(args.config_source, config)
    installed = run([str(args.executable), "--home", str(args.home), "terminal", "install",
                     "--config-file", str(config), "--executable", str(args.executable), "--status"])
    if installed.returncode:
        raise SystemExit("terminal install failed: " + (installed.stdout + installed.stderr)[-600:])
    shown = run([WEZTERM, "--config-file", str(config), "show-keys", "--lua"])
    report = {
        "schema": 1,
        "started": datetime.now(timezone.utc).isoformat(),
        "wezterm": run([WEZTERM, "--version"]).stdout.strip(),
        "executable": str(args.executable),
        "awb_version": run([str(args.executable), "--version"]).stdout.strip(),
        "config_sha256_after_install": sha256(config),
        "shortcut_bound": shortcut_bound(shown.stdout),
        "sizes": [],
    }
    for size in args.sizes.split(","):
        cols, rows = (int(v) for v in size.split("x"))
        font = None
        for attempt in range(4):
            entry = capture_size(args, config, work, out, tool, size, cols, rows, font)
            if entry.get("pane", {}).get("rows", rows) >= rows or attempt == 3:
                break
            # The display cannot fit the rows at this font; shrink it a little
            # and record the size actually used.
            font = round((font or entry["font_size"]) * 0.9, 1)
        report["sizes"].append(entry)
    report["finished"] = datetime.now(timezone.utc).isoformat()
    (out / "wezterm-capture.json").write_text(json.dumps(report, indent=1))
    print(json.dumps({k: v for k, v in report.items() if k != "sizes"}, indent=1))
    for entry in report["sizes"]:
        print(entry["size"], entry.get("pane"), [v["screenshot"] for v in entry["views"]][:2])


def capture_size(args, config, work, out, tool, size, cols, rows, font):
    wrapper = work / ("size-" + size + ".lua")
    wrapper.write_text(
        "local config = dofile(" + json.dumps(str(config)) + ")\n"
        f"config.initial_cols = {cols}\nconfig.initial_rows = {rows}\n"
        + (f"config.font_size = {font}\n" if font else "")
        + "return config\n"
    )
    gui = subprocess.Popen(
        [WEZTERM, "--config-file", str(wrapper), "start", "--always-new-process",
         "--class", "awb-capture-" + size, "--", str(args.executable), "--home", str(args.home), "tui"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    socket = Path.home() / ".local/share/wezterm" / ("gui-sock-" + str(gui.pid))
    env = {**os.environ, "WEZTERM_UNIX_SOCKET": str(socket)}
    entry = {"size": size, "gui_pid": gui.pid, "font_size": font, "views": []}
    try:
        listed = None
        for _ in range(40):
            if socket.exists():
                listed = run([WEZTERM, "cli", "list", "--format", "json"], env=env)
                if listed.returncode == 0 and json.loads(listed.stdout or "[]"):
                    break
            time.sleep(0.5)
        pane = json.loads(listed.stdout)[0]
        entry["pane"] = pane["size"]
        if font is None:
            # The operator's configured size, or WezTerm's default of 12.
            match = re.search(r"font_size\s*=\s*([0-9.]+)", config.read_text())
            entry["font_size"] = float(match.group(1)) if match else 12.0

        def text():
            return run([WEZTERM, "cli", "get-text", "--pane-id", str(pane["pane_id"])], env=env).stdout

        for _ in range(40):  # Wait until the TUI has drawn its tabs.
            if "Outcomes" in text() and "Inbox" in text():
                break
            time.sleep(0.5)
        time.sleep(2)

        def send(keys):
            run([WEZTERM, "cli", "send-text", "--pane-id", str(pane["pane_id"]), "--no-paste", keys], env=env)
            time.sleep(0.8)

        def capture(view):
            text_file = out / f"{size}-{view}.txt"
            text_file.write_text(text())
            item = {"view": view, "text": text_file.name, "text_sha256": sha256(text_file)}
            locked = run([str(tool), "locked"]).stdout.strip() == "1"
            windows = run([str(tool), "windows", str(gui.pid)]).stdout.split()
            if locked or not windows:
                item["screenshot"] = "not captured: " + ("screen locked" if locked else "no window")
            else:
                png = out / f"{size}-{view}.png"
                shot = run(["screencapture", "-x", "-o", "-l", windows[0], str(png)])
                item["screenshot"] = png.name if shot.returncode == 0 else "not captured: " + shot.stderr.strip()
                if png.exists():
                    item["screenshot_sha256"] = sha256(png)
            entry["views"].append(item)

        # Focus the prompt, then the tab strip; arrows switch views.
        send(KEYS["compose"])
        send(KEYS["tab"])
        capture("inbox")
        for view in TABS[1:]:
            send(KEYS["right"])
            capture(view)
        send(KEYS["f1"])
        capture("help")
        send(KEYS["escape"])
    finally:
        gui.terminate()
        try:
            gui.wait(10)
        except subprocess.TimeoutExpired:
            gui.kill()
    return entry


if __name__ == "__main__":
    main()

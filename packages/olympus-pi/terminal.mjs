#!/usr/bin/env node
import { Dashboard } from "./view.mjs";
import { client } from "./client.mjs";

if (!process.stdin.isTTY || !process.stdout.isTTY) {
  try { const view = new Dashboard(client()); await view.refresh(); console.log(view.render(144, 40).join("\n")); if (view.error) process.exitCode = 1; }
  catch (e) { console.error(e.message); process.exitCode = 1; }
} else {
  let closed = false;
  const view = new Dashboard(client(), { reducedMotion: process.env.OLYMPUS_REDUCED_MOTION === "1", repaint, close });
  function repaint() {
    if (closed) return;
    const lines = view.render(process.stdout.columns || 144, process.stdout.rows || 40);
    process.stdout.write("\x1b[H" + lines.map((l, i) => {
      const color = i === 0 ? "\x1b[1;36m" : l.startsWith("STALE") || /BLOCKED|RECOVERING/.test(l) ? "\x1b[33m" : l.startsWith(">") ? "\x1b[36m" : "\x1b[37m";
      return "\x1b[2K" + color + l + "\x1b[0m";
    }).join("\r\n"));
  }
  function close() { if (closed) return; closed = true; view.dispose(); process.stdin.setRawMode(false); process.stdin.pause(); process.stdout.write("\x1b[?25h\x1b[?1049l"); }
  process.stdout.write("\x1b[?1049h\x1b[2J\x1b[?25l"); process.stdin.setRawMode(true); process.stdin.resume();
  process.stdin.on("data", (data) => void view.key(data.toString("utf8")));
  process.stdout.on("resize", repaint); process.on("SIGINT", close); process.on("SIGTERM", close); process.on("exit", close);
  await view.start();
}

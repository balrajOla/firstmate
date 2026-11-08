"""Resumable live soak against a running Workbench service.

scripts/soak.py measures durable intake only. This runner drives an
installed Workbench whose bridge and coordinator already run as services.
Each cycle it:

* submits one small real request to a live project through the installed CLI;
* submits a burst of synthetic requests to a paused project, checks every
  acknowledged prompt was saved, then cancels them;
* samples bridge, observation and coordinator health, queue depth, storage
  and the latency of earlier live requests.

It appends one JSON line per cycle and keeps a state file, so a restarted
runner continues the same soak. The summary says RUNNING until the requested
real elapsed time has passed. Time the runner was not running is reported as
gaps and never counted as soaked time.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import statistics
import subprocess
import sys
import time
import urllib.request
import uuid

MAX_HOURS = 72
# Small mixed live work: a code change, a report and a simple answer.
LIVE_REQUESTS = [
    "Append the line 'soak cycle {n}' to SOAK.md, creating the file if needed, and commit it.",
    "Write a three-sentence status note for soak cycle {n} that says what a soak run checks.",
    "Answer in one sentence: what is {n} multiplied by 7?",
]


def write_json(path, value):
    tmp = path.with_name(path.name + ".tmp")
    descriptor = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, indent=1)
    os.replace(tmp, path)


def awb(command, home, *args):
    result = subprocess.run(
        [*command, "--home", str(home), *args], capture_output=True, text=True, timeout=120
    )
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip()[-300:])
    return json.loads(result.stdout)


def query(home, sql, args=()):
    uri = (Path(home) / "workbench.sqlite").resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=10)) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute(sql, args)]


def health_file(home, name, now):
    path = Path(home) / name
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"status": "missing"}
    return {"status": value.get("status"), "age_seconds": round(now - value.get("at", 0), 1)}


def coordinator_health(url):
    if not url:
        return {"status": "not_checked"}
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return {"status": json.loads(response.read()).get("status"), "http": response.status}
    except Exception as exc:  # Any failure is an observation, not a crash.
        return {"status": "unreachable", "error": str(exc)[:200]}


def storage(home):
    files = {}
    for path in Path(home).iterdir():
        if path.is_file() and (path.name.startswith("workbench.sqlite") or path.suffix == ".log"):
            files[path.name] = path.stat().st_size
    return {"bytes": sum(files.values()), "files": files}


def live_latencies(home, request_ids):
    """Seconds from submission to the first proposed outcome, per finished request."""
    if not request_ids:
        return {}
    marks = ",".join("?" * len(request_ids))
    rows = query(
        home,
        "SELECT r.id, r.created, r.status, (SELECT MIN(o.created) FROM outcomes o "
        f"WHERE o.request_id=r.id) AS outcome FROM requests r WHERE r.id IN ({marks})",
        request_ids,
    )
    return {r["id"]: {"status": r["status"],
                      "latency_seconds": round(r["outcome"] - r["created"], 1) if r["outcome"] else None}
            for r in rows}


def run_cycle(state, command, home, live_project, intake_project, burst, health_url):
    n = state["cycles"] + 1
    started = time.time()
    record = {"cycle": n, "started": started}
    try:
        text = LIVE_REQUESTS[(n - 1) % len(LIVE_REQUESTS)].format(n=n)
        submitted = awb(command, home, "submit", text, "--project", live_project,
                        "--key", state["run_id"] + "-live-" + str(n))
        state["live"].append(submitted["request_id"])
        record["live_request"] = submitted["request_id"]
    except Exception as exc:
        record["live_error"] = str(exc)
    keys, timings, intake_errors = [], [], []
    for i in range(burst):
        key = state["run_id"] + "-intake-" + str(n) + "-" + str(i)
        began = time.monotonic()
        try:
            request = awb(command, home, "submit", "Soak intake " + str(n) + "." + str(i),
                          "--project", intake_project, "--key", key)["request_id"]
            timings.append(time.monotonic() - began)
            keys.append(key)
            awb(command, home, "request", json.dumps(
                {"action": "request.cancel", "args": {"request_id": request}}))
        except Exception as exc:
            intake_errors.append(str(exc))
    saved = 0
    if keys:
        marks = ",".join("?" * len(keys))
        saved = query(home, f"SELECT COUNT(*) AS n FROM prompts WHERE client_key IN ({marks})", keys)[0]["n"]
    record["intake"] = {
        "acknowledged": len(keys), "saved": saved, "lost": len(keys) - saved, "errors": intake_errors[:3],
        "error_count": len(intake_errors),
        "submit_p50_seconds": round(statistics.median(timings), 3) if timings else None,
        "submit_max_seconds": round(max(timings), 3) if timings else None,
    }
    now = time.time()
    record["health"] = {
        "bridge": health_file(home, "bridge-health.json", now),
        "observation": health_file(home, "observation-health.json", now),
        "coordinator": coordinator_health(health_url),
    }
    record["outbox"] = {r["status"]: r["n"] for r in query(
        home, "SELECT status, COUNT(*) AS n FROM outbox GROUP BY status")}
    record["storage"] = storage(home)
    record["live"] = live_latencies(home, state["live"])
    record["finished"] = time.time()
    return record


def summarize(state, records, hours):
    live = records[-1]["live"] if records else {}
    statuses = [v["status"] for v in live.values()]
    latencies = [v["latency_seconds"] for v in live.values() if v["latency_seconds"] is not None]
    soaked = state["soaked_seconds"]
    return {
        "run_id": state["run_id"],
        "status": "FINISHED" if soaked >= hours * 3600 else "RUNNING",
        "target_hours": hours,
        "soaked_hours": round(soaked / 3600, 3),
        "soaked_seconds": round(soaked, 1),
        "gaps": state["gaps"],
        "cycles": state["cycles"],
        "live": {
            "submitted": len(state["live"]),
            "with_outcome": len(latencies),
            "by_status": {s: statuses.count(s) for s in sorted(set(statuses))},
            "latency_p50_seconds": round(statistics.median(latencies), 1) if latencies else None,
            "latency_max_seconds": max(latencies) if latencies else None,
        },
        "intake_lost": sum(r["intake"]["lost"] for r in records),
        "intake_errors": sum(r["intake"]["error_count"] for r in records),
        "unhealthy_cycles": sum(
            1 for r in records
            if r["health"]["bridge"]["status"] != "running"
            or r["health"]["observation"]["status"] != "observed"
            or r["health"]["coordinator"]["status"] not in ("ok", "not_checked")
        ),
        "storage_bytes": {"first": records[0]["storage"]["bytes"], "last": records[-1]["storage"]["bytes"]}
        if records else None,
        "notice": "Live mixed workload on one machine. RUNNING until real elapsed soak time reaches the target.",
    }


def soak(command, home, output, live_project, intake_project, hours, interval_seconds,
         burst=20, health_url=None, max_cycles=None):
    if not 0 < hours <= MAX_HOURS:
        raise SystemExit("Use a duration above zero and at most 72 hours")
    paused = query(
        home, "SELECT p.id FROM projects p JOIN project_policy pp ON pp.project_id=p.id "
        "WHERE pp.paused=1 AND (p.id=? OR p.name=?)", (intake_project, intake_project))
    if not paused:
        # Synthetic intake must never be dispatched to real workers.
        raise SystemExit("Pause the intake project before soaking")
    output.mkdir(parents=True, exist_ok=True)
    state_path, cycles_path = output / "state.json", output / "cycles.jsonl"
    if state_path.exists():
        state = json.loads(state_path.read_text())
    else:
        state = {"run_id": "soak-" + uuid.uuid4().hex[:8], "started": time.time(), "cycles": 0,
                 "soaked_seconds": 0.0, "last_cycle_end": None, "gaps": [], "live": []}
    records = [json.loads(line) for line in cycles_path.read_text().splitlines()] if cycles_path.exists() else []
    done = 0
    while state["soaked_seconds"] < hours * 3600 and (max_cycles is None or done < max_cycles):
        began = time.time()
        last = state["last_cycle_end"]
        # Time between cycles counts only when the runner kept its schedule.
        if last is not None:
            elapsed = began - last
            if elapsed > 2 * interval_seconds + 60:
                state["gaps"].append({"from": last, "to": began, "seconds": round(elapsed, 1)})
            else:
                state["soaked_seconds"] += elapsed
        record = run_cycle(state, command, home, live_project, intake_project, burst, health_url)
        state["soaked_seconds"] += record["finished"] - began
        state["cycles"] += 1
        state["last_cycle_end"] = record["finished"]
        records.append(record)
        with open(cycles_path, "a") as stream:
            stream.write(json.dumps(record) + "\n")
        write_json(state_path, state)
        write_json(output / "summary.json", summarize(state, records, hours))
        done += 1
        if state["soaked_seconds"] < hours * 3600 and (max_cycles is None or done < max_cycles):
            time.sleep(max(0.0, interval_seconds - (time.time() - began)))
    summary = summarize(state, records, hours)
    write_json(output / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--awb", required=True, help="installed awb launcher")
    parser.add_argument("--home", type=Path, required=True, help="state served by the running bridge")
    parser.add_argument("--live-project", required=True)
    parser.add_argument("--intake-project", required=True, help="a paused project for synthetic intake")
    parser.add_argument("--hours", type=float, default=MAX_HOURS)
    parser.add_argument("--interval-minutes", type=float, default=60)
    parser.add_argument("--intake-burst", type=int, default=20)
    parser.add_argument("--health-url", help="coordinator health URL, for example Paperclip's /api/health")
    parser.add_argument("--max-cycles", type=int, help="stop after this many cycles in this invocation")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = soak([args.awb], args.home, args.output, args.live_project, args.intake_project,
                   args.hours, args.interval_minutes * 60, args.intake_burst, args.health_url,
                   args.max_cycles)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    sys.exit(main())

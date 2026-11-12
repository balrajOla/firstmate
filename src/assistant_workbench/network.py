"""Connectivity guard: while the agents' providers are unreachable nothing is
routed or dispatched, and each outage is recorded so tasks whose runs failed
during it are retried once the providers answer again. Running workers are
left alone: their CLIs retry short interruptions themselves. An outage costs
time, not work."""

from __future__ import annotations

import json
import socket
import time
from .util import WorkbenchError, now

# Where each harness's CLI connects. Machines that reach a provider through a
# gateway set network.probe_hosts instead.
DEFAULT_HOSTS = {"claude": ["api.anthropic.com:443"], "codex": ["api.openai.com:443", "chatgpt.com:443"]}
KEY = "network"
OUTAGES = "network-outages"


def network_settings(settings):
    spec = settings.get("network") or {}
    if not isinstance(spec, dict) or set(spec) - {"enabled", "probe_hosts", "interval_seconds"}:
        raise WorkbenchError("network accepts enabled, probe_hosts and interval_seconds")
    enabled = spec.get("enabled", True)
    if not isinstance(enabled, bool):
        raise WorkbenchError("network.enabled must be true or false")
    hosts = spec.get("probe_hosts")
    if hosts is not None and (not isinstance(hosts, list) or not all(
            isinstance(h, str) and h.count(":") == 1 and h.rsplit(":", 1)[1].isdigit() for h in hosts)):
        raise WorkbenchError("network.probe_hosts must list host:port entries")
    interval = spec.get("interval_seconds", 10)
    if not isinstance(interval, int) or isinstance(interval, bool) or not 2 <= interval <= 600:
        raise WorkbenchError("network.interval_seconds must be from 2 to 600")
    if hosts is None:
        hosts = sorted({h for m in settings.get("models", []) for h in DEFAULT_HOSTS.get(m.get("harness"), [])})
    return {"enabled": enabled, "hosts": hosts, "interval": interval}


def reachable(hosts, timeout=3):
    """Whether any host accepts a TCP connection, which needs working DNS and routing."""
    for target in hosts:
        host, port = target.rsplit(":", 1)
        try:
            socket.create_connection((host, int(port)), timeout=timeout).close()
            return True
        except OSError:
            continue
    return False


class Guard:
    """Online until two probes in a row fail; online again on the first success."""

    def __init__(self, store, settings, check=None, clock=None):
        self.store, self.spec = store, network_settings(settings)
        self.check, self.clock = check or reachable, clock or time.time

    def state(self):
        rows = self.store.rows("SELECT value FROM meta WHERE key=?", (KEY,))
        return json.loads(rows[0]["value"]) if rows else {"status": "online", "since": None, "failures": 0}

    def save(self, state):
        with self.store.transaction() as db:
            db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (KEY, json.dumps(state)))

    def update(self):
        state = self.state()
        if not self.spec["enabled"] or not self.spec["hosts"]:
            if state["status"] != "online":
                state.update(status="online", since=now(), failures=0)
                self.save(state)
            return state
        if self.clock() - state.get("checked", 0) < self.spec["interval"]:
            return state
        ok = self.check(self.spec["hosts"])
        state["checked"] = self.clock()
        previous = state["status"]
        if ok:
            state.update(failures=0, status="online")
        else:
            state["failures"] = state.get("failures", 0) + 1
            if state["failures"] >= 2:
                state["status"] = "offline"
        if state["status"] != previous:
            started = state.get("since")
            state["since"] = now()
            with self.store.transaction() as db:
                self.store.event(db, "network." + state["status"], KEY, {"hosts": self.spec["hosts"]})
                if state["status"] == "online" and started:
                    windows = outages(self.store) + [[started, state["since"]]]
                    db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                               (OUTAGES, json.dumps(windows[-50:])))
        self.save(state)
        return state

    def mark(self, paused):
        state = self.state()
        state["paused"] = paused
        self.save(state)


def outages(store):
    """Recorded outages as [start, end] pairs, oldest first."""
    rows = store.rows("SELECT value FROM meta WHERE key=?", (OUTAGES,))
    return json.loads(rows[0]["value"]) if rows else []

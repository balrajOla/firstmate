"""Runtime selection and durable multi-host admission; never a second scheduler.

The registry is a control-plane ledger. A registered host is not proof that a
worker is running, and an expired lease never authorizes duplicate execution.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from pathlib import PurePosixPath, PureWindowsPath

from .util import WorkbenchError, canonical, digest, now, uid

SCHEMA = """
CREATE TABLE IF NOT EXISTS runtime_hosts(
 id TEXT PRIMARY KEY, name TEXT NOT NULL, task_queue TEXT NOT NULL UNIQUE,
 capacity INTEGER NOT NULL, interactive_reserve INTEGER NOT NULL,
 capabilities TEXT NOT NULL, workspaces TEXT NOT NULL,
 token_hash TEXT NOT NULL, heartbeat REAL, enabled INTEGER NOT NULL DEFAULT 1,
 created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS runtime_leases(
 id TEXT PRIMARY KEY, host_id TEXT NOT NULL REFERENCES runtime_hosts(id),
 request_id TEXT NOT NULL REFERENCES requests(id), revision INTEGER NOT NULL,
 task_key TEXT NOT NULL, generation INTEGER NOT NULL,
 token_hash TEXT NOT NULL, state TEXT NOT NULL, expires REAL NOT NULL,
 run_id TEXT, receipt TEXT, created REAL NOT NULL,
 UNIQUE(request_id,revision,task_key,generation)
);
CREATE TABLE IF NOT EXISTS temporal_executions(
 request_id TEXT NOT NULL REFERENCES requests(id), revision INTEGER NOT NULL,
 workflow_id TEXT NOT NULL UNIQUE, run_id TEXT, payload_hash TEXT NOT NULL,
 state TEXT NOT NULL, result TEXT, created REAL NOT NULL, updated REAL NOT NULL,
 PRIMARY KEY(request_id,revision)
);
"""

ACTIVE_LEASES = ("leased", "running", "cancel_requested", "uncertain")
USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "reasoning_output_tokens",
)
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


def initialize_runtime(store):
    db = store.connect()
    try:
        db.executescript(SCHEMA)
    finally:
        db.close()


def select_runtime(store, settings, expected=None):
    """Fence a backend change while old work may still execute.

    Both runtime entry points must call this before submitting work. It is a local
    single-controller guard, not a distributed consensus protocol.
    """
    initialize_runtime(store)
    selected = settings.get("coordinator", {}).get("kind")
    if selected not in {"paperclip", "temporal"}:
        raise WorkbenchError("Select exactly one coordinator: paperclip or temporal")
    if expected and selected != expected:
        raise WorkbenchError(
            f"{expected} is inactive; selected coordinator is {selected}"
        )
    with store.transaction() as db:
        previous = db.execute(
            "SELECT value FROM meta WHERE key='runtime:selected'"
        ).fetchone()
        if previous and previous[0] != selected:
            active = db.execute(
                "SELECT 1 FROM requests WHERE status='running' LIMIT 1"
            ).fetchone()
            lease = db.execute(
                "SELECT 1 FROM runtime_leases WHERE state IN ('leased','running','cancel_requested','uncertain') LIMIT 1"
            ).fetchone()
            delivery = db.execute(
                "SELECT 1 FROM outbox WHERE status IN ('delivering','uncertain') LIMIT 1"
            ).fetchone()
            temporal = db.execute(
                "SELECT 1 FROM temporal_executions WHERE state IN ('starting','running','cancel_requested','uncertain') LIMIT 1"
            ).fetchone()
            if active or lease or delivery or temporal:
                raise WorkbenchError(
                    "Reconcile and stop active work before changing the coordinator"
                )
        db.execute(
            "INSERT INTO meta VALUES('runtime:selected',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (selected,),
        )
    return selected


class Fleet:
    """Registered workers, project mappings, capacity and fenced leases.

    Only the central controller writes this SQLite ledger. Remote hosts use the
    authenticated operator interface; do not put SQLite on a shared network volume.
    """

    def __init__(self, store):
        self.store = store
        initialize_runtime(store)

    def register(
        self,
        host_id,
        name,
        task_queue,
        capacity,
        capabilities,
        workspaces,
        interactive_reserve=0,
    ):
        if (
            not isinstance(host_id, str)
            or not isinstance(task_queue, str)
            or not ID.fullmatch(host_id)
            or not ID.fullmatch(task_queue)
        ):
            raise WorkbenchError(
                "Host and queue IDs need 1–128 letters, digits, dots, dashes or underscores"
            )
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(capacity, int)
            or isinstance(capacity, bool)
            or not 1 <= capacity <= 1024
        ):
            raise WorkbenchError("A host needs a name and capacity between 1 and 1024")
        if (
            not isinstance(interactive_reserve, int)
            or isinstance(interactive_reserve, bool)
            or not 0 <= interactive_reserve < capacity
        ):
            raise WorkbenchError("Interactive reserve must be less than host capacity")
        if (
            not isinstance(capabilities, list)
            or not capabilities
            or any(not isinstance(x, str) or not ID.fullmatch(x) for x in capabilities)
        ):
            raise WorkbenchError("Declare nonempty worker capability IDs")
        if not isinstance(workspaces, dict) or not workspaces:
            raise WorkbenchError("Declare explicit project-to-workspace mappings")
        mapped = {}
        for project, path in workspaces.items():
            pid = self.store.project(project)["id"]
            if not isinstance(path, str) or "\x00" in path:
                raise WorkbenchError(
                    "Workspace paths must be absolute paths on the worker"
                )
            parsed = (
                PureWindowsPath(path)
                if PureWindowsPath(path).is_absolute()
                else PurePosixPath(path)
            )
            if (
                not parsed.is_absolute()
                or ".." in parsed.parts
                or str(parsed) == parsed.anchor
            ):
                raise WorkbenchError(
                    "Workspace paths must be scoped absolute directories"
                )
            mapped[pid] = path
        token = uid("host_")
        with self.store.transaction() as db:
            if db.execute(
                "SELECT 1 FROM runtime_hosts WHERE id=?", (host_id,)
            ).fetchone():
                raise WorkbenchError(
                    "Host already exists; explicitly drain it before replacing registration"
                )
            if db.execute(
                "SELECT 1 FROM runtime_hosts WHERE task_queue=?", (task_queue,)
            ).fetchone():
                raise WorkbenchError("Each host requires a distinct task queue")
            db.execute(
                "INSERT INTO runtime_hosts VALUES(?,?,?,?,?,?,?,?,NULL,1,?)",
                (
                    host_id,
                    name,
                    task_queue,
                    capacity,
                    interactive_reserve,
                    canonical(sorted(set(capabilities))),
                    canonical(mapped),
                    digest(token),
                    now(),
                ),
            )
            self.store.event(
                db,
                "fleet.registered",
                host_id,
                {
                    "task_queue": task_queue,
                    "capacity": capacity,
                    "projects": sorted(mapped),
                },
            )
        return {
            "host_id": host_id,
            "token": token,
            "notice": "Store the host token privately. Registration is not a live-worker observation.",
        }

    def _host(self, db, host_id, token):
        host = db.execute(
            "SELECT * FROM runtime_hosts WHERE id=?", (host_id,)
        ).fetchone()
        if (
            not host
            or not token
            or not hmac.compare_digest(host["token_hash"], digest(token))
        ):
            raise WorkbenchError("Invalid host credential")
        return host

    def heartbeat(self, host_id, token):
        with self.store.transaction() as db:
            host = self._host(db, host_id, token)
            if not host["enabled"]:
                raise WorkbenchError("Host is disabled")
            db.execute(
                "UPDATE runtime_hosts SET heartbeat=? WHERE id=?", (now(), host_id)
            )
        return {
            "host_id": host_id,
            "heartbeat": now(),
            "notice": "Host liveness only; model/authentication must be probed separately.",
        }

    def disable(self, host_id):
        with self.store.transaction() as db:
            count = db.execute(
                "UPDATE runtime_hosts SET enabled=0 WHERE id=?", (host_id,)
            ).rowcount
            if not count:
                raise WorkbenchError("Host not registered")
            self.store.event(db, "fleet.disabled", host_id, {})
        return {
            "host_id": host_id,
            "enabled": False,
            "notice": "New admissions stop; existing jobs require cancellation and stop receipts.",
        }

    def status(self):
        self.expire()
        hosts = self.store.rows(
            "SELECT id,name,task_queue,capacity,interactive_reserve,capabilities,workspaces,heartbeat,enabled FROM runtime_hosts ORDER BY id"
        )
        for host in hosts:
            host["capabilities"] = json.loads(host["capabilities"])
            host["workspaces"] = json.loads(host["workspaces"])
            host["alive"] = bool(
                host["heartbeat"] and 0 <= now() - host["heartbeat"] <= 60
            )
            host["occupied"] = self.store.one(
                "SELECT COUNT(*) AS n FROM runtime_leases WHERE host_id=? AND state IN ('leased','running','cancel_requested','uncertain')",
                (host["id"],),
            )["n"]
        leases = self.store.rows(
            "SELECT id,host_id,request_id,revision,task_key,generation,state,expires,run_id,receipt FROM runtime_leases ORDER BY created"
        )
        return {
            "hosts": hosts,
            "leases": leases,
            "notice": "Expired leases remain uncertain and occupy capacity until reconciled.",
        }

    def expire(self):
        with self.store.transaction() as db:
            rows = db.execute(
                "SELECT id FROM runtime_leases WHERE state IN ('leased','running','cancel_requested') AND expires<?",
                (now(),),
            ).fetchall()
            for row in rows:
                db.execute(
                    "UPDATE runtime_leases SET state='uncertain' WHERE id=?",
                    (row["id"],),
                )
                self.store.event(
                    db,
                    "fleet.lease_uncertain",
                    row["id"],
                    {"reason": "Lease expired; process termination is not established"},
                )
        return {"uncertain": [r["id"] for r in rows]}

    def admit(
        self,
        host_id,
        token,
        request_id,
        revision,
        task_key,
        capability,
        interactive=False,
        lease_seconds=60,
        generation=1,
    ):
        if (
            not isinstance(task_key, str)
            or not ID.fullmatch(task_key)
            or not isinstance(lease_seconds, int)
            or isinstance(lease_seconds, bool)
            or not 10 <= lease_seconds <= 300
            or not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation < 1
        ):
            raise WorkbenchError(
                "Use a bounded 10–300 second lease, task ID and positive generation"
            )
        self.expire()
        with self.store.transaction() as db:
            host = self._host(db, host_id, token)
            request = db.execute(
                "SELECT * FROM requests WHERE id=?", (request_id,)
            ).fetchone()
            if (
                not request
                or request["revision"] != revision
                or request["status"] in {"cancelled", "accepted", "grouped"}
            ):
                raise WorkbenchError("Lease targets a closed or stale request")
            if (
                not host["enabled"]
                or not host["heartbeat"]
                or not 0 <= now() - host["heartbeat"] <= 60
            ):
                raise WorkbenchError("Host has no recent heartbeat or is disabled")
            if request["project_id"] not in json.loads(host["workspaces"]):
                raise WorkbenchError("Project has no workspace mapping on this host")
            if capability not in json.loads(host["capabilities"]):
                raise WorkbenchError("Host does not declare the required capability")
            prior = db.execute(
                "SELECT * FROM runtime_leases WHERE request_id=? AND revision=? AND task_key=? ORDER BY generation DESC LIMIT 1",
                (request_id, revision, task_key),
            ).fetchone()
            if prior and prior["generation"] == generation:
                if prior["host_id"] != host_id or prior["state"] not in {
                    "leased",
                    "running",
                }:
                    raise WorkbenchError(
                        "Existing task ownership must be reconciled before admission"
                    )
                return self._receipt(
                    prior, token, json.loads(host["workspaces"])[request["project_id"]]
                )
            if generation != (prior["generation"] + 1 if prior else 1) or (
                prior and prior["state"] in ACTIVE_LEASES
            ):
                raise WorkbenchError(
                    "Previous generation is not stopped or generation is out of sequence"
                )
            occupied = db.execute(
                "SELECT COUNT(*) FROM runtime_leases WHERE host_id=? AND state IN ('leased','running','cancel_requested','uncertain')",
                (host_id,),
            ).fetchone()[0]
            available = host["capacity"] - (
                0 if interactive else host["interactive_reserve"]
            )
            if occupied >= available:
                raise WorkbenchError(
                    "Host capacity is full; reserved interactive slots are preserved"
                )
            ident = uid("lease_")
            lease_token = hmac.new(
                token.encode(), ident.encode(), hashlib.sha256
            ).hexdigest()
            db.execute(
                "INSERT INTO runtime_leases VALUES(?,?,?,?,?,?,?,'leased',?,NULL,NULL,?)",
                (
                    ident,
                    host_id,
                    request_id,
                    revision,
                    task_key,
                    generation,
                    digest(lease_token),
                    now() + lease_seconds,
                    now(),
                ),
            )
            self.store.event(
                db,
                "fleet.admitted",
                request_id,
                {
                    "host_id": host_id,
                    "lease_id": ident,
                    "task_key": task_key,
                    "generation": generation,
                },
            )
            lease = db.execute(
                "SELECT * FROM runtime_leases WHERE id=?", (ident,)
            ).fetchone()
            return self._receipt(
                lease, token, json.loads(host["workspaces"])[request["project_id"]]
            )

    @staticmethod
    def _receipt(lease, host_token, workspace):
        return {
            "lease_id": lease["id"],
            "host_id": lease["host_id"],
            "generation": lease["generation"],
            "state": lease["state"],
            "expires": lease["expires"],
            "workspace": workspace,
            "lease_token": hmac.new(
                host_token.encode(), lease["id"].encode(), hashlib.sha256
            ).hexdigest(),
        }

    def renew(self, lease_id, lease_token, run_id, lease_seconds=60):
        if (
            not isinstance(run_id, str)
            or not run_id.strip()
            or not isinstance(lease_seconds, int)
            or isinstance(lease_seconds, bool)
            or not 10 <= lease_seconds <= 300
        ):
            raise WorkbenchError("Renewal requires a run identity and bounded lease")
        self.expire()
        with self.store.transaction() as db:
            lease = self._lease(db, lease_id, lease_token)
            request = db.execute(
                "SELECT revision,status FROM requests WHERE id=?",
                (lease["request_id"],),
            ).fetchone()
            if (
                lease["state"] not in {"leased", "running"}
                or request["revision"] != lease["revision"]
                or request["status"] in {"cancelled", "accepted", "grouped"}
            ):
                raise WorkbenchError("Lease is fenced, cancelled, expired or uncertain")
            if lease["run_id"] and lease["run_id"] != run_id:
                raise WorkbenchError("Run identity cannot change within a lease")
            db.execute(
                "UPDATE runtime_leases SET state='running',run_id=?,expires=? WHERE id=?",
                (run_id, now() + lease_seconds, lease_id),
            )
        return {"lease_id": lease_id, "state": "running"}

    @staticmethod
    def _lease(db, lease_id, lease_token):
        row = db.execute(
            "SELECT * FROM runtime_leases WHERE id=?", (lease_id,)
        ).fetchone()
        if (
            not row
            or not lease_token
            or not hmac.compare_digest(row["token_hash"], digest(lease_token))
        ):
            raise WorkbenchError("Invalid lease credential")
        return row

    def cancel(self, lease_id):
        with self.store.transaction() as db:
            lease = db.execute(
                "SELECT * FROM runtime_leases WHERE id=?", (lease_id,)
            ).fetchone()
            if not lease:
                raise WorkbenchError("Lease not found")
            if lease["state"] in {"leased", "running"}:
                db.execute(
                    "UPDATE runtime_leases SET state='cancel_requested' WHERE id=?",
                    (lease_id,),
                )
            self.store.event(db, "fleet.cancel_requested", lease_id, {})
        return {
            "lease_id": lease_id,
            "notice": "Cancellation requested; capacity is retained until the worker reports process stop.",
        }

    def release(self, lease_id, lease_token, state, stop_receipt):
        if state not in {"completed", "cancelled", "failed"} or not isinstance(
            stop_receipt, dict
        ):
            raise WorkbenchError(
                "Release requires a terminal state and a process-stop receipt"
            )
        with self.store.transaction() as db:
            lease = self._lease(db, lease_id, lease_token)
            if (
                stop_receipt.get("stopped") is not True
                or stop_receipt.get("run_id") != lease["run_id"]
            ):
                raise WorkbenchError(
                    "Stop receipt must identify this lease's process and confirm termination"
                )
            if not lease["run_id"] and stop_receipt.get("never_started") is not True:
                raise WorkbenchError(
                    "An unstarted lease needs an explicit never-started receipt"
                )
            if lease["state"] not in ACTIVE_LEASES:
                if (
                    lease["state"] == state
                    and json.loads(lease["receipt"]) == stop_receipt
                ):
                    return {"lease_id": lease_id, "state": state, "duplicate": True}
                raise WorkbenchError("Lease already has a different terminal receipt")
            db.execute(
                "UPDATE runtime_leases SET state=?,receipt=? WHERE id=?",
                (state, canonical(stop_receipt), lease_id),
            )
            self.store.event(
                db,
                "fleet.released",
                lease_id,
                {"state": state, "run_id": lease["run_id"]},
            )
        return {
            "lease_id": lease_id,
            "state": state,
            "notice": "A worker-reported stop receipt is not independent host attestation.",
        }

    def worker_request(self, host_id, token, action, args=None):
        """Narrow host-authenticated interface for a tunneled/HTTPS worker client.

        Host credentials never grant operator actions or access to another host's
        leases, projects or full prompt contents. The service owns this ledger.
        """
        args = {} if args is None else args
        if not isinstance(args, dict) or action not in {
            "heartbeat",
            "status",
            "request_state",
            "admit",
            "renew",
            "release",
        }:
            raise WorkbenchError("Unsupported worker operation")
        with self.store.transaction() as db:
            host = dict(self._host(db, host_id, token))
            if action in {"renew", "release"}:
                lease = db.execute(
                    "SELECT host_id FROM runtime_leases WHERE id=?",
                    (args.get("lease_id"),),
                ).fetchone()
                if not lease or lease["host_id"] != host_id:
                    raise WorkbenchError("Lease is outside this worker's scope")
            if action == "request_state":
                request = db.execute(
                    "SELECT id,project_id,revision,status FROM requests WHERE id=?",
                    (args.get("request_id"),),
                ).fetchone()
                if not request or request["project_id"] not in json.loads(
                    host["workspaces"]
                ):
                    raise WorkbenchError(
                        "Request is outside this worker's project scope"
                    )
                result = dict(request)
                execution = db.execute(
                    "SELECT payload_hash FROM temporal_executions WHERE request_id=? AND revision=?",
                    (request["id"], request["revision"]),
                ).fetchone()
                result["payload_hash"] = (
                    execution["payload_hash"] if execution else None
                )
                return result
        if action == "heartbeat":
            if args:
                raise WorkbenchError("Heartbeat takes no extra arguments")
            return self.heartbeat(host_id, token)
        if action == "status":
            if args:
                raise WorkbenchError("Status takes no extra arguments")
            return next(h for h in self.status()["hosts"] if h["id"] == host_id)
        if action == "admit":
            if set(args) - {
                "request_id",
                "revision",
                "task_key",
                "capability",
                "lease_seconds",
                "generation",
            }:
                raise WorkbenchError(
                    "Worker admission cannot change host or reserve policy"
                )
            return self.admit(host_id, token, **args)
        if action == "renew":
            if set(args) - {"lease_id", "lease_token", "run_id", "lease_seconds"}:
                raise WorkbenchError("Unknown renewal arguments")
            return self.renew(**args)
        if set(args) - {"lease_id", "lease_token", "state", "stop_receipt"}:
            raise WorkbenchError("Unknown release arguments")
        return self.release(**args)

    def usage(self, project=None, request_id=None, revision=None):
        return usage_report(self.store, project, request_id, revision)


def usage_report(store, project=None, request_id=None, revision=None):
    """Read reported counters once per request/revision/node/generation.

    Completed workflow results and central process-stop receipts overlap. Their
    shared identity is counted once; conflicting counters are unknown. Records
    from historical revisions still consumed resources and remain in the total.
    No logs, prompt content, credentials or estimated prices are returned.
    """
    if revision is not None and (
        not isinstance(revision, int) or isinstance(revision, bool) or revision < 1
    ):
        raise WorkbenchError("Usage revision must be a positive integer")
    project_id = store.project(project)["id"] if project else None
    tables = {
        r["name"]
        for r in store.rows("SELECT name FROM sqlite_master WHERE type='table'")
    }
    requests = {
        r["id"]: r for r in store.rows("SELECT id,project_id,revision FROM requests")
    }
    records = {}
    execution_count, executions_without_results, malformed_executions = 0, 0, 0

    def selected(rid, rev):
        req = requests.get(rid)
        return bool(
            req
            and (not project_id or req["project_id"] == project_id)
            and (not request_id or rid == request_id)
            and (revision is None or rev == revision)
        )

    def counters(usage):
        values = {field: None for field in USAGE_FIELDS}
        malformed = False
        if usage is None:
            return values, False
        if (
            not isinstance(usage, dict)
            or type(usage.get("schema")) is not int
            or usage.get("schema") != 1
            or usage.get("source") not in {"codex.turn.completed", "claude.result"}
            or not isinstance(usage.get("tokens"), dict)
        ):
            return values, True
        for field in USAGE_FIELDS:
            value = usage["tokens"].get(field)
            if (
                isinstance(value, int)
                and not isinstance(value, bool)
                and 0 <= value <= 2**63 - 1
            ):
                values[field] = value
            elif value is not None:
                malformed = True
        return values, malformed or bool(usage.get("malformed_fields"))

    def add(rid, rev, node, generation, usage, origin, fixture=False):
        if (
            not isinstance(node, str)
            or not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation < 1
        ):
            return False
        key = (rid, rev, node, generation)
        values, malformed = counters(usage)
        previous = records.get(key)
        if previous:
            previous["origins"].add(origin)
            previous["fixture"] = previous["fixture"] or fixture
            previous["malformed"] = previous["malformed"] or malformed
            for field, value in values.items():
                old = previous["tokens"][field]
                if field in previous["conflicts"]:
                    continue
                if old is not None and value is not None and old != value:
                    previous["tokens"][field] = None
                    previous["conflicts"].add(field)
                elif old is None:
                    previous["tokens"][field] = value
        else:
            records[key] = {
                "request_id": rid,
                "revision": rev,
                "node": node,
                "generation": generation,
                "project_id": requests[rid]["project_id"],
                "tokens": values,
                "malformed": malformed,
                "conflicts": set(),
                "fixture": fixture,
                "origins": {origin},
            }
        return True

    if "runtime_leases" in tables:
        for lease in store.rows(
            "SELECT request_id,revision,task_key,generation,receipt FROM runtime_leases"
        ):
            if not selected(lease["request_id"], lease["revision"]):
                continue
            try:
                receipt = json.loads(lease["receipt"]) if lease["receipt"] else {}
            except (TypeError, ValueError):
                receipt = {"usage": "malformed receipt"}
            if not isinstance(receipt, dict):
                receipt = {"usage": "malformed receipt"}
            add(
                lease["request_id"],
                lease["revision"],
                lease["task_key"],
                lease["generation"],
                receipt.get("usage"),
                "lease",
                receipt.get("fixture_only") is True,
            )
    if "temporal_executions" in tables:
        for execution in store.rows(
            "SELECT request_id,revision,result FROM temporal_executions"
        ):
            if not selected(execution["request_id"], execution["revision"]):
                continue
            execution_count += 1
            try:
                result = json.loads(execution["result"]) if execution["result"] else {}
            except (TypeError, ValueError):
                result = None
            if not isinstance(result, dict) or not isinstance(
                result.get("nodes", {}), dict
            ):
                malformed_executions += 1
                executions_without_results += 1
                continue
            nodes = result.get("nodes", {})
            if not nodes:
                executions_without_results += 1
            for node, value in nodes.items():
                if not isinstance(value, dict):
                    value = {"usage": "malformed node"}
                generation = value.get("generation", 1)
                if not add(
                    execution["request_id"],
                    execution["revision"],
                    node,
                    generation,
                    value.get("usage"),
                    "workflow",
                    value.get("fixture_only") is True,
                ):
                    malformed_executions += 1

    counted = [r for r in records.values() if not r["fixture"]]
    metrics = {}
    for field in USAGE_FIELDS:
        observed = [
            r["tokens"][field] for r in counted if r["tokens"][field] is not None
        ]
        metrics[field] = {
            "reported_total": sum(observed) if observed else None,
            "known_records": len(observed),
            "unknown_records": len(counted) - len(observed),
            "complete_for_recorded_nodes": bool(counted)
            and len(observed) == len(counted),
        }
    complete = sum(
        r["tokens"]["input_tokens"] is not None
        and r["tokens"]["output_tokens"] is not None
        for r in counted
    )
    unknown = sum(all(value is None for value in r["tokens"].values()) for r in counted)
    generations = {}
    for key in records:
        generations.setdefault(key[:3], set()).add(key[3])
    missing_generations = sum(
        max(values) - len(values) for values in generations.values()
    )
    hosts = (
        store.rows("SELECT id,capacity,interactive_reserve,enabled FROM runtime_hosts")
        if "runtime_hosts" in tables
        else []
    )
    occupied = (
        store.one(
            "SELECT COUNT(*) AS count FROM runtime_leases WHERE state IN ('leased','running','cancel_requested','uncertain')"
        )["count"]
        if "runtime_leases" in tables
        else 0
    )
    per_revision = []
    revision_groups = {}
    for record in counted:
        revision_groups.setdefault(
            (record["request_id"], record["revision"]), []
        ).append(record)
    for (rid, rev), group in sorted(revision_groups.items()):
        totals = {}
        for field in USAGE_FIELDS:
            values = [
                r["tokens"][field] for r in group if r["tokens"][field] is not None
            ]
            totals[field] = sum(values) if values else None
        per_revision.append(
            {
                "request_id": rid,
                "revision": rev,
                "current_revision": requests[rid]["revision"] == rev,
                "project_id": requests[rid]["project_id"],
                "records": len(group),
                "reported_totals": totals,
            }
        )
    return {
        "schema": 1,
        "at": now(),
        "scope": "Observed CLI counters in fleet stop receipts and stored Temporal results; all request revisions unless filtered",
        "totals": {field: value["reported_total"] for field, value in metrics.items()},
        "metrics": metrics,
        "coverage": {
            "recorded_nodes": len(counted),
            "input_output_complete": complete,
            "partial": len(counted) - complete - unknown,
            "unknown": unknown,
            "malformed": sum(r["malformed"] for r in counted),
            "conflicting": sum(bool(r["conflicts"]) for r in counted),
            "excluded_fixture_nodes": sum(r["fixture"] for r in records.values()),
            "executions": execution_count,
            "executions_without_node_results": executions_without_results,
            "malformed_executions": malformed_executions,
            "unrepresented_prior_generations": missing_generations,
        },
        "revisions": per_revision,
        "capacity": {
            "hosts": len(hosts),
            "configured_slots": sum(h["capacity"] for h in hosts),
            "enabled_slots": sum(h["capacity"] for h in hosts if h["enabled"]),
            "interactive_reserve": sum(h["interactive_reserve"] for h in hosts),
            "occupied_slots": occupied,
        },
        "cost_usd": None,
        "subscription_remaining": None,
        "cpu_percent": None,
        "memory_bytes": None,
        "notice": "Totals cover reported counters only. Missing counters are unknown, not zero. Cache and reasoning are breakdowns, not additions to input/output. No bill, subscription allowance, CPU or memory measurement is inferred.",
    }


class WorkerControl:
    """Worker side of the host-scoped control API; no shared SQLite required."""

    def __init__(self, host_id, token, *, store=None, endpoint=None):
        import urllib.parse

        if (store is None) == (endpoint is None):
            raise WorkbenchError(
                "Select one local store or remote worker control endpoint"
            )
        self.host_id, self.token, self.store, self.endpoint = (
            host_id,
            token,
            store,
            endpoint,
        )
        if endpoint:
            parsed = urllib.parse.urlsplit(endpoint)
            if (
                parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.path not in {"", "/"}
            ):
                raise WorkbenchError(
                    "Control endpoint must be an origin without credentials"
                )
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise WorkbenchError("Control endpoint requires HTTP(S)")
            if parsed.scheme != "https" and parsed.hostname not in {
                "127.0.0.1",
                "localhost",
                "::1",
            }:
                raise WorkbenchError(
                    "Remote control requires HTTPS or an explicit loopback tunnel"
                )

    async def call(self, action, **args):
        import asyncio

        if self.store is not None:
            return Fleet(self.store).worker_request(
                self.host_id, self.token, action, args
            )
        return await asyncio.to_thread(self._remote, action, args)

    def _remote(self, action, args):
        import urllib.error
        import urllib.request

        body = canonical(
            {"host_id": self.host_id, "action": action, "args": args}
        ).encode()
        if len(body) > 256 * 1024:
            raise WorkbenchError("Worker control request exceeds 256 KiB")
        request = urllib.request.Request(
            self.endpoint.rstrip("/") + "/worker",
            data=body,
            headers={
                "Authorization": "Bearer " + self.token,
                "Content-Type": "application/json",
            },
            method="POST",
        )

        # Reject redirects so a control token cannot follow a server-supplied URL.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        try:
            with urllib.request.build_opener(NoRedirect).open(
                request, timeout=10
            ) as response:
                data = response.read(256 * 1024 + 1)
            if len(data) > 256 * 1024:
                raise WorkbenchError("Worker control response exceeds 256 KiB")
            value = json.loads(data)
            if not isinstance(value, dict) or "result" not in value:
                raise WorkbenchError("Unexpected worker control response")
            return value["result"]
        except (urllib.error.URLError, ValueError, TimeoutError) as exc:
            raise WorkbenchError(
                "Worker control unavailable or rejected the scoped operation; do not launch replacement work"
            ) from exc

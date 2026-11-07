from __future__ import annotations

import json
from .util import WorkbenchError, canonical, digest, now, uid


class Continuation:
    def __init__(self, store, limits):
        self.store = store
        self.limits = limits

    def checkpoint(self, request_id, revision, generation, summary, artifacts, pending):
        from pathlib import Path
        from .context import Context

        req = self.store.one("SELECT * FROM requests WHERE id=?", (request_id,))
        for artifact in artifacts:
            if artifact.get("source_id"):
                source = Context(self.store).source(
                    req["project_id"], artifact["source_id"]
                )
                if artifact.get("sha256") != source["hash"]:
                    raise WorkbenchError("Checkpoint source hash mismatch")
            elif artifact.get("path"):
                repo = self.store.project(req["project_id"])["repository"]
                path = Path(artifact["path"]).resolve(strict=True)
                if (
                    not repo
                    or not path.is_relative_to(Path(repo))
                    or not path.is_file()
                    or digest(path.read_bytes()) != artifact.get("sha256")
                ):
                    raise WorkbenchError(
                        "Checkpoint artifact is outside project scope or its hash changed"
                    )
            else:
                raise WorkbenchError(
                    "Checkpoint artifacts require a source_id or scoped path and sha256"
                )
        with self.store.transaction() as db:
            req = db.execute(
                "SELECT * FROM requests WHERE id=?", (request_id,)
            ).fetchone()
            if (
                not req
                or req["revision"] != revision
                or req["status"] in {"cancelled", "accepted"}
            ):
                raise WorkbenchError("Checkpoint targets a closed or changed request")
            previous = list(
                db.execute(
                    "SELECT * FROM checkpoints WHERE request_id=? ORDER BY generation DESC LIMIT 3",
                    (request_id,),
                )
            )
            expected = previous[0]["generation"] + 1 if previous else 1
            if generation != expected:
                raise WorkbenchError("Worker generation is stale or out of sequence")
            if (
                generation > self.limits["max_iterations"]
                or now() - req["created"] > self.limits["max_seconds"]
            ):
                raise WorkbenchError("Continuation budget exhausted")
            unresolved = db.execute(
                "SELECT id FROM effects WHERE request_id=? AND state='uncertain'",
                (request_id,),
            ).fetchall()
            if unresolved:
                raise WorkbenchError("Reconcile uncertain effects before continuing")
            # Progress is measured from immutable artifact content, not wording of a summary.
            progress = digest(canonical(artifacts))
            if len(previous) >= 2 and all(
                row["progress_hash"] == progress for row in previous[:2]
            ):
                raise WorkbenchError("No artifact progress across three checkpoints")
            payload = {
                "summary": summary,
                "artifacts": artifacts,
                "pending": pending,
                "requirements_hash": digest(req["body"]),
                "generation": generation,
                "instruction": "Reload current requirements and capability revisions; old worker generation is fenced.",
            }
            ident = uid("cp_")
            db.execute(
                "INSERT INTO checkpoints VALUES(?,?,?,?,?,?,?,?)",
                (
                    ident,
                    request_id,
                    revision,
                    generation,
                    generation,
                    progress,
                    canonical(payload),
                    now(),
                ),
            )
            self.store.event(
                db,
                "checkpoint.committed",
                request_id,
                {"id": ident, "generation": generation},
            )
        return {"id": ident, **payload}

    def recover(self, request_id):
        req = self.store.one("SELECT * FROM requests WHERE id=?", (request_id,))
        cp = self.store.one(
            "SELECT * FROM checkpoints WHERE request_id=? ORDER BY generation DESC LIMIT 1",
            (request_id,),
        )
        payload = json.loads(cp["payload"])
        if (
            req["status"] in {"cancelled", "accepted"}
            or cp["revision"] != req["revision"]
            or payload["requirements_hash"] != digest(req["body"])
        ):
            raise WorkbenchError("Checkpoint no longer matches active requirements")
        return {
            "request": req,
            "checkpoint": payload,
            "next_generation": cp["generation"] + 1,
            "iterations_used": cp["iteration"],
            "started_at": req["created"],
        }

    def effect(self, request_id, generation, key, kind, receipt=None):
        with self.store.transaction() as db:
            req = db.execute(
                "SELECT * FROM requests WHERE id=?", (request_id,)
            ).fetchone()
            cp = (
                db.execute(
                    "SELECT MAX(generation) FROM checkpoints WHERE request_id=?",
                    (request_id,),
                ).fetchone()[0]
                or 0
            )
            if (
                not req
                or req["status"] in {"cancelled", "accepted"}
                or generation != cp + 1
            ):
                raise WorkbenchError("Stale worker cannot record effects")
            old = db.execute("SELECT * FROM effects WHERE id=?", (key,)).fetchone()
            if old:
                if old["request_id"] != request_id or old["kind"] != kind:
                    raise WorkbenchError("Effect key conflict")
                if receipt is not None:
                    db.execute(
                        "UPDATE effects SET state='confirmed',receipt=? WHERE id=?",
                        (canonical(receipt), key),
                    )
            else:
                db.execute(
                    "INSERT INTO effects VALUES(?,?,?,?,?,?,?)",
                    (
                        key,
                        request_id,
                        generation,
                        kind,
                        "confirmed" if receipt is not None else "uncertain",
                        canonical(receipt) if receipt is not None else None,
                        now(),
                    ),
                )
        return self.store.one("SELECT * FROM effects WHERE id=?", (key,))

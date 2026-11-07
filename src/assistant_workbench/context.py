from __future__ import annotations

import json
import mimetypes
import re
from pathlib import Path
from .store import Store
from .util import WorkbenchError, atomic, digest, now, uid


class Context:
    def __init__(self, store: Store):
        self.store = store

    def ingest(self, project, path, authority="source", text=None):
        pid = self.store.project(project)["id"]
        path = Path(path).expanduser().resolve(strict=True)
        if not path.is_file() or path.stat().st_size > 100 * 1024 * 1024:
            raise WorkbenchError("Source must be a regular file no larger than 100 MiB")
        data = path.read_bytes()
        sha = digest(data)
        if text is None:
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                raise WorkbenchError(
                    "Binary source needs extraction; use the Docling integration"
                )
        if authority not in {"source", "user", "decision", "derived", "inference"}:
            raise WorkbenchError("Unknown source authority")
        archived = self.store.home / "archive" / sha[:2] / sha
        if not archived.exists():
            atomic(archived, data)
        elif digest(archived.read_bytes()) != sha:
            raise WorkbenchError("Archive corruption detected")
        with self.store.transaction() as db:
            old = db.execute(
                "SELECT * FROM sources WHERE project_id=? AND locator=? ORDER BY created DESC LIMIT 1",
                (pid, str(path)),
            ).fetchone()
            if old and old["hash"] == sha and not old["revoked"]:
                old_text = db.execute(
                    "SELECT text FROM sources_text WHERE id=?", (old["id"],)
                ).fetchone()
                old_check = db.execute(
                    "SELECT value FROM meta WHERE key=?", ("source-text:" + old["id"],)
                ).fetchone()
                if (
                    old_text
                    and old_check
                    and digest(old_text[0]) == old_check[0] == digest(text)
                ):
                    return dict(old)
            ident = uid("s_")
            media = mimetypes.guess_type(path)[0] or "application/octet-stream"
            db.execute(
                "INSERT INTO sources VALUES(?,?,?,?,?,?,0,?,?)",
                (
                    ident,
                    pid,
                    str(path),
                    sha,
                    media,
                    authority,
                    old["id"] if old else None,
                    now(),
                ),
            )
            db.execute("INSERT INTO sources_text VALUES(?,?)", (ident, text))
            db.execute(
                "INSERT INTO meta VALUES(?,?)", ("source-text:" + ident, digest(text))
            )
            db.execute("INSERT INTO source_search VALUES(?,?,?)", (ident, pid, text))
            db.execute(
                "INSERT INTO coverage VALUES(?,?,'extracted',?,NULL) ON CONFLICT(project_id,locator) DO UPDATE SET state='extracted',source_id=excluded.source_id,error=NULL",
                (pid, str(path), ident),
            )
            self.store.event(
                db,
                "source.ingested",
                ident,
                {"project": pid, "hash": sha, "supersedes": old["id"] if old else None},
            )
        return self.store.one("SELECT * FROM sources WHERE id=?", (ident,))

    def inventory(self, project, root):
        pid = self.store.project(project)["id"]
        root = Path(root).resolve(strict=True)
        if not root.is_dir():
            raise WorkbenchError("Inventory root must be a directory")
        excluded = {".git", "node_modules", ".venv", "__pycache__"}
        count = 0
        import os

        with self.store.transaction() as db:
            for directory, dirs, files in os.walk(root, followlinks=False):
                dirs[:] = sorted(
                    d
                    for d in dirs
                    if d not in excluded and not (Path(directory) / d).is_symlink()
                )
                for name in sorted(files):
                    path = Path(directory) / name
                    if path.is_symlink():
                        continue
                    db.execute(
                        "INSERT OR IGNORE INTO coverage VALUES(?,?,'discovered',NULL,NULL)",
                        (pid, str(path)),
                    )
                    count += 1
            self.store.event(
                db,
                "corpus.inventoried",
                pid,
                {
                    "root": str(root),
                    "files": count,
                    "excluded_directories": sorted(excluded),
                    "symlinks": "excluded",
                },
            )
        return {
            "files": count,
            "excluded_directories": sorted(excluded),
            "symlinks": "excluded",
        }

    def mark(self, project, locator, state, error=None):
        if state not in {"reviewed", "excluded", "failed"}:
            raise WorkbenchError("Invalid coverage disposition")
        pid = self.store.project(project)["id"]
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT * FROM coverage WHERE project_id=? AND locator=?",
                (pid, locator),
            ).fetchone()
            if not row:
                raise WorkbenchError("Source was not inventoried")
            if state == "reviewed" and not row["source_id"]:
                raise WorkbenchError("Cannot review a source before extraction")
            if state in {"excluded", "failed"} and not error:
                raise WorkbenchError("Record the exclusion or failure reason")
            db.execute(
                "UPDATE coverage SET state=?,error=? WHERE project_id=? AND locator=?",
                (state, error, pid, locator),
            )

    def coverage(self, project):
        pid = self.store.project(project)["id"]
        rows = self.store.rows("SELECT * FROM coverage WHERE project_id=?", (pid,))
        counts = {
            s: sum(r["state"] == s for r in rows)
            for s in ["discovered", "extracted", "reviewed", "excluded", "failed"]
        }
        return {
            "project_id": pid,
            "total": len(rows),
            "counts": counts,
            "complete": bool(rows)
            and counts["discovered"] == 0
            and counts["extracted"] == 0
            and counts["failed"] == 0,
            "items": rows,
        }

    def source(self, project, source_id):
        pid = self.store.project(project)["id"]
        src = self.store.one(
            "SELECT s.*,t.text FROM sources s JOIN sources_text t ON s.id=t.id WHERE s.id=?",
            (source_id,),
        )
        if src["revoked"] or not self.store.can_read(pid, src["project_id"]):
            raise WorkbenchError("Source is unavailable in this project scope")
        blob = self.store.home / "archive" / src["hash"][:2] / src["hash"]
        if not blob.exists() or digest(blob.read_bytes()) != src["hash"]:
            raise WorkbenchError("Original source integrity check failed")
        check = self.store.rows(
            "SELECT value FROM meta WHERE key=?", ("source-text:" + source_id,)
        )
        if not check or check[0]["value"] != digest(src["text"]):
            raise WorkbenchError(
                "Extracted text integrity is unverified or changed; re-extract and review the source"
            )
        return src

    def search(self, project, query, limit=8, include_superseded=False):
        pid = self.store.project(project)["id"]
        terms = re.findall(r"[\w-]+", query, flags=re.UNICODE)
        if not terms:
            return []
        fts = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms[:32])
        ids = [
            pid,
            *[
                r["target_id"]
                for r in self.store.rows(
                    "SELECT target_id FROM grants WHERE project_id=?", (pid,)
                )
            ],
        ]
        q = (
            "SELECT s.*,snippet(source_search,2,'[',']',' … ',35) AS excerpt,bm25(source_search) AS rank FROM source_search JOIN sources s ON source_search.id=s.id WHERE source_search MATCH ? AND s.revoked=0 AND s.project_id IN ("
            + ",".join("?" for _ in ids)
            + ")"
        )
        if not include_superseded:
            q += " AND NOT EXISTS (SELECT 1 FROM sources newer WHERE newer.supersedes=s.id AND newer.revoked=0)"
        q += " ORDER BY rank,s.created DESC LIMIT ?"
        return self.store.rows(q, (fts, *ids, max(1, min(int(limit), 100))))

    def packet(self, project, query, max_chars=24000):
        if max_chars < 100 or max_chars > 1_000_000:
            raise WorkbenchError(
                "Context character budget must be between 100 and 1,000,000"
            )
        hits = self.search(project, query, limit=30)
        items = []
        used = 0
        omitted = []
        for hit in hits:
            source = self.source(project, hit["id"])
            # Preserve complete selected passages; avoid silently truncating a qualification.
            paragraphs = source["text"].split("\n\n")
            terms = set(re.findall(r"\w+", query.lower()))
            selected = sorted(
                enumerate(paragraphs),
                key=lambda pair: -len(terms & set(re.findall(r"\w+", pair[1].lower()))),
            )
            passages = []
            for idx, paragraph in selected:
                cost = len(paragraph) + len(source["id"]) + 100
                if used + cost > max_chars:
                    continue
                passages.append({"paragraph": idx + 1, "text": paragraph})
                used += cost
                if len(passages) >= 3:
                    break
            if passages:
                items.append(
                    {
                        "id": source["id"],
                        "hash": source["hash"],
                        "authority": source["authority"],
                        "locator": source["locator"],
                        "passages": passages,
                    }
                )
            else:
                omitted.append(source["id"])
        packet = {
            "project": project,
            "budget_chars": max_chars,
            "used_chars": used,
            "sources": items,
            "omitted": omitted,
            "coverage": "targeted retrieval; not proof of exhaustive corpus understanding",
        }
        return packet

    def history(self, project, query, limit=20):
        pid = self.store.project(project)["id"]
        return self.store.rows(
            "SELECT p.id,p.request_id,p.body,p.created,r.revision,r.status FROM prompts p JOIN requests r ON r.id=p.request_id WHERE r.project_id=? AND instr(lower(p.body),lower(?))>0 ORDER BY p.created DESC LIMIT ?",
            (pid, query, max(1, min(int(limit), 100))),
        )

    def assertion(self, project, source_id, subject, value, effective_date):
        pid = self.store.project(project)["id"]
        self.source(pid, source_id)
        if not subject.strip() or not value.strip() or not effective_date:
            raise WorkbenchError("Assertions require subject, value and effective date")
        ident = uid("a_")
        with self.store.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS assertions(id TEXT PRIMARY KEY,project_id TEXT,source_id TEXT,subject TEXT,value TEXT,effective_date TEXT)"
            )
            conflicts = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM assertions WHERE project_id=? AND subject=? AND effective_date=? AND value!=?",
                    (pid, subject, effective_date, value),
                )
            ]
            db.execute(
                "INSERT INTO assertions VALUES(?,?,?,?,?,?)",
                (ident, pid, source_id, subject, value, effective_date),
            )
            self.store.event(
                db,
                "assertion.recorded",
                ident,
                {"conflicts": [r["id"] for r in conflicts]},
            )
        return {
            "id": ident,
            "conflicts": conflicts,
            "status": "needs_reconciliation" if conflicts else "recorded",
            "notice": "Recorded assertions are not automatically established facts.",
        }

    def purge(self, project, source_id):
        self.revoke(project, source_id)
        with self.store.transaction() as db:
            src = db.execute(
                "SELECT * FROM sources WHERE id=?", (source_id,)
            ).fetchone()
            db.execute("DELETE FROM sources_text WHERE id=?", (source_id,))
            db.execute(
                "UPDATE sources SET locator=? WHERE id=?",
                ("purged:" + source_id, source_id),
            )
            db.execute(
                "UPDATE coverage SET locator=?,state='excluded',error='Source purged' WHERE source_id=?",
                ("purged:" + source_id, source_id),
            )
            db.execute("DELETE FROM source_search WHERE id=?", (source_id,))
            shared = db.execute(
                "SELECT 1 FROM sources s JOIN sources_text t ON t.id=s.id WHERE s.hash=?",
                (src["hash"],),
            ).fetchone()
            self.store.event(
                db,
                "source.purged",
                source_id,
                {
                    "external_indexes": "manual purge/verification required",
                    "backups": "apply configured retention separately",
                },
            )
        if not shared:
            (self.store.home / "archive" / src["hash"][:2] / src["hash"]).unlink(
                missing_ok=True
            )
        return {
            "id": source_id,
            "active_archive_deleted": not bool(shared),
            "notice": "Previously exported reports, external integration stores and recovery backups need separate deletion verification.",
        }

    def revoke(self, project, source_id):
        pid = self.store.project(project)["id"]
        with self.store.transaction() as db:
            count = db.execute(
                "UPDATE sources SET revoked=1 WHERE id=? AND project_id=?",
                (source_id, pid),
            ).rowcount
            if not count:
                raise WorkbenchError("Source is not owned by this project")
            db.execute("DELETE FROM source_search WHERE id=?", (source_id,))
            for outcome in db.execute(
                "SELECT * FROM outcomes WHERE status!='superseded'"
            ).fetchall():
                if any(
                    c.get("source_id") == source_id
                    for c in json.loads(outcome["claims"])
                ):
                    db.execute(
                        "UPDATE outcomes SET status='source_revoked' WHERE id=?",
                        (outcome["id"],),
                    )
                    db.execute(
                        "UPDATE requests SET status='needs_review' WHERE id=? AND status='accepted'",
                        (outcome["request_id"],),
                    )
            self.store.event(db, "source.revoked", source_id, {"project": pid})

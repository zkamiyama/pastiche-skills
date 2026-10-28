"""Versioned claims, local source selectors, human reviews, and reusable tasks.

Source passages are read on demand from registered local files, not stored in
claims. The database and its exports are PRIVATE: names and short descriptions
are intentionally present. Never commit a user's database or source files.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any

MAX_SOURCE_BYTES = 16_000_000


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def uid(prefix: str, *values: Any) -> str:
    return prefix + "-" + hashlib.sha256(dump(values).encode()).hexdigest()[:24]


def text(value: Any, limit: int = 400, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise ValueError("invalid text field")
    if any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise ValueError("control characters are not allowed")
    return value.strip()


def position(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("position must be null or a finite number in [0,1]")
    return float(value)


def read_source(path: Path) -> tuple[str, str]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("source is missing, symlinked, or too large")
    raw = path.read_bytes()
    if len(raw) > MAX_SOURCE_BYTES:
        raise ValueError("source is too large")
    for encoding in ("utf-8-sig", "cp932"):
        try:
            return raw.decode(encoding).replace("\r\n", "\n").replace("\r", "\n"), hashlib.sha256(raw).hexdigest()
        except UnicodeError:
            pass
    raise ValueError("source must be UTF-8 or CP932")


def segments(body: str, maximum: int = 500) -> list[dict[str, Any]]:
    """Stable offsets in decoded, newline-normalized text; not semantic scenes."""
    result = []
    for line in re.finditer(r"[^\n]+", body):
        start, stop = line.span()
        while start < stop:
            end = min(stop, start + maximum)
            if end < stop:
                cuts = list(re.finditer(r"[。！？!?]", body[start:end]))
                if cuts:
                    end = start + cuts[-1].end()
            a, b = start, end
            while a < b and body[a].isspace():
                a += 1
            while b > a and body[b - 1].isspace():
                b -= 1
            if a < b:
                result.append({"id": f"S{len(result)+1:06d}", "start": a, "end": b, "text": body[a:b]})
            start = end
    return result


def chunked(units: list[dict[str, Any]], limit: int) -> list[list[dict[str, Any]]]:
    if limit < 800:
        raise ValueError("chunk limit must be >=800")
    batches, batch, size = [], [], 0
    for unit in units:
        needed = len(unit["text"]) + len(unit["id"]) + 5
        if batch and size + needed > limit:
            batches.append(batch)
            batch, size = [], 0
        batch.append(unit)
        size += needed
    if batch:
        batches.append(batch)
    return batches


class Conflict(ValueError):
    """Optimistic-lock conflict: refresh before retrying a human edit."""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY, source TEXT NOT NULL REFERENCES sources(id), body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS entities(id TEXT PRIMARY KEY, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS claims(id TEXT PRIMARY KEY, body TEXT NOT NULL, override TEXT,
          status TEXT NOT NULL DEFAULT 'auto_accepted', revision INTEGER NOT NULL DEFAULT 1, updated TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reviews(seq INTEGER PRIMARY KEY AUTOINCREMENT, claim_id TEXT NOT NULL,
          action TEXT NOT NULL, reviewer TEXT NOT NULL, note TEXT NOT NULL, before_json TEXT NOT NULL,
          after_json TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS merges(source TEXT PRIMARY KEY REFERENCES entities(id), target TEXT NOT NULL REFERENCES entities(id));
        CREATE TABLE IF NOT EXISTS merge_history(seq INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tasks(key TEXT PRIMARY KEY, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY, kind TEXT NOT NULL, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS layout(id TEXT PRIMARY KEY, x REAL NOT NULL, y REAL NOT NULL);
        """)
        # Existing databases may contain untouched claims created before
        # opt-out review became the default. Convert ONLY untouched legacy
        # rows: explicit human decisions (including defer/correct) stay intact.
        self.db.execute("""
            UPDATE claims SET status='auto_accepted'
            WHERE status='pending'
              AND NOT EXISTS (SELECT 1 FROM reviews WHERE reviews.claim_id=claims.id)
        """)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def transaction(self):
        try:
            self.db.execute("BEGIN IMMEDIATE")
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def get(self, table: str, key: str) -> Any:
        if table not in {"sources", "evidence", "entities", "tasks", "artifacts"}:
            raise ValueError("unknown table")
        col = "key" if table == "tasks" else "id"
        row = self.db.execute(f"SELECT body FROM {table} WHERE {col}=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def rows(self, table: str) -> list[dict[str, Any]]:
        if table not in {"sources", "evidence", "entities", "artifacts"}:
            raise ValueError("unknown table")
        return [json.loads(r[0]) for r in self.db.execute(f"SELECT body FROM {table} ORDER BY id")]

    def task_put(self, key: str, body: Any) -> None:
        self.db.execute("INSERT OR REPLACE INTO tasks VALUES (?,?)", (key, dump(body)))
        self.db.commit()

    def artifact(self, key: str, kind: str, body: Any) -> None:
        self.db.execute("INSERT OR REPLACE INTO artifacts VALUES (?,?,?)", (key, kind, dump(body)))
        self.db.commit()

    def register_source(self, path: Path, scope: str, *, uri: str | None = None,
                        source_type: str = "corpus", label: str | None = None) -> tuple[dict, str]:
        if path.is_symlink():
            raise ValueError("symlink source not allowed")
        path = path.resolve()
        body, sha = read_source(path)
        locator = uri or path.as_uri()
        scope = text(scope, 160)
        sid = uid("SRC", locator, scope)
        source = {"id": sid, "path": str(path), "uri": locator, "label": label or path.name,
                  "scope": scope, "sha256": sha, "source_type": source_type, "accessed_at": now()}
        self.db.execute("INSERT OR REPLACE INTO sources VALUES (?,?)", (sid, dump(source)))
        self.db.commit()
        return source, body

    def evidence_for(self, source: dict, units: list[dict], ids: list[str], length: int) -> str:
        selected = [u for u in units if u["id"] in ids]
        if not ids or len(set(ids)) != len(ids) or len(selected) != len(ids):
            raise ValueError("unknown or duplicate source segment")
        evidence = {"source_id": source["id"], "source_hash": source["sha256"],
                    "selectors": [{"segment": u["id"], "start": u["start"], "end": u["end"]} for u in selected],
                    "position": selected[0]["start"] / max(1, length)}
        eid = uid("Q", evidence)
        evidence["id"] = eid
        self.db.execute("INSERT OR IGNORE INTO evidence VALUES (?,?,?)", (eid, source["id"], dump(evidence)))
        self.db.commit()
        return eid

    def resolve_evidence(self, eid: str, context: int = 240) -> dict:
        evidence = self.get("evidence", eid)
        if not evidence:
            raise ValueError("unknown evidence")
        source = self.get("sources", evidence["source_id"])
        body, sha = read_source(Path(source["path"]))
        if sha != evidence["source_hash"]:
            raise Conflict("source changed; this selector must be re-extracted and reviewed")
        lo = max(0, min(x["start"] for x in evidence["selectors"]) - context)
        hi = min(len(body), max(x["end"] for x in evidence["selectors"]) + context)
        return {"evidence_id": eid, "source": {k: source[k] for k in
                 ("id", "label", "uri", "scope", "sha256", "source_type", "accessed_at")},
                "text": body[lo:hi], "offset": lo, "coordinate_unit": "unicode_code_points", "selectors": evidence["selectors"]}

    def entity(self, namespace: str, name: str, scope: str, aliases: list[str] | None = None) -> str:
        name, scope = text(name, 100), text(scope, 160)
        eid = uid("C", namespace, name, scope)
        old = self.get("entities", eid)
        aliases = sorted({text(a, 100) for a in (aliases or [])} | set((old or {}).get("aliases", [])))
        value = {"id": eid, "name": name, "namespace": namespace, "scope": scope, "aliases": aliases}
        self.db.execute("INSERT OR REPLACE INTO entities VALUES (?,?)", (eid, dump(value)))
        self.db.commit()
        return eid

    def _validate_claim(self, body: dict) -> dict:
        result = {"subject": text(body.get("subject"), 100), "predicate": text(body.get("predicate"), 80),
                  "object": body.get("object") or None, "value": text(body.get("value"), 400),
                  "kind": text(body.get("kind", "attribute"), 40),
                  "epistemic": body.get("epistemic", "inferred"), "scope": text(body.get("scope"), 160),
                  "observed_at": position(body.get("observed_at")),
                  "valid_from": position(body.get("valid_from")), "valid_to": position(body.get("valid_to")),
                  "evidence_ids": body.get("evidence_ids"), "model_checked": body.get("model_checked") is True}
        if result["epistemic"] not in {"explicit", "inferred", "reported"}:
            raise ValueError("unknown epistemic status")
        if result["kind"] not in {"appearance", "voice", "value", "goal", "belief", "knowledge", "relationship", "attribute"}:
            raise ValueError("unknown claim kind")
        for k in ("subject", "object"):
            if result[k] is not None:
                ent = self.get("entities", result[k])
                if not ent or ent["scope"] != result["scope"]:
                    raise ValueError("unknown or cross-scope character")
        ids = result["evidence_ids"]
        if not isinstance(ids, list) or not ids or not all(isinstance(e, str) and self.get("evidence", e) for e in ids):
            raise ValueError("claims require registered evidence")
        if any(self.get("sources", self.get("evidence", e)["source_id"])["scope"] != result["scope"] for e in ids):
            raise ValueError("cross-scope evidence")
        result["evidence_ids"] = sorted(set(ids))
        a, b = result["valid_from"], result["valid_to"]
        if (a is None) != (b is None) or (a is not None and a > b):
            raise ValueError("validity interval requires ordered endpoints")
        return result

    def add_claim(self, body: dict) -> str:
        value = self._validate_claim(body)
        cid = uid("K", value)
        self.db.execute("INSERT OR IGNORE INTO claims(id,body,updated) VALUES (?,?,?)", (cid, dump(value), now()))
        self.db.commit()
        return cid

    def claim(self, cid: str) -> dict:
        r = self.db.execute("SELECT * FROM claims WHERE id=?", (cid,)).fetchone()
        if not r:
            raise ValueError("unknown claim")
        base = json.loads(r["body"])
        effective = json.loads(r["override"]) if r["override"] else base
        return {"id": cid, **effective, "original": base, "status": r["status"],
                "revision": r["revision"], "updated": r["updated"]}

    def review(self, cid: str, action: str, revision: int, reviewer: str, note: str,
               patch: dict | None = None) -> dict:
        if action not in {"approve", "reject", "defer", "correct", "revoke"} or type(revision) is not int:
            raise ValueError("invalid review action or revision")
        reviewer, note = text(reviewer, 80), text(note, 2000, empty=True)
        with self.transaction():
            current = self.claim(cid)
            if current["revision"] != revision:
                raise Conflict("claim changed; reload before reviewing")
            effective = {k: current[k] for k in current["original"]}
            if action == "correct":
                allowed = {"subject", "object", "predicate", "value", "kind", "epistemic", "valid_from", "valid_to"}
                if not isinstance(patch, dict) or not patch or set(patch) - allowed:
                    raise ValueError("invalid correction fields")
                effective.update(patch)
                effective = self._validate_claim(effective)
            if action in {"approve", "correct"}:
                for eid in effective["evidence_ids"]:
                    self.resolve_evidence(eid, 0)
            status = {"approve": "human_approved", "reject": "rejected", "revoke": "revoked", "defer": "needs_review", "correct": "human_approved"}[action]
            self.db.execute("UPDATE claims SET override=?,status=?,revision=revision+1,updated=? WHERE id=?",
                            (dump(effective), status, now(), cid))
            updated = self.claim(cid)
            self.db.execute("INSERT INTO reviews(claim_id,action,reviewer,note,before_json,after_json,created) VALUES (?,?,?,?,?,?,?)",
                            (cid, action, reviewer, note, dump(current), dump(updated), now()))
        return updated

    def history(self, cid: str) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM reviews WHERE claim_id=? ORDER BY seq", (cid,))]

    def canonical(self, eid: str) -> str:
        seen = set()
        while eid not in seen:
            seen.add(eid)
            row = self.db.execute("SELECT target FROM merges WHERE source=?", (eid,)).fetchone()
            if not row:
                return eid
            eid = row[0]
        raise ValueError("identity cycle")

    def merge(self, source: str, target: str, reviewer: str, note: str) -> None:
        a, b = self.get("entities", source), self.get("entities", target)
        if not a or not b or a["scope"] != b["scope"] or source == target:
            raise ValueError("merge requires different characters in the same canon scope")
        if not text(note, 2000):
            raise ValueError("merge requires a reason")
        with self.transaction():
            if self.canonical(target) == source:
                raise ValueError("identity cycle")
            self.db.execute("INSERT OR REPLACE INTO merges VALUES (?,?)", (source, target))
            self.db.execute("INSERT INTO merge_history(body) VALUES (?)", (dump({"source": source, "target": target,
                "reviewer": text(reviewer, 80), "note": note, "at": now()}),))

    def unmerge(self, source: str, reviewer: str, note: str) -> None:
        reviewer, note = text(reviewer, 80), text(note, 2000)
        with self.transaction():
            row = self.db.execute("SELECT target FROM merges WHERE source=?", (source,)).fetchone()
            if not row:
                raise ValueError("no active identity link")
            self.db.execute("DELETE FROM merges WHERE source=?", (source,))
            self.db.execute("INSERT INTO merge_history(body) VALUES (?)", (dump({"source": source, "target": row[0],
                "action": "unmerge", "reviewer": reviewer, "note": note, "at": now()}),))

    def graph(self) -> dict:
        claims = [self.claim(r[0]) for r in self.db.execute("SELECT id FROM claims ORDER BY id")]
        fingerprints = {}
        for source in self.rows("sources"):
            try:
                fingerprints[source["id"]] = read_source(Path(source["path"]))[1]
            except (ValueError, OSError):
                fingerprints[source["id"]] = None
        for c in claims:
            c["stale"] = any(fingerprints.get(self.get("evidence", e)["source_id"]) != self.get("evidence", e)["source_hash"]
                             for e in c["evidence_ids"])
            c["display_subject"] = self.canonical(c["subject"])
            c["display_object"] = self.canonical(c["object"]) if c["object"] else None
        return {"schema": "pastiche.character.graph.v1", "entities": self.rows("entities"), "claims": claims,
                "redirects": dict(self.db.execute("SELECT source,target FROM merges")),
                "merges": [json.loads(r[0]) for r in self.db.execute("SELECT body FROM merge_history ORDER BY seq")],
                "layout": {r[0]: {"x": r[1], "y": r[2]} for r in self.db.execute("SELECT id,x,y FROM layout")}}

    def save_layout(self, values: dict) -> None:
        if not isinstance(values, dict) or len(values) > 2000:
            raise ValueError("invalid layout")
        with self.transaction():
            for eid, p in values.items():
                if not self.get("entities", eid) or not isinstance(p, dict):
                    raise ValueError("unknown layout entity")
                for k in ("x", "y"):
                    if type(p.get(k)) not in (int, float) or not math.isfinite(p[k]) or abs(p[k]) > 1_000_000:
                        raise ValueError("invalid coordinate")
                self.db.execute("INSERT OR REPLACE INTO layout VALUES (?,?,?)", (eid, p["x"], p["y"]))

    def writer_context(self, scope: str, cutoff: float = 1.0) -> dict:
        cutoff = position(cutoff)
        if cutoff is None:
            raise ValueError("cutoff is required")
        graph = self.graph()
        selected = []
        for c in graph["claims"]:
            if c["scope"] != scope or c["status"] not in {"auto_accepted", "human_approved"} or c["stale"]:
                continue
            # Unknown observation time is excluded for spoiler-restricted views.
            if cutoff < 1 and (c["observed_at"] is None or c["observed_at"] > cutoff):
                continue
            if c["valid_from"] is not None and not c["valid_from"] <= cutoff <= c["valid_to"]:
                continue
            selected.append({k: c[k] for k in ("id", "display_subject", "display_object", "predicate", "value", "kind", "epistemic", "status", "model_checked", "observed_at", "valid_from", "valid_to", "evidence_ids")})
        used = {c["display_subject"] for c in selected} | {c["display_object"] for c in selected if c["display_object"]}
        return {"schema": "pastiche.writer.characters.v1", "scope": scope, "cutoff": cutoff,
                "entities": [e for e in graph["entities"] if e["id"] in used], "claims": selected,
                "note": "Auto-accepted source-grounded claims and human-approved edits. Auto-accepted is NOT independently verified or established canon; stale/revoked/rejected/deferred claims are excluded. Unknown duration is not persistence."}


def atomic_json(path: Path, body: Any) -> None:
    import os
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(dump(body) + "\n")
        os.replace(tmp, path)
    finally:
        if Path(tmp).exists():
            Path(tmp).unlink()

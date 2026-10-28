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
from json_boundary import atomic_json

MAX_SOURCE_BYTES = 16_000_000
CHARACTER_KINDS = {"appearance", "voice", "value", "goal", "belief", "knowledge", "relationship", "attribute"}
WORLD_TYPES = {"place", "organization", "polity", "species", "artifact", "concept", "system", "culture", "era"}
WORLD_KINDS = {"geography", "affiliation", "history", "institution", "law", "culture", "technology", "magic", "ecology", "resource", "cosmology", "world_relation"}
WORLD_MODALITIES = {"descriptive", "normative", "capability", "belief", "unknown"}
USABLE_STATUSES = {"auto_accepted", "human_approved"}


def entity_type(entity: dict) -> str:
    """Old JSON entity records are characters; do not change their identifiers."""
    return entity.get("entity_type", "character")


def short_list(value: Any, maximum: int = 8) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError("invalid world conditions or exceptions")
    return list(dict.fromkeys(text(v, 240) for v in value))


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

    def entity(self, namespace: str, name: str, scope: str, aliases: list[str] | None = None,
               *, entity_type: str = "character") -> str:
        if entity_type not in WORLD_TYPES | {"character"}:
            raise ValueError("unknown entity type")
        name, scope = text(name, 100), text(scope, 160)
        # Preserve legacy character IDs and human review history exactly.
        eid = (uid("C", namespace, name, scope) if entity_type == "character"
               else uid("W", namespace, name, scope, entity_type))
        old = self.get("entities", eid)
        if aliases is not None and (not isinstance(aliases, list) or len(aliases) > 30):
            raise ValueError("invalid aliases")
        aliases = sorted({text(a, 100) for a in (aliases or [])} | set((old or {}).get("aliases", [])))
        value = {"id": eid, "name": name, "namespace": namespace, "scope": scope,
                 "aliases": aliases, "entity_type": entity_type}
        self.db.execute("INSERT OR REPLACE INTO entities VALUES (?,?)", (eid, dump(value)))
        self.db.commit()
        return eid

    def _validate_claim(self, body: dict) -> dict:
        if not isinstance(body, dict):
            raise ValueError("claim must be an object")
        for key in ("subject", "object", "holder", "proposition_subject", "speaker"):
            value = body.get(key)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError("claim entity IDs must be nonempty strings or null")
        for key in ("domain", "epistemic", "modality", "polarity", "stance", "basis"):
            if key in body and not isinstance(body[key], str):
                raise ValueError("claim classifications must be strings")
        result = {"subject": text(body.get("subject"), 100), "predicate": text(body.get("predicate"), 80),
                  "object": body.get("object"), "value": text(body.get("value"), 400),
                  "kind": text(body.get("kind", "attribute"), 40),
                  "epistemic": body.get("epistemic", "inferred"), "scope": text(body.get("scope"), 160),
                  "observed_at": position(body.get("observed_at")),
                  "valid_from": position(body.get("valid_from")), "valid_to": position(body.get("valid_to")),
                  "evidence_ids": body.get("evidence_ids"), "model_checked": body.get("model_checked") is True}
        if result["epistemic"] not in {"explicit", "inferred", "reported"}:
            raise ValueError("unknown epistemic status")
        domain = body.get("domain", "world" if result["kind"] in WORLD_KINDS else "character")
        if domain not in {"world", "character", "epistemic"}:
            raise ValueError("unknown knowledge domain")
        if result["kind"] not in ({"perspective"} if domain == "epistemic" else WORLD_KINDS if domain == "world" else CHARACTER_KINDS):
            raise ValueError("unknown claim kind")
        if domain == "world":
            result.update(domain="world", conditions=short_list(body.get("conditions", [])),
                          exceptions=short_list(body.get("exceptions", [])),
                          modality=body.get("modality", "unknown"),
                          polarity=body.get("polarity", "unknown"),
                          story_time=None if body.get("story_time") is None else text(body["story_time"], 160))
            if result["modality"] not in WORLD_MODALITIES or result["polarity"] not in {"affirmed", "denied", "unknown"}:
                raise ValueError("invalid world rule modality or polarity")
        if domain == "epistemic":
            from .epistemic import BASES, RESERVED
            holder, prop, speaker = body.get("holder"), body.get("proposition_subject"), body.get("speaker")
            for key in [holder, prop, speaker]:
                if key in RESERVED or key is None:
                    continue
                ent = self.get("entities", key)
                if not ent or ent["scope"] != result["scope"]:
                    raise ValueError("unknown or cross-scope perspective entity")
            if (holder not in RESERVED and (not holder or entity_type(self.get("entities", holder)) != "character")):
                raise ValueError("perspective holder must be a character/world/reader")
            if not prop or prop != result["object"] or result["subject"] != (prop if holder in RESERVED else holder):
                raise ValueError("perspective endpoints and holder do not agree")
            if speaker is not None and (speaker in RESERVED or entity_type(self.get("entities", speaker)) != "character"):
                raise ValueError("speaker must be a character")
            if body.get("stance") not in {"affirmed", "denied", "uncertain"} or body.get("basis") not in BASES:
                raise ValueError("invalid perspective stance or acquisition basis")
            result.update(domain=domain, holder=holder, proposition_subject=prop, speaker=speaker,
                          stance=body["stance"], basis=body["basis"],
                          event_id=None if body.get("event_id") is None else text(body["event_id"], 100))
        for k in ("subject", "object"):
            if result[k] is not None:
                ent = self.get("entities", result[k])
                if not ent or ent["scope"] != result["scope"]:
                    raise ValueError("unknown or cross-scope entity")
        involved = [self.get("entities", result[k]) for k in ("subject", "object") if result[k]]
        if domain == "character" and any(entity_type(e) != "character" for e in involved):
            raise ValueError("character claims require character entities")
        if domain == "world" and all(entity_type(e) == "character" for e in involved):
            raise ValueError("world claims require a world entity")
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
        self.db.execute("INSERT OR IGNORE INTO claims(id,body,status,updated) VALUES (?,?,?,?)", (cid, dump(value), "auto_accepted", now()))
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
                if effective.get("domain") == "world":
                    allowed |= {"conditions", "exceptions", "modality", "polarity", "story_time"}
                if effective.get("domain") == "epistemic":
                    allowed |= {"holder", "proposition_subject", "stance", "basis", "speaker", "event_id"}
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
        if (not a or not b or a["scope"] != b["scope"] or source == target
                or entity_type(a) != entity_type(b)):
            raise ValueError("merge requires different entities of the same type and canon scope")
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
            c["domain"] = c.get("domain", "character")
            c["display_subject"] = self.canonical(c["subject"])
            c["display_object"] = self.canonical(c["object"]) if c["object"] else None
        return {"schema": "pastiche.knowledge.graph.v2",
                "entities": [dict(e, entity_type=entity_type(e)) for e in self.rows("entities")], "claims": claims,
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

    def _context(self, scope: str, cutoff: float, domain: str, *, graph: dict | None = None) -> dict:
        cutoff = position(cutoff)
        if cutoff is None:
            raise ValueError("cutoff is required")
        graph = self.graph() if graph is None else graph
        selected = []
        for c in graph["claims"]:
            if (c["scope"] != scope or c["domain"] != domain
                    or c["status"] not in USABLE_STATUSES or c["stale"]):
                continue
            if cutoff < 1 and (c["observed_at"] is None or c["observed_at"] > cutoff):
                continue
            if c["valid_from"] is not None and not c["valid_from"] <= cutoff <= c["valid_to"]:
                continue
            keys = ("id", "display_subject", "display_object", "predicate", "value", "kind", "epistemic",
                    "status", "model_checked", "observed_at", "valid_from", "valid_to", "evidence_ids", "revision")
            value = {k: c[k] for k in keys}
            if domain == "world":
                value.update({k: c[k] for k in ("domain", "conditions", "exceptions", "modality", "polarity", "story_time")})
            if domain == "epistemic":
                value.update({k: c[k] for k in ("domain", "holder", "proposition_subject", "stance", "basis", "speaker", "event_id")})
                for key in ("holder", "proposition_subject", "speaker"):
                    if value[key] and not value[key].startswith("$"):
                        value[key] = self.canonical(value[key])
            selected.append(value)
        used = {c["display_subject"] for c in selected} | {c["display_object"] for c in selected if c["display_object"]}
        if domain == "epistemic":
            used.update(c[k] for c in selected for k in ("holder", "proposition_subject", "speaker") if c[k] and not c[k].startswith("$"))
        return {"schema": {"character":"pastiche.writer.characters.v2", "world":"pastiche.writer.world.v1",
                           "epistemic":"pastiche.writer.perspectives.v1"}[domain],
                "scope": scope, "cutoff": cutoff,
                "entities": [e for e in graph["entities"] if e["id"] in used], "claims": selected,
                "note": "Auto-accepted source-grounded claims and human-approved edits. Auto-accepted is NOT independently verified or established canon; stale/revoked/rejected/deferred claims are excluded. Unknown duration is not persistence."}

    def writer_context(self, scope: str, cutoff: float = 1.0) -> dict:
        return self._context(scope, cutoff, "character")

    def perspective_context(self, scope: str, cutoff: float = 1.0) -> dict:
        return self._context(scope, cutoff, "epistemic")

    def world_context(self, scope: str, cutoff: float = 1.0) -> dict:
        from .world import conflict_candidates
        context = self._context(scope, cutoff, "world")
        context["conflicts"] = conflict_candidates(context["claims"])
        return context

    def world_graph(self) -> dict:
        graph = self.graph()
        graph["claims"] = [c for c in graph["claims"] if c["domain"] == "world"]
        used = {c[k] for c in graph["claims"] for k in ("display_subject", "display_object") if c[k]}
        graph["entities"] = [e for e in graph["entities"] if entity_type(e) != "character" or e["id"] in used]
        graph["schema"] = "pastiche.world.graph.v1"
        from .world import conflict_candidates
        graph["conflicts"] = conflict_candidates([c for c in graph["claims"]
            if c["status"] in USABLE_STATUSES and not c["stale"]])
        return graph

    def validate_context_snapshot(self, context: dict) -> None:
        """Recheck all selected claims, entities AND plot evidence at point of use."""
        from .context_validation import validate_snapshot
        if self.db.in_transaction:
            validate_snapshot(self, context)
        else:
            with self.transaction():
                validate_snapshot(self, context)

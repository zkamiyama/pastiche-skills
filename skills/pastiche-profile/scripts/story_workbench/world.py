"""Typed, source-grounded world knowledge. No graph DB or ML runtime needed.

A world assertion is not an unconditional fact. Keep its modality, conditions,
exceptions, narrative observation cutoff and uncertain story-era information.
Contradiction detection deliberately reports candidates, not a proof of error.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Callable

from .store import (Store, WORLD_TYPES, WORLD_KINDS, WORLD_MODALITIES, text,
                    short_list, entity_type, atomic_json, dump)

TYPE_LABELS = {"character": "人物", "place": "場所", "organization": "組織", "polity": "国家・政治体",
               "species": "種族・生物", "artifact": "物品・資源", "concept": "概念・規範",
               "system": "技術・魔法体系", "culture": "文化", "era": "時代"}


def prompt_contract() -> str:
    return (
        " Also extract WORLD KNOWLEDGE, not just characters: places/geography, institutions, "
        "political entities, history/eras, technology or magic rules, species/ecology, "
        "resources and social customs. World types: " + ",".join(sorted(WORLD_TYPES)) + ". "
        "World claim kinds: " + ",".join(sorted(WORLD_KINDS)) + ". "
        "World names or aliases must occur in their cited segments. Do not invent a "
        "setting from genre conventions. A law forbidding an action is normative, "
        "not proof that the action is physically impossible. A character's assertion "
        "or rumor remains reported, not universal truth. Retain exceptions and "
        "conditions; never turn a single occurrence into a universal law. Distinguish "
        "what is true in the setting from what a character believes. Unknown dates "
        "stay null. Entity IDs must be unique across character/world arrays. At most "
        "20 world entities and 30 world claims per chunk. Both arrays are REQUIRED "
        "(empty when nothing is evidenced). Add world_entities:[local world IDs] to "
        "each event to connect places/organizations/rules used in that event. "
        'Extend the JSON with "world_entities":[{"id":"w1","name":"原文中の名称",'
        '"entity_type":"place","aliases":[],"identity":"explicit",'
        '"evidence_ids":["actual segment ID"]}],"world_claims":[{"subject":"w1",'
        '"object":null,"kind":"law","predicate":"requires_permission",'
        '"value":"短い観察","epistemic":"explicit","modality":"normative",'
        '"polarity":"affirmed","conditions":[],"exceptions":[],"story_time":null,'
        '"evidence_ids":["actual segment ID"]}]. World subjects/objects may refer '
        "to supplied character IDs (membership, location, authority), but at least "
        "one endpoint must be a world entity. modality: descriptive|normative|"
        "capability|belief|unknown. polarity: affirmed|denied|unknown, describing "
        "whether that exact predicate holds, not whether it sounds good or bad. "
    )


def parse_world(raw: dict, chunk: list[dict], characters: list[dict],
                abstract: Callable) -> tuple[list[dict], list[dict]]:
    from .protocol import array, ids
    if "world_entities" not in raw or "world_claims" not in raw:
        raise ValueError("world extraction arrays are required; missing output is not absence")
    refs = {u["id"] for u in chunk}
    cids = {c["id"] for c in characters}
    known, entities = set(cids), []
    for e in array(raw["world_entities"], 20):
        if not isinstance(e, dict):
            raise ValueError("world entity must be an object")
        eid, name = text(e.get("id"), 40), text(e.get("name"), 100)
        kind = e.get("entity_type")
        if kind not in WORLD_TYPES or eid in known or e.get("identity") not in {"explicit", "uncertain"}:
            raise ValueError("invalid or duplicate world entity")
        aliases = [text(a, 100) for a in array(e.get("aliases", []), 10)]
        evidence = ids(e.get("evidence_ids"), refs)
        witness = "".join(u["text"] for u in chunk if u["id"] in evidence)
        if not any(n in witness for n in [name, *aliases]):
            raise ValueError("world entity name/alias has no cited evidence")
        entities.append({"id": eid, "name": name, "entity_type": kind, "aliases": aliases,
                         "identity": e["identity"], "evidence_ids": evidence})
        known.add(eid)
    claims = []
    for c in array(raw["world_claims"], 30):
        if not isinstance(c, dict):
            raise ValueError("world claim must be an object")
        subject, obj = c.get("subject"), c.get("object")
        if (subject not in known or (obj is not None and obj not in known)
                or (subject in cids and (obj is None or obj in cids))):
            raise ValueError("world claim requires known endpoints and a world entity")
        if (c.get("kind") not in WORLD_KINDS or c.get("modality") not in WORLD_MODALITIES
                or c.get("epistemic") not in {"explicit", "inferred", "reported"}
                or c.get("polarity") not in {"affirmed", "denied", "unknown"}):
            raise ValueError("invalid world claim classification")
        conditions = [abstract(v, 240) for v in short_list(c.get("conditions", []))]
        exceptions = [abstract(v, 240) for v in short_list(c.get("exceptions", []))]
        claims.append({"subject": subject, "object": obj, "domain": "world", "kind": c["kind"],
                       "predicate": text(c.get("predicate"), 80), "value": abstract(c.get("value"), 400),
                       "epistemic": c["epistemic"], "modality": c["modality"], "polarity": c["polarity"],
                       "conditions": conditions, "exceptions": exceptions,
                       "story_time": None if c.get("story_time") is None else abstract(c["story_time"], 160),
                       "evidence_ids": ids(c.get("evidence_ids"), refs)})
    return entities, claims


def conflict_candidates(claims: list[dict], maximum: int = 100) -> dict:
    """Conservative exact-predicate polarity conflicts; semantic recall is limited.

    Different canon scopes, explicit eras or conditional exceptions are kept
    separate. Unknown durations are explicitly marked, never treated as proof
    of temporal overlap. Names are only unified via human-approved identity links.
    """
    groups: dict[tuple, list] = defaultdict(list)
    for c in claims:
        if c.get("polarity") not in {"affirmed", "denied"}:
            continue
        key = (c.get("scope"), c.get("display_subject", c.get("subject")), c["predicate"],
               c.get("display_object", c.get("object")), c.get("story_time"),
               tuple(sorted(c.get("conditions", []))), tuple(sorted(c.get("exceptions", []))), c.get("modality"))
        groups[key].append(c)
    result = []
    for rows in groups.values():
        yes = [c for c in rows if c["polarity"] == "affirmed"]
        no = [c for c in rows if c["polarity"] == "denied"]
        for a in yes:
            for b in no:
                known = all(c.get("valid_from") is not None and c.get("valid_to") is not None for c in (a, b))
                if known and max(a["valid_from"], b["valid_from"]) > min(a["valid_to"], b["valid_to"]):
                    continue
                if len(result) >= maximum:
                    return {"candidates": result, "truncated": True, "method": "exact_predicate_opposite_polarity"}
                result.append({"claim_ids": sorted([a["id"], b["id"]]), "reason": "opposing_polarity",
                               "temporal_overlap": "overlap" if known else "unknown",
                               "status": "needs_context", "note": "May reflect unreliable narration or changing rules; not automatically resolved."})
    return {"candidates": result, "truncated": False, "method": "exact_predicate_opposite_polarity"}


def world_bible(context: dict) -> str:
    names = {e["id"]: e["name"] for e in context["entities"]}
    lines = ["# World Bible — source-grounded, provisional", "",
             f"作品版: {context['scope']} / 観測位置: {context['cutoff']}",
             "自動採用は人間の確認済みではありません。法・能力・信仰と、条件・例外・時期を区別してください。",
             "出典が変更された情報、使用停止・保留・却下は除外しています。", ""]
    for c in context["claims"]:
        subject, obj = names[c["display_subject"]], names.get(c["display_object"], "")
        lines.append(f"- {subject} / {c['kind']} / {c['predicate']} {obj}: {c['value']} [{c['id']}] ({c['epistemic']}, {c['status']}, {c['modality']}, {c['polarity']})")
        if c["conditions"]: lines.append("  - 条件: " + " / ".join(c["conditions"]))
        if c["exceptions"]: lines.append("  - 例外: " + " / ".join(c["exceptions"]))
        if c["story_time"]: lines.append("  - 作中の時期: " + c["story_time"])
        lines.append("  - 根拠: " + ", ".join(c["evidence_ids"]))
    if not context["claims"]:
        lines.append("この範囲で利用可能な世界設定はありません。空の出力は設定の不存在を意味しません。")
    if context.get("conflicts", {}).get("candidates"):
        lines += ["", "## 矛盾の可能性（自動判定で解消しない）"]
        for item in context["conflicts"]["candidates"]:
            lines.append("- " + " / ".join(item["claim_ids"]) + " — 時期の重なり: " + item["temporal_overlap"])
    return "\n".join(lines) + "\n"


def export_world(store: Store, out: Path, scope: str, cutoff: float = 1.0) -> dict:
    graph = store.world_graph()
    atomic_json(out / "world_graph.json", graph)
    context = store.world_context(scope, cutoff)
    atomic_json(out / "world_context.json", context)
    (out / "world_bible.md").write_text(world_bible(context), encoding="utf-8")
    return {"entities": len([e for e in graph["entities"] if entity_type(e) != "character"]),
            "claims": len(graph["claims"]), "usable_claims": len(context["claims"]),
            "conflict_candidates": len(context["conflicts"]["candidates"]),
            "scope": scope, "cutoff": cutoff,
            "note": "World extraction covers submitted text, not necessarily every true rule; no completeness or semantic correctness guarantee."}

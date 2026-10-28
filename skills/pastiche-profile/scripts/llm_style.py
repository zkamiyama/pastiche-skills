#!/usr/bin/env python3
"""LLM-first corpus analysis with validated, resumable qualitative synthesis.

Pipeline: sample -> scene observations -> LLM semantic grouping -> LLM group
audit -> source-evidence recheck -> private, provenance-labelled style guides.
Raw passages are passed only to the configured model and never persisted.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sys
from typing import Any

import llm_style_core as core
import narrative
import narrative_fast


JAPANESE_LABELS = {
    "lexicon": "語彙・言葉の選び方",
    "emotion": "情感の作り方",
    "voice": "語り口・視点の距離",
    "dialogue": "台詞の運び",
    "sensory": "感覚描写",
    "pacing": "テンポ・緩急",
    "narrative": "物語の構造",
}


def sampled_jobs(corpus: Path, works_limit: int, chunks_per_work: int,
                 chunk_chars: int) -> tuple[list[Any], list[Any], int]:
    works = narrative.enumerated_corpus(corpus, 80)
    if not works:
        raise ValueError("no qualifying corpus")
    selected_idx = set(narrative_fast.stratified_indices(
        len(works), works_limit if works_limit else len(works)))
    selected = []
    jobs = []
    full_chunks = 0
    for work_index, (work_id, _path, units) in enumerate(works):
        chunks = narrative.chunk_segments(units, work_id, chunk_chars)
        full_chunks += len(chunks)
        if work_index in selected_idx:
            selected.append(work_id)
            for position, chunk in narrative_fast.select_chunks(
                    chunks, chunks_per_work, work_index):
                jobs.append((work_id, position, chunk))
    return works, jobs, full_chunks


def _run_tasks(jobs: list[Any], model: Any, workers: int, task: Any,
               reporter: narrative.ProgressReporter, stage: str,
               store: narrative.CheckpointStore) -> tuple[dict[Any, Any], int, int]:
    """Read and validate cached stage outputs on main thread, then execute misses.

    job = (logical_id, key, payload, validator); validator is pure and raises
    on malformed results. Callback returns the *raw* model JSON. Validated,
    sanitized outputs are checkpointed. No raw response or source in SQLite.
    """
    ready = {}
    pending = []
    restored = 0
    for logical_id, key, payload, validator in jobs:
        entry = store.get(key)
        if isinstance(entry, dict) and "value" in entry:
            try:
                ready[logical_id] = validator(entry["value"])
                restored += 1
                continue
            except (ValueError, TypeError, KeyError):
                pass
        pending.append((logical_id, key, payload, validator))

    reporter.status(stage=stage, total_tasks=len(jobs), completed_tasks=len(ready),
                    cached_tasks=restored)

    def work(job: Any, worker_model: Any) -> Any:
        logical_id, key, payload, validator = job
        response = task(payload, worker_model, logical_id)
        return validator(response)

    calls = 0
    for (logical_id, key, _payload, _validator), value in narrative.model_jobs(
            pending, model, workers, work):
        store.put(key, {"value": value})
        ready[logical_id] = value
        calls += 1
        reporter.status(stage=stage, completed_tasks=len(ready),
                        total_tasks=len(jobs), cached_tasks=restored)
    return ready, calls, restored


def _observations(jobs: list[Any], model: Any,
                  store: narrative.CheckpointStore,
                  reporter: narrative.ProgressReporter,
                  workers: int, retries: int) -> tuple[list[dict[str, Any]], dict[Any, Any], int, int]:
    requests = []
    chunk_map = {}
    for work_id, position, chunk in jobs:
        chunk_map[(work_id, position)] = chunk
        if len(chunk) < 2:
            continue
        key = "scene:" + core.digest("observe", chunk, model)
        def validator(value: Any, passage: Any = chunk) -> Any:
            # Cached value is a validated list, raw response a JSON object.
            if isinstance(value, list):
                return core.clean_observations({"observations": value}, passage)
            return core.clean_observations(value, passage)
        requests.append(((work_id, position), key, (work_id, chunk), validator))

    def call(payload: Any, worker_model: Any, logical_id: Any) -> Any:
        work_id, passage = payload
        return reporter.call(worker_model, core.observe_prompt(work_id, passage),
                             f"STYLE OBSERVE {work_id} chunk {logical_id[1]}",
                             retries=retries, record_status=workers == 1)

    data, calls, restored = _run_tasks(
        requests, model, workers, call, reporter, "STYLE_OBSERVE", store)
    all_items = []
    for work_id, position, chunk in jobs:
        for item in data.get((work_id, position), []):
            all_items.append(item | {
                "id": f"O{len(all_items) + 1:05d}",
                "work_id": work_id, "chunk_index": position,
            })
    return all_items, chunk_map, calls, restored


def _initial_nodes(items: list[dict[str, Any]],
                   dimension: str) -> list[dict[str, Any]]:
    return [{
        "id": item["id"], "members": [item["id"]],
        "dimension": dimension, "trigger": item["trigger"],
        "technique": item["technique"], "effect": item["effect"],
        "label": "",
    } for item in items if item["dimension"] == dimension]


def _cluster(items: list[dict[str, Any]], model: Any,
             store: narrative.CheckpointStore,
             reporter: narrative.ProgressReporter,
             workers: int, retries: int,
             batch_size: int, rounds: int) -> tuple[list[dict[str, Any]], int, int]:
    all_nodes = []
    total_calls = 0
    total_cached = 0
    for dimension in core.DIMENSIONS:
        nodes = _initial_nodes(items, dimension)
        for round_no in range(rounds):
            panels = core.cluster_panels(nodes, batch_size, round_no)
            if not panels:
                break
            requests = []
            for panel_index, panel in enumerate(panels):
                key = "cluster:" + core.digest(
                    "cluster", {"dimension": dimension, "panel": panel}, model)
                allowed = {node["id"] for node in panel}
                def validate(raw: Any, choices: set[str] = allowed) -> Any:
                    # Convert tuple list (from cache) back through the same validator.
                    if isinstance(raw, list):
                        raw = {"groups": [
                            {"ids": ids, "label": label} for ids, label in raw]}
                    return core.parse_clusters(raw, choices)
                requests.append((panel_index, key, (dimension, panel), validate))

            def call(payload: Any, worker_model: Any, panel_index: Any) -> Any:
                dim, panel = payload
                return reporter.call(
                    worker_model, core.cluster_prompt(dim, panel),
                    f"STYLE CLUSTER {dim} round={round_no + 1} panel={panel_index + 1}",
                    retries=retries, record_status=workers == 1)

            results, calls, cached = _run_tasks(
                requests, model, workers, call, reporter, "STYLE_CLUSTER", store)
            total_calls += calls
            total_cached += cached
            merged = []
            handled = set()
            merged_count = 0
            for i, panel in enumerate(panels):
                merges = results[i]
                merged_count += sum(len(ids) - 1 for ids, _ in merges)
                merged.extend(core.regroup(panel, merges))
                handled.update(n["id"] for n in panel)
            merged.extend(n for n in nodes if n["id"] not in handled)
            nodes = sorted(merged, key=lambda node: node["id"])
            reporter.log(f"cluster dimension={dimension} round={round_no + 1} "
                         f"remaining={len(nodes)} reductions={merged_count}")
            if len(nodes) <= 1:
                break
            # If all candidates fit into one panel, repeating it is useless.
            if len(panels) == 1:
                break
            # Do one interleaved pass even if adjacent pairs did not merge.
            if not merged_count and round_no >= 1:
                break
        all_nodes.extend(nodes)
    return all_nodes, total_calls, total_cached


def _work_count(ids: list[str], by_id: dict[str, Any]) -> int:
    return len({by_id[oid]["work_id"] for oid in ids})


def _review_groups(nodes: list[dict[str, Any]],
                   by_id: dict[str, dict[str, Any]], model: Any,
                   store: narrative.CheckpointStore,
                   reporter: narrative.ProgressReporter,
                   workers: int, retries: int,
                   minimum_works: int, max_groups: int) -> tuple[list[dict[str, Any]], dict[str, list[str]], int, int]:
    # Review only repeated groups, prioritizing wider independent-work support.
    candidates = [n for n in nodes if _work_count(n["members"], by_id) >= minimum_works]
    candidates.sort(key=lambda n: (-_work_count(n["members"], by_id),
                                   -len(n["members"]), n["dimension"], n["id"]))
    chosen = candidates[:max_groups]
    requests = []
    for node in chosen:
        # Limit group-review prompt size; span distinct works first.
        selected = []
        seen_works = set()
        for oid in node["members"]:
            work_id = by_id[oid]["work_id"]
            if work_id not in seen_works:
                selected.append(by_id[oid])
                seen_works.add(work_id)
            if len(selected) >= 12:
                break
        if len(selected) < 12:
            selected_ids = {s["id"] for s in selected}
            selected.extend(by_id[oid] for oid in node["members"]
                            if oid not in selected_ids and len(selected) < 12)
        label = node["label"] or node["technique"]
        key = "group-review:" + core.digest(
            "review", {"label": label, "items": selected}, model)
        allowed = {item["id"] for item in selected}
        def validator(raw: Any, choices: set[str] = allowed) -> list[str]:
            # Cache stores only list of accepted IDs, never raw model prose.
            if isinstance(raw, list):
                return sorted(raw) if (len(raw) == len(set(raw))
                          and set(raw) <= choices) else core.parse_review(None, choices)
            return core.parse_review(raw, choices)
        requests.append((node["id"], key,
                         (node["dimension"], selected, label), validator))

    def call(payload: Any, worker_model: Any, key: Any) -> Any:
        dimension, selected, label = payload
        return reporter.call(
            worker_model, core.review_prompt(dimension, selected, label),
            f"STYLE GROUP CHECK {key}", retries=retries,
            record_status=workers == 1)

    reviewed, calls, cached = _run_tasks(
        requests, model, workers, call, reporter, "STYLE_GROUP_CHECK", store)
    return chosen, reviewed, calls, cached


def _check_sources(chosen: list[dict[str, Any]],
                   reviewed: dict[str, list[str]],
                   by_id: dict[str, dict[str, Any]],
                   chunk_map: dict[Any, Any], model: Any,
                   store: narrative.CheckpointStore,
                   reporter: narrative.ProgressReporter,
                   workers: int, retries: int,
                   minimum_works: int, verify_works: int) -> tuple[dict[str, list[str]], int, int]:
    requests = []
    for node in chosen:
        accepted = reviewed.get(node["id"], [])
        if _work_count(accepted, by_id) < minimum_works:
            continue
        selected, seen_works = [], set()
        for oid in accepted:
            obs = by_id[oid]
            if obs["work_id"] in seen_works:
                continue
            seen_works.add(obs["work_id"])
            selected.append(obs)
            if len(selected) >= verify_works:
                break
        label = node["label"] or node["technique"]
        for obs in selected:
            chunk = chunk_map[(obs["work_id"], obs["chunk_index"])]
            key = "source-check:" + core.digest(
                "source-check", {"item": obs, "chunk": chunk, "label": label}, model)
            def validator(raw: Any, item: Any = obs, text: Any = chunk) -> bool:
                if isinstance(raw, bool):
                    return raw
                if not isinstance(raw, dict) or type(raw.get("supported")) is not bool:
                    raise ValueError("STYLE_EVIDENCE did not return a boolean verdict")
                return core.valid_evidence(raw, item, text)
            requests.append(((node["id"], obs["id"]), key, (obs, chunk, label), validator))

    def call(payload: Any, worker_model: Any, key: Any) -> Any:
        obs, chunk, label = payload
        return reporter.call(
            worker_model, core.evidence_prompt(chunk, obs, label),
            f"STYLE EVIDENCE {key[0]} {key[1]}", retries=retries,
            record_status=workers == 1)

    answers, calls, cached = _run_tasks(
        requests, model, workers, call, reporter, "STYLE_EVIDENCE", store)
    supports = {}
    for node in chosen:
        supports[node["id"]] = sorted(
            oid for (node_id, oid), valid in answers.items()
            if node_id == node["id"] and valid)
    return supports, calls, cached


def _synthesize(patterns: list[dict[str, Any]], model: Any,
                store: narrative.CheckpointStore,
                reporter: narrative.ProgressReporter,
                workers: int, retries: int) -> tuple[list[dict[str, Any]], int, int]:
    supported = [p for p in patterns if p["status"] == "model_supported"][:24]
    if not supported:
        return [], 0, 0

    key = "synthesis:" + core.digest("synthesize", supported, model)

    def validate(raw: Any) -> list[dict[str, Any]]:
        if isinstance(raw, list):
            raw = {"sections": raw}
        return core.parse_synthesis(raw, supported)

    def call(payload: Any, worker_model: Any, logical_id: Any) -> Any:
        return reporter.call(worker_model, core.synthesize_prompt(payload),
                             "STYLE SYNTHESIZE", retries=retries,
                             record_status=workers == 1)

    data, calls, cached = _run_tasks(
        [("overall", key, supported, validate)],
        model, workers, call, reporter, "STYLE_SYNTHESIZE", store)
    return data["overall"], calls, cached


def make_patterns(nodes: list[dict[str, Any]],
                  by_id: dict[str, dict[str, Any]],
                  verified: dict[str, list[str]],
                  sampled: int, total_works: int,
                  minimum_works: int) -> list[dict[str, Any]]:
    patterns = []
    for node in nodes:
        members = node["members"]
        supported = verified.get(node["id"], [])
        work_count = _work_count(supported, by_id)
        valid = work_count >= minimum_works
        exemplar = by_id[members[0]]
        evidence_ids = sorted({
            eid for oid in (supported if valid else members)
            for eid in by_id[oid]["evidence_ids"]})
        anchors = Counter()
        anchor_works = {}
        for oid in members:
            item = by_id[oid]
            for anchor in item.get("anchors", []):
                anchor_works.setdefault(anchor, set()).add(item["work_id"])
        anchors = [a for a, works in sorted(
            anchor_works.items(), key=lambda x: (-len(x[1]), x[0]))
            if len(works) >= minimum_works][:3]
        patterns.append({
            "pattern_id": "", "dimension": node["dimension"],
            "status": "model_supported" if valid else "hypothesis",
            "trigger": exemplar["trigger"],
            "mechanism": node["label"] or exemplar["technique"],
            "effect": exemplar["effect"], "anchors": anchors,
            "observed_work_count": _work_count(members, by_id),
            "support_work_count": work_count if valid else 0,
            "support_occurrences": len(members),
            "sample_coverage": round(_work_count(members, by_id)/max(sampled, 1), 4),
            "corpus_observed_lower_bound": round(
                _work_count(members, by_id)/max(total_works, 1), 4),
            "evidence_ids": evidence_ids,
            "source": "model_two_pass" if valid else "model_one_pass",
            "validation_passes": 2 if valid else 1,
            "verification": (
                "LLM group audit plus source recheck; not independent human verification"
                if valid else "unconfirmed candidate; not for style instructions"),
        })
    patterns.sort(key=lambda p: (p["status"] != "model_supported",
                                -p["support_work_count"], -p["observed_work_count"],
                                -p["support_occurrences"], p["dimension"],
                                p["mechanism"], p["trigger"]))
    for i, pattern in enumerate(patterns, 1):
        pattern["pattern_id"] = f"P{i:04d}"
    return patterns


def qualitative_guide(patterns: list[dict[str, Any]], *,
                      total_works: int, sampled_works: int, chunks: int,
                      synthesis: list[dict[str, Any]] | None = None,
                      max_per_dimension: int = 6) -> str:
    lines = [
        "# Qualitative Style Bible — LLM-assisted (private)", "",
        f"> Sampled {sampled_works}/{total_works} works and {chunks} chunks.",
        "> These are repeat observations in the sampled material, NOT author-specific",
        "> traits, ground truth, a frequency estimate for the complete corpus or",
        "> permission to copy source wording. Model review is not human validation.",
        "> Use as optional techniques for unrelated characters and new plots.", "",
    ]
    if synthesis:
        lines += ["## LLMによる横断的な作風のまとめ", ""]
        for section in synthesis:
            lines.append(f"- {JAPANESE_LABELS[section['dimension']]}: "
                         f"{section['summary']}")
            lines.append(f"  - 執筆時の応用: {section['writing_use']}"
                         f"（根拠: {', '.join(section['pattern_ids'])}）")
        lines.append("")
    any_supported = False
    for dimension in core.DIMENSIONS:
        rows = [r for r in patterns if r["dimension"] == dimension
                and r["status"] == "model_supported"]
        if not rows:
            continue
        any_supported = True
        lines += [f"## {JAPANESE_LABELS[dimension]}", ""]
        for row in rows[:max_per_dimension]:
            lines.append(
                f"- 場面条件: {row['trigger']} / 表現方法: {row['mechanism']} "
                f"/ 推定効果: {row['effect']} "
                f"({row['support_work_count']}作品の原文をモデルが再確認)")
            if dimension == "lexicon" and row["anchors"]:
                lines.append("- 複数作品で観測された短い表記: "
                             + "、".join(json.dumps(a, ensure_ascii=False)
                                          for a in row["anchors"]))
        lines.append("")
    if not any_supported:
        lines += ["複数作品で根拠を再検証できた定性的な特徴はありません。"
                  "未検証の仮説を執筆指示に転用しないでください。", ""]
    lines += ["## 解釈の限界", "",
              "- 感情・読者への効果はモデルによる推定で、読者実験ではない。",
              "- 共通ジャンル、類似作品、作者の異なる作品の混入を区別していない。",
              "- 実測頻度は style_profile.json と lexical_profile.json を参照する。",
              "- 題材・人物・固有の台詞や文章の長い連続を再利用しない。", ""]
    return "\n".join(lines)


def analyze(corpus: Path, out: Path, model: Any, *,
            analysis_works: int = 30, chunks_per_work: int = 2,
            chunk_chars: int = 3800, minimum_works: int = 2,
            max_groups: int = 16, verify_works: int = 4,
            cluster_batch_size: int = 30, cluster_rounds: int = 3,
            llm_workers: int = 1, retries: int = 2,
            progress_interval: float = 15.0, restart: bool = False,
            max_rules: int = 12) -> dict[str, Any]:
    if (analysis_works < 0 or chunks_per_work < 1 or chunk_chars < 1100 or
            minimum_works < 2 or max_groups < 0 or max_rules < 1 or
            verify_works < minimum_works or cluster_batch_size < 2 or
            cluster_rounds < 1 or llm_workers < 1 or retries < 0):
        raise ValueError("invalid LLM style analysis limits")
    works, jobs, full_chunks = sampled_jobs(
        corpus, analysis_works, chunks_per_work, chunk_chars)
    out.mkdir(parents=True, exist_ok=True)
    store = narrative.CheckpointStore(out / "style_llm_checkpoint.sqlite3", reset=restart)
    reporter = narrative.ProgressReporter(out, interval=progress_interval)
    reporter.status(state="running", mode="llm-first", stage="begin",
                    total_works=len(works), sampled_works=len({j[0] for j in jobs}),
                    selected_chunks=len(jobs))
    counts: dict[str, int] = {}
    cache: dict[str, int] = {}
    try:
        observations, chunks, counts["observations"], cache["observations"] = _observations(
            jobs, model, store, reporter, llm_workers, retries)
        reporter.log(f"LLM observations={len(observations)}")
        nodes, counts["clustering"], cache["clustering"] = _cluster(
            observations, model, store, reporter, llm_workers, retries,
            cluster_batch_size, cluster_rounds)
        by_id = {item["id"]: item for item in observations}
        chosen, reviewed, counts["group_review"], cache["group_review"] = _review_groups(
            nodes, by_id, model, store, reporter, llm_workers, retries,
            minimum_works, max_groups)
        verified, counts["source_check"], cache["source_check"] = _check_sources(
            chosen, reviewed, by_id, chunks, model, store, reporter, llm_workers,
            retries, minimum_works, verify_works)
        patterns = make_patterns(
            nodes, by_id, verified, len({j[0] for j in jobs}),
            len(works), minimum_works)
        accepted = [p for p in patterns if p["status"] == "model_supported"]
        synthesis, counts["synthesis"], cache["synthesis"] = _synthesize(
            patterns, model, store, reporter, llm_workers, retries)
        narrative_rules = []
        for row in (p for p in patterns if p["dimension"] == "narrative"):
            rule = {k: row[k] for k in (
                "status", "trigger", "mechanism", "effect", "observed_work_count",
                "support_work_count", "support_occurrences", "evidence_ids",
                "source", "validation_passes", "verification")}
            rule["rule_id"] = f"R{len(narrative_rules) + 1:03d}"
            rule["category"] = "structure"
            rule["coverage"] = row["corpus_observed_lower_bound"]
            narrative_rules.append(rule)
            if len(narrative_rules) >= max_rules:
                break
        audit = {
            "schema": "pastiche.llm_style.audit.v1", "mode": "llm-first",
            "total_works": len(works), "sampled_works": len({j[0] for j in jobs}),
            "selected_chunks": len(jobs), "full_chunks": full_chunks,
            "discovered_observations": len(observations),
            "semantic_groups": len(nodes),
            "cross_work_candidate_groups": sum(
                _work_count(n["members"], by_id) >= minimum_works for n in nodes),
            "reviewed_groups": len(chosen),
            "verified_groups": len([n for n in chosen
                                   if _work_count(verified.get(n["id"], []), by_id)
                                   >= minimum_works]),
            "supported_by_dimension": dict(Counter(
                row["dimension"] for row in accepted)),
            "summarized_dimensions": [s["dimension"] for s in synthesis],
            "actual_llm_calls_this_run": counts,
            "restored_checkpoint_entries": cache,
            "note": "Counts refer to sampled works; review is model-based, not human ground truth.",
        }
        # All exported content is abstract; the model saw raw scenes only transiently.
        narrative.atomic_json_write(out / "qualitative_observations.json", {
            "schema": core.SCHEMA, "observations": observations,
            "sampling_note": "Observations cover sampled chunks, not all corpus text."})
        narrative.atomic_json_write(out / "qualitative_patterns.json", {
            "schema": core.SCHEMA, "patterns": patterns,
            "sampled_works": audit["sampled_works"],
            "total_works": len(works),
            "limits": ["LLM-generated descriptions may be inaccurate",
                       "similar meaning does not establish identical technique",
                       "model source checks are not independent verification"]})
        narrative.atomic_json_write(out / "qualitative_summary.json", {
            "schema": core.SCHEMA,
            "sections": synthesis,
            "note": "LLM-written guidance supported only by model-screened pattern IDs."})
        narrative.atomic_json_write(out / "narrative_rules.json", {
            "schema": "pastiche.narrative.v2", "generator_version": core.VERSION,
            "mode": "llm-first-semantic", "works": audit["sampled_works"],
            "total_works": len(works), "rules": narrative_rules,
            "minimum_independent_works": minimum_works,
            "model_backend": model.backend, "model_name": model.model})
        narrative.atomic_json_write(out / "style_llm_audit.json", audit)
        narrative.atomic_json_write(out / "narrative_audit.json", audit)
        (out / "qualitative_bible.md").write_text(
            qualitative_guide(patterns, total_works=len(works),
                              sampled_works=audit["sampled_works"],
                              chunks=len(jobs), synthesis=synthesis), encoding="utf-8")
        reporter.status(state="completed", stage="finished",
                        completed_chunks=len(jobs),
                        actual_model_calls=sum(counts.values()))
        reporter.log(f"LLM style complete: {len(accepted)} supported patterns, "
                     f"calls={sum(counts.values())}")
        return audit
    except BaseException as exc:
        reporter.status(state="interrupted" if isinstance(exc, KeyboardInterrupt)
                        else "failed", stage="stopped")
        raise
    finally:
        store.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="LLM-first grounded narrative and stylistic analysis")
    p.add_argument("corpus", type=Path)
    p.add_argument("--out", type=Path, default=Path(".pastiche-private/profile"))
    p.add_argument("--backend", choices=("ollama", "openai-compatible"), required=True)
    p.add_argument("--endpoint", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--allow-remote", action="store_true")
    p.add_argument("--allow-insecure-http", action="store_true")
    p.add_argument("--api-key-env")
    p.add_argument("--no-stream", action="store_true")
    p.add_argument("--timeout", type=int, default=0)
    p.add_argument("--retries", type=int, default=2)
    p.add_argument("--llm-workers", type=int, default=1)
    p.add_argument("--progress-interval", type=float, default=15.0)
    p.add_argument("--chunk-chars", type=int, default=3800)
    p.add_argument("--analysis-works", type=int, default=30)
    p.add_argument("--chunks-per-work", type=int, default=2)
    p.add_argument("--cluster-batch-size", type=int, default=30)
    p.add_argument("--cluster-rounds", type=int, default=3)
    p.add_argument("--verify-groups", type=int, default=16)
    p.add_argument("--verify-works", type=int, default=4)
    p.add_argument("--min-works", type=int, default=2)
    p.add_argument("--max-rules", type=int, default=12)
    p.add_argument("--max-output-tokens", type=int, default=0)
    p.add_argument("--restart", action="store_true")
    args = p.parse_args(argv)
    try:
        model = narrative_fast.LimitedModel(
            args.backend, args.model, args.endpoint, args.timeout,
            args.allow_remote, args.allow_insecure_http, args.api_key_env,
            not args.no_stream, max_output_tokens=args.max_output_tokens)
        audit = analyze(
            args.corpus, args.out, model,
            analysis_works=args.analysis_works,
            chunks_per_work=args.chunks_per_work,
            chunk_chars=args.chunk_chars, minimum_works=args.min_works,
            max_groups=args.verify_groups, verify_works=args.verify_works,
            cluster_batch_size=args.cluster_batch_size,
            cluster_rounds=args.cluster_rounds,
            llm_workers=args.llm_workers, retries=args.retries,
            progress_interval=args.progress_interval, restart=args.restart,
            max_rules=args.max_rules)
        print(json.dumps(audit, ensure_ascii=False))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

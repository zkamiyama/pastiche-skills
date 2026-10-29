#!/usr/bin/env python3
"""LLM-first corpus analysis with validated, resumable qualitative synthesis.

Pipeline: sample -> scene observations -> LLM semantic grouping -> LLM group
audit -> source-evidence recheck -> private, provenance-labelled style guides.
Raw passages are passed only to the configured model and never persisted.
"""
from __future__ import annotations

import argparse
import execution
from task_transport import TasksPending
from collections import Counter
import json
from pathlib import Path
import sys
import threading
from typing import Any

import llm_style_core as core
import llm_runtime
import corpus as corpus_utils
from python_cluster import call_cluster
import prose_contracts as prose
from prose_runtime import call_prose
from prose_observe_recovery import call_observe


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
    works = corpus_utils.enumerated_corpus(corpus, 80)
    if not works:
        raise ValueError("no qualifying corpus")
    selected_idx = set(corpus_utils.stratified_indices(
        len(works), works_limit if works_limit else len(works)))
    selected = []
    jobs = []
    full_chunks = 0
    for work_index, (work_id, _path, units) in enumerate(works):
        chunks = corpus_utils.chunk_segments(units, work_id, chunk_chars)
        full_chunks += len(chunks)
        if work_index in selected_idx:
            selected.append(work_id)
            for position, chunk in corpus_utils.select_chunks(
                    chunks, chunks_per_work, work_index):
                jobs.append((work_id, position, chunk))
    return works, jobs, full_chunks


def _run_tasks(jobs: list[Any], model: Any, workers: int, task: Any,
               reporter: llm_runtime.ProgressReporter, stage: str,
               store: llm_runtime.CheckpointStore) -> tuple[dict[Any, Any], int, int]:
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
    for (logical_id, key, _payload, _validator), value in llm_runtime.model_jobs(
            pending, model, workers, work):
        store.put(key, {"value": value})
        ready[logical_id] = value
        calls += 1
        reporter.status(stage=stage, completed_tasks=len(ready),
                        total_tasks=len(jobs), cached_tasks=restored)
    return ready, calls, restored


def _observations(jobs: list[Any], model: Any,
                  store: llm_runtime.CheckpointStore,
                  reporter: llm_runtime.ProgressReporter,
                  workers: int, retries: int) -> tuple[list[dict[str, Any]], dict[Any, Any], int, int]:
    requests = []
    chunk_map = {}
    request_count = 0
    count_lock = threading.Lock()

    def count_request() -> None:
        nonlocal request_count
        with count_lock:
            request_count += 1
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
        return call_observe(work_id, passage, worker_model, reporter,
                            retries=retries, chunk_index=logical_id[1],
                            record_status=workers == 1, on_request=count_request)

    data, _completed_jobs, restored = _run_tasks(
        requests, model, workers, call, reporter, "STYLE_OBSERVE", store)
    all_items = []
    for work_id, position, chunk in jobs:
        for item in data.get((work_id, position), []):
            all_items.append(item | {
                "id": f"O{len(all_items) + 1:05d}",
                "work_id": work_id, "chunk_index": position,
            })
    return all_items, chunk_map, request_count, restored


def _initial_nodes(items: list[dict[str, Any]],
                   dimension: str) -> list[dict[str, Any]]:
    return [{
        "id": item["id"], "members": [item["id"]],
        "dimension": dimension, "trigger": item["trigger"],
        "technique": item["technique"], "effect": item["effect"],
        "label": "",
    } for item in items if item["dimension"] == dimension]


def _cluster(items: list[dict[str, Any]], model: Any,
             store: llm_runtime.CheckpointStore,
             reporter: llm_runtime.ProgressReporter,
             workers: int, retries: int,
             batch_size: int, rounds: int) -> tuple[list[dict[str, Any]], int, int]:
    all_nodes = []
    total_calls = 0
    total_cached = 0
    count_lock = threading.Lock()

    def count_request() -> None:
        nonlocal total_calls
        with count_lock:
            total_calls += 1

    for dimension in core.DIMENSIONS:
        nodes = _initial_nodes(items, dimension)
        for round_no in range(rounds):
            panels = core.cluster_panels(nodes, batch_size, round_no)
            if not panels:
                break
            requests = []
            for panel_index, panel in enumerate(panels):
                key = "cluster:" + core.digest(
                    "cluster-python-owned", {"contract": prose.VERSION, "dimension": dimension, "panel": panel}, model)
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
                return call_cluster(
                    dim, panel, worker_model, reporter, retries=retries,
                    round_number=round_no + 1, panel_number=panel_index + 1,
                    record_status=workers == 1, on_request=count_request)

            results, _completed_panels, cached = _run_tasks(
                requests, model, workers, call, reporter, "STYLE_CLUSTER", store)
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
                   store: llm_runtime.CheckpointStore,
                   reporter: llm_runtime.ProgressReporter,
                   workers: int, retries: int,
                   minimum_works: int, max_groups: int) -> tuple[list[dict[str, Any]], dict[str, list[str]], int, int]:
    candidates = [n for n in nodes if _work_count(n["members"], by_id) >= minimum_works]
    candidates.sort(key=lambda n: (-_work_count(n["members"], by_id),
                                  -len(n["members"]), n["dimension"], n["id"]))
    chosen = candidates[:max_groups]
    requests = []
    request_count = 0
    count_lock = threading.Lock()
    def counted():
        nonlocal request_count
        with count_lock: request_count += 1
    for node in chosen:
        selected, seen_works = [], set()
        for oid in node["members"]:
            work_id = by_id[oid]["work_id"]
            if work_id not in seen_works:
                selected.append(by_id[oid]); seen_works.add(work_id)
            if len(selected) >= 12: break
        selected_ids = {v["id"] for v in selected}
        for oid in node["members"]:
            if len(selected) >= 12: break
            if oid not in selected_ids:
                selected.append(by_id[oid]); selected_ids.add(oid)
        label = node["label"] or node["technique"]
        key = "group-review:" + core.digest("review-python-owned", {
            "contract": prose.VERSION, "label": label, "items": selected}, model)
        def validator(raw: Any, choices: set[str] = selected_ids) -> list[str]:
            if isinstance(raw, list): raw = {"compatible": bool(raw), "accepted_ids": raw}
            return core.parse_review(raw, choices)
        requests.append((node["id"], key, (node["dimension"], selected, label), validator))
    def call(payload: Any, worker_model: Any, logical_id: Any) -> Any:
        dimension, selected, label = payload
        accepted = []
        for observation in selected:
            verdict = call_prose(worker_model, reporter, kind="group-review",
                binding={"dimension": dimension, "observation": observation, "mechanism": label},
                prompt=prose.review_prompt(dimension, observation, label),
                parser=prose.parse_review, label=f"STYLE GROUP CHECK {logical_id}",
                retries=retries, on_request=counted)
            if verdict["compatible"]: accepted.append(observation["id"])
        if len(accepted) < 2 or _work_count(accepted, by_id) < minimum_works:
            return {"compatible": False, "accepted_ids": []}
        return {"compatible": True, "accepted_ids": accepted}
    reviewed, _jobs, cached = _run_tasks(requests, model, workers, call,
                                       reporter, "STYLE_GROUP_CHECK", store)
    return chosen, reviewed, request_count, cached


def _check_sources(chosen: list[dict[str, Any]], reviewed: dict[str, list[str]],
                   by_id: dict[str, dict[str, Any]], chunk_map: dict[Any, Any], model: Any,
                   store: llm_runtime.CheckpointStore, reporter: llm_runtime.ProgressReporter,
                   workers: int, retries: int, minimum_works: int,
                   verify_works: int) -> tuple[dict[str, list[str]], int, int]:
    requests = []; request_count = 0; count_lock = threading.Lock()
    def counted():
        nonlocal request_count
        with count_lock: request_count += 1
    for node in chosen:
        accepted = reviewed.get(node["id"], [])
        if _work_count(accepted, by_id) < minimum_works: continue
        selected, seen_works = [], set()
        for oid in accepted:
            observation = by_id[oid]
            if observation["work_id"] in seen_works: continue
            seen_works.add(observation["work_id"]); selected.append(observation)
            if len(selected) >= verify_works: break
        label = node["label"] or node["technique"]
        for observation in selected:
            chunk = chunk_map[(observation["work_id"], observation["chunk_index"])]
            key = "source-check:" + core.digest("source-python-owned", {
                "contract": prose.VERSION, "item": observation, "chunk": chunk, "label": label}, model)
            def validator(raw: Any, item: Any = observation, passage: Any = chunk) -> bool:
                if isinstance(raw, bool): return raw
                if not isinstance(raw, dict) or type(raw.get("supported")) is not bool:
                    raise ValueError("invalid Python-bound source verdict")
                return core.valid_evidence(raw, item, passage)
            requests.append(((node["id"], observation["id"]), key, (observation, chunk, label), validator))
    def call(payload: Any, worker_model: Any, logical_id: Any) -> Any:
        observation, chunk, label = payload
        verdict = call_prose(worker_model, reporter, kind="source-check",
            binding={"observation": observation, "chunk": chunk, "mechanism": label},
            prompt=prose.evidence_prompt(chunk, observation, label),
            parser=prose.evidence_verdict, cache_validator=prose.cached_evidence,
            label=f"STYLE EVIDENCE {logical_id[0]}", retries=retries, on_request=counted)
        return {**verdict, "evidence_ids": list(observation["evidence_ids"])}
    answers, _jobs, cached = _run_tasks(requests, model, workers, call,
                                       reporter, "STYLE_EVIDENCE", store)
    supported = {node["id"]: sorted(oid for (nid, oid), valid in answers.items()
                                    if nid == node["id"] and valid) for node in chosen}
    return supported, request_count, cached


def _synthesize(patterns: list[dict[str, Any]], model: Any,
                store: llm_runtime.CheckpointStore, reporter: llm_runtime.ProgressReporter,
                workers: int, retries: int) -> tuple[list[dict[str, Any]], int, int]:
    supported = [p for p in patterns if p["status"] == "model_supported"][:24]
    if not supported: return [], 0, 0
    request_count = 0; count_lock = threading.Lock()
    def counted():
        nonlocal request_count
        with count_lock: request_count += 1
    requests = []
    for pattern in supported:
        key = "synthesis:" + core.digest("synthesize-python-owned", {
            "contract": prose.VERSION, "pattern": pattern}, model)
        def validate(raw: Any, item: Any = pattern) -> dict:
            if (not isinstance(raw, dict) or raw.get("dimension") != item["dimension"]
                    or raw.get("pattern_ids") != [item["pattern_id"]]):
                raise ValueError("summary differs from Python-owned task binding")
            text = prose.parse_synthesis({k: raw.get(k) for k in ("summary", "writing_use")})
            return {**text, "dimension": item["dimension"], "pattern_ids": [item["pattern_id"]]}
        requests.append((pattern["pattern_id"], key, pattern, validate))
    def call(pattern: Any, worker_model: Any, logical_id: Any) -> Any:
        note = call_prose(worker_model, reporter, kind="synthesis", binding=pattern,
            prompt=prose.synthesis_prompt(pattern), parser=prose.parse_synthesis,
            label="STYLE SYNTHESIZE", retries=retries, on_request=counted)
        return {**note, "dimension": pattern["dimension"], "pattern_ids": [pattern["pattern_id"]]}
    results, _jobs, cached = _run_tasks(requests, model, workers, call,
                                      reporter, "STYLE_SYNTHESIZE", store)
    return [results[p["pattern_id"]] for p in supported], request_count, cached


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
            analysis_works: int = 0, chunks_per_work: int = 2,
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
    store = llm_runtime.CheckpointStore(out / "style_llm_checkpoint.sqlite3", reset=restart)
    reporter = llm_runtime.ProgressReporter(out, interval=progress_interval)
    reporter.reuse_prose_cache = not restart
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
            "model_interface": prose.VERSION,
            "reference_owner": "python",
            "observation_cache_reuse": "Prior validated observations may be reused; not newly quote-matched.",
            "clustering_method": "bounded greedy group comparison; at most three candidate groups per node; not exhaustive",
            "summary_binding": "one model-supported pattern per request; Python attaches the exact reference",
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
            "summarized_dimensions": sorted({s["dimension"] for s in synthesis}),
            "actual_llm_calls_this_run": counts,
            "restored_checkpoint_entries": cache,
            "note": "Counts refer to sampled works; review is model-based, not human ground truth.",
        }
        # All exported content is abstract; the model saw raw scenes only transiently.
        llm_runtime.atomic_json_write(out / "qualitative_observations.json", {
            "schema": core.SCHEMA, "observations": observations, "model_interface": prose.VERSION,
            "sampling_note": "Observations cover sampled chunks, not all corpus text."})
        llm_runtime.atomic_json_write(out / "qualitative_patterns.json", {
            "schema": core.SCHEMA, "patterns": patterns, "model_interface": prose.VERSION,
            "sampled_works": audit["sampled_works"],
            "total_works": len(works),
            "limits": ["LLM-generated descriptions may be inaccurate",
                       "similar meaning does not establish identical technique",
                       "model source checks are not independent verification"]})
        llm_runtime.atomic_json_write(out / "qualitative_summary.json", {
            "schema": "pastiche.qualitative.summary.v2",
            "sections": synthesis,
            "model_interface": prose.VERSION,
            "reference_scope": "one input pattern per writing note",
            "note": "LLM-written guidance supported only by model-screened pattern IDs."})
        llm_runtime.atomic_json_write(out / "narrative_rules.json", {
            "schema": "pastiche.narrative.v2", "generator_version": core.VERSION,
            "mode": "llm-first-semantic", "model_interface": prose.VERSION, "works": audit["sampled_works"],
            "total_works": len(works), "rules": narrative_rules,
            "minimum_independent_works": minimum_works,
            "model_backend": model.backend, "model_name": model.model})
        llm_runtime.atomic_json_write(out / "style_llm_audit.json", audit)
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
        reporter.status(state="waiting_for_tasks" if isinstance(exc, TasksPending) else
                        "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed", stage="stopped")
        raise
    finally:
        store.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="LLM-first grounded narrative and stylistic analysis")
    p.add_argument("corpus", type=Path)
    p.add_argument("--out", type=Path, default=Path(".pastiche-private/profile"))
    p.add_argument("--chunk-chars", type=int, default=3800)
    p.add_argument("--analysis-works", type=int, default=0,
                   help="0: all eligible works (default); positive N: limit works")
    p.add_argument("--chunks-per-work", type=int, default=2)
    p.add_argument("--cluster-batch-size", type=int, default=30)
    p.add_argument("--cluster-rounds", type=int, default=3)
    p.add_argument("--verify-groups", type=int, default=16)
    p.add_argument("--verify-works", type=int, default=4)
    p.add_argument("--min-works", type=int, default=2)
    p.add_argument("--max-rules", type=int, default=12)
    p.add_argument("--restart", action="store_true")
    execution.add_model_flags(p)
    args = p.parse_args(argv)
    try:
        model = execution.model_from(args)
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
    except TasksPending:
        return TasksPending.exit_code
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

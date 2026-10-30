"""Evidence-contract regression tests with synthetic passages only."""
from __future__ import annotations

from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/pastiche-profile/scripts"
sys.path.insert(0, str(SCRIPTS))

import llm_runtime
import llm_style
import llm_style_core as core
import prose_contracts as pc
from prose_runtime import call_prose, fingerprint

CHUNKS = {
    i: [(f"W{i:04d}:S0001", "古い時計が静かに刻む音を聞いた。"),
        (f"W{i:04d}:S0002", "冷たい硝子の感触が手に伝わった。")]
    for i in (1, 2)
}


def observation(i: int, dimension: str = "sensory") -> dict:
    return {
        "id": f"O{i:05d}", "work_id": f"W{i:04d}", "chunk_index": 1,
        "dimension": dimension,
        "trigger": "静かな部屋で物に触れる場面",
        "technique": "音と温度を対照して手触りを示す",
        "effect": "場面の距離感を触覚で伝える",
        "anchors": [], "evidence_ids": [eid for eid, _ in CHUNKS[i]]
    }


def reply(*, supported=True, basis="sensory", reason="二つの場面で同じ操作を確認する。"):
    return {"supported": supported, "basis": basis, "reason": reason}


class ScopedParserTests(unittest.TestCase):
    def test_non_narrative_prompt_does_not_request_basis(self):
        prompt = pc.evidence_prompt(CHUNKS[1], observation(1), "感覚の対比を使う")
        instructions = prompt.split("\nEVIDENCE=", 1)[0]
        self.assertIn('"supported":true|false', instructions)
        self.assertNotIn('"basis":', instructions)
        self.assertIn("TWO different selected passages", instructions)
        self.assertNotIn("W0001:S0001", prompt)

    def test_narrative_prompt_still_requires_actual_basis(self):
        prompt = pc.evidence_prompt(CHUNKS[1], observation(1, "narrative"), "因果の対照を使う")
        self.assertIn('"basis":', prompt.split("\nEVIDENCE=", 1)[0])

    def test_non_narrative_irrelevant_basis_does_not_block_grounded_verdict(self):
        for dimension in ("lexicon", "emotion", "voice", "dialogue", "sensory", "pacing"):
            with self.subTest(dimension=dimension):
                item = observation(1, dimension)
                verdict = pc.evidence_verdict(reply(), dimension=dimension)
                self.assertEqual(verdict, {"supported": True, "basis": "unclear"})
                self.assertTrue(core.valid_evidence(
                    {**verdict, "evidence_ids": item["evidence_ids"]},
                    item, CHUNKS[1]))

    def test_non_narrative_basis_may_be_absent(self):
        raw = reply()
        del raw["basis"]
        self.assertEqual(
            pc.evidence_verdict(raw, dimension="sensory"),
            {"supported": True, "basis": "unclear"})

    def test_false_and_uncertain_never_become_support(self):
        item = observation(1)
        verdict = pc.evidence_verdict(reply(supported=False), dimension="sensory")
        self.assertFalse(core.valid_evidence(
            {**verdict, "evidence_ids": item["evidence_ids"]}, item, CHUNKS[1]))
        for bad in ("true", "false", 1, None, "uncertain", {}):
            with self.subTest(bad_type=type(bad).__name__), self.assertRaises(pc.ProseError):
                pc.evidence_verdict(reply(supported=bad), dimension="sensory")

    def test_narrative_unlisted_basis_is_strictly_rejected(self):
        with self.assertRaises(pc.ProseError) as captured:
            pc.evidence_verdict(reply(), dimension="narrative")
        self.assertEqual(captured.exception.code, "source_basis_invalid")
        with self.assertRaises(pc.ProseError):
            pc.parse_evidence(reply())

    def test_narrative_temporal_only_is_not_promoted(self):
        item = observation(1, "narrative")
        verdict = pc.evidence_verdict(reply(basis="temporal_only"), dimension="narrative")
        self.assertFalse(core.valid_evidence(
            {**verdict, "evidence_ids": item["evidence_ids"]}, item, CHUNKS[1]))

    def test_missing_required_reason_or_extra_ids_remain_invalid(self):
        for raw in [dict(reply(), evidence_ids=["fabricated"]), {k:v for k,v in reply().items() if k!="reason"},
                    reply(reason="  ")]:
            with self.subTest(keys=list(raw)), self.assertRaises(pc.ProseError):
                pc.evidence_verdict(raw, dimension="sensory")

    def test_unknown_dimension_rejected(self):
        with self.assertRaises(ValueError):
            pc.evidence_verdict(reply(), dimension="invented")

    def test_task_fingerprint_and_safe_cache_are_preserved(self):
        class Model:
            backend = "fixture"
            model = "fixture"
            endpoint = "fixture"
            max_output_tokens = 4096
            def __init__(self, answers):
                self.answers = iter(answers)
                self.calls = []
            def ask(self, prompt):
                self.calls.append(prompt)
                return next(self.answers)

        with tempfile.TemporaryDirectory() as td, redirect_stderr(io.StringIO()):
            out = Path(td)
            reporter = llm_runtime.ProgressReporter(out, interval=0)
            binding = {"synthetic": "fixed"}
            parser = lambda raw: pc.evidence_verdict(raw, dimension="sensory")
            options = dict(kind="source-check", binding=binding, prompt="SYNTHETIC",
                           parser=parser, cache_validator=pc.cached_evidence,
                           label="STYLE EVIDENCE", retries=0, retry_delay=0)
            model = Model([reply(reason="PRIVATE_SENTINEL"*30)])
            first = call_prose(model, reporter, **options)
            self.assertEqual(first, {"supported": True, "basis": "unclear"})
            key = fingerprint("source-check", binding, model)
            self.assertTrue((out/"style_binding_cache"/f"{key}.json").is_file())
            saved = "".join(p.read_text() for p in out.rglob("*.json"))
            self.assertNotIn("PRIVATE_SENTINEL", saved)
            self.assertNotIn("sensory", saved)
            replay = Model([])
            self.assertEqual(call_prose(replay, reporter, **options), first)
            self.assertEqual(replay.calls, [])

    def test_pipeline_retains_completed_responses(self):
        class Model:
            backend = "fixture"
            model = "fixture"
            endpoint = "fixture"
            max_output_tokens = 4096
            def __init__(self, available):
                self.available = available
                self.calls = 0
            def ask(self, prompt):
                self.calls += 1
                if not self.available:
                    raise AssertionError("unexpected inference")
                return reply(reason="説明は保存しない。")

        observations = {f"O{i:05d}": observation(i) for i in (1, 2)}
        chunks = {(f"W{i:04d}", 1): CHUNKS[i] for i in (1, 2)}
        chosen = [{"id": "G", "label": "感覚を対照して表現する",
                   "members": list(observations)}]
        reviewed = {"G": list(observations)}
        with tempfile.TemporaryDirectory() as td, redirect_stderr(io.StringIO()):
            out = Path(td)
            reporter = llm_runtime.ProgressReporter(out, interval=0)
            store = llm_runtime.CheckpointStore(out/"main.sqlite3")
            try:
                m = Model(True)
                result, calls, cached = llm_style._check_sources(
                    chosen, reviewed, observations, chunks, m, store, reporter,
                    2, 0, 2, 4)
                self.assertEqual(result, {"G": list(observations)})
                self.assertEqual(calls, 2)
                self.assertEqual(cached, 0)
                second = Model(False)
                again, calls, cached = llm_style._check_sources(
                    chosen, reviewed, observations, chunks, second, store, reporter,
                    2, 0, 2, 4)
                self.assertEqual(again, result)
                self.assertEqual(calls, 0)
                self.assertEqual(cached, 2)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()

"""Synthetic checks for the write-side lexical audit. No source books or model calls."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/pastiche-write/scripts/evaluate_lexical.py"
spec = importlib.util.spec_from_file_location("pastiche_write_evaluate_lexical", SCRIPT)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def fixture() -> dict:
    return {
        "schema": "pastiche.lexical.v1", "works": 5, "total_characters": 10000,
        "symbols": [
            {"form": "♡", "count": 20, "work_count": 4,
             "contexts": {"dialogue": 18, "narration": 2, "terminal": 16}},
            {"form": "♪", "count": 6, "work_count": 2,
             "contexts": {"dialogue": 4, "narration": 2, "terminal": 3}},
        ],
        "variant_pairs": [
            {"variants": ["じゃねえか", "じゃあねえか"], "counts": [30, 10], "work_counts": [5, 2]},
            {"variants": ["来たっ！", "来たッ！"], "counts": [3, 12], "work_counts": [1, 4]},
            {"variants": ["あ", "ああ"], "counts": [1, 10], "work_counts": [1, 3]},
        ],
        "symbol_runs": [{"symbol": "♡", "repetitions": 3, "count": 10, "work_count": 3}],
        "mixed_sequences": [{"form": "♡♪", "count": 5, "work_count": 3}],
        "kaomoji_candidates": [{"form": "(・ω・)", "count": 7, "work_count": 4}],
        "sentence_endings": [{"form": "そうだ", "count": 18, "work_count": 4}],
    }


class WriteLexicalTests(unittest.TestCase):
    def test_exact_variants_are_separate(self):
        report = mod.compare(fixture(), "「じゃねえか。じゃあねえか。来たっ！来たッ！♡♡♡」")
        self.assertEqual(report["variant_pairs"][0]["draft_counts"], [1, 1])
        self.assertEqual(report["variant_pairs"][1]["draft_counts"], [1, 1])
        self.assertEqual(report["symbol_runs"][0]["draft_count"], 1)
        self.assertEqual(report["symbols"][0]["draft_count"], 3)
        self.assertEqual(report["symbols"][0]["draft_contexts"]["dialogue"], 3)

    def test_overlapping_variants_do_not_double_count(self):
        self.assertEqual(mod.variant_counts("あああ", "あ", "ああ"), [1, 1])

    def test_mixed_marks_and_kaomoji(self):
        report = mod.compare(fixture(), "「大丈夫♡♪ (・ω・)」")
        self.assertEqual(report["mixed_sequences"][0]["draft_count"], 1)
        self.assertEqual(report["kaomoji_candidates"][0]["draft_count"], 1)

    def test_sparse_draft_is_not_auto_rewritten(self):
        report = mod.compare(fixture(), "一面の海を眺める。")
        self.assertEqual(report["variant_pairs"][0]["assessment"], "insufficient_comparable_occurrences")
        self.assertEqual(report["symbols"][0]["draft_count"], 0)
        self.assertTrue(report["draft_is_short"])

    def test_cli_writes_private_aggregate_no_passages(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = root / "lexical_profile.json"
            draft = root / "new.txt"
            output = root / "review.json"
            profile.write_text(json.dumps(fixture(), ensure_ascii=False), encoding="utf-8")
            draft.write_text("「じゃねえか♡♡♡」\nきょうは静かだった。", encoding="utf-8")
            self.assertEqual(mod.main(["--profile", str(profile), "--draft", str(draft), "--out", str(output)]), 0)
            text = output.read_text(encoding="utf-8")
            self.assertIn("じゃねえか", text)
            self.assertNotIn("きょうは静かだった", text)
            self.assertEqual(json.loads(text)["schema"], "pastiche.lexical-evaluation.v1")

    def test_bad_schema_fails(self):
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "wrong.json"
            p.write_text('{"schema":"wrong"}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "schema"):
                mod.load_profile(p)


if __name__ == "__main__":
    unittest.main()

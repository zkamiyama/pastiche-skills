"""Synthetic tests for the LLM-first pipeline. No real corpus or network calls."""
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "pastiche-profile" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import llm_style
import llm_style_core as core
import pastiche


class FixtureModel:
    backend = "openai-compatible"
    model = "fixture"
    endpoint = "http://127.0.0.1:1/v1/chat/completions"

    def __init__(self, *, invalid_groups=False, no_groups=False,
                 reject_evidence=False, delay=0):
        self.calls = []
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0
        self.invalid_groups = invalid_groups
        self.no_groups = no_groups
        self.reject_evidence = reject_evidence
        self.delay = delay

    def ask(self, prompt):
        with self.lock:
            self.calls.append(prompt.split(".")[0])
            self.active += 1
            self.peak = max(self.active, self.peak)
        try:
            if self.delay:
                time.sleep(self.delay)
            if prompt.startswith("TASK=STYLE_OBSERVE"):
                evidence = re.findall(r"\[(W\d{4}:S\d{4,})\]", prompt)
                return {"observations": [
                    {"dimension": "narrative", "trigger": "既存の期待に矛盾する知らせが届く",
                     "technique": "説明を遅らせて別の手掛かりを先に示す",
                     "effect": "読者の当初の判断を後から更新させる",
                     "evidence_ids": evidence[:2], "anchors": []},
                    {"dimension": "emotion", "trigger": "人物が不意の出来事に直面する",
                     "technique": "沈黙と短い行動を交互に示して感情を匂わせる",
                     "effect": "説明文を増やさずにためらいを感じさせる",
                     "evidence_ids": evidence[:2], "anchors": []},
                    {"dimension": "lexicon", "trigger": "未知の出来事を表現するとき",
                     "technique": "一般的な動詞と短い助詞を反復して流れを作る",
                     "effect": "落ち着いた語りのテンポを保つ",
                     "evidence_ids": evidence[:2], "anchors": ["駅"]},
                ]}
            if prompt.startswith("TASK=STYLE_CLUSTER"):
                items = json.loads(prompt.split("ITEMS_JSON=", 1)[1])
                ids = [x["id"] for x in items]
                if self.invalid_groups:
                    ids += ["C999999"]
                if self.no_groups or len(ids) < 2:
                    return {"groups": []}
                return {"groups": [{
                    "ids": ids, "label": "抽象的な同一の描写操作を反復する"}]}
            if prompt.startswith("TASK=STYLE_GROUP_CHECK"):
                items = json.loads(prompt.split("ITEMS_JSON=", 1)[1])
                return {"compatible": True,
                        "accepted_ids": [x["id"] for x in items]}
            if prompt.startswith("TASK=STYLE_EVIDENCE"):
                evidence = re.findall(r"\[(W\d{4}:S\d{4,})\]", prompt)
                return {"supported": not self.reject_evidence,
                        "basis": "rhetorical",
                        "evidence_ids": evidence[:2]}
            raise AssertionError("unexpected prompt " + prompt[:100])
        finally:
            with self.lock:
                self.active -= 1


class LlmStyleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.corpus = self.root / "corpus"
        self.corpus.mkdir()
        self.out = self.root / "out"
        for i in range(6):
            source = (
                "早朝の駅で、友人は約束の時間を待っていた。"
                "いつもの列車が来なかった。\n"
                "駅に戻ると、知らない手紙が置かれていた。"
                "返事はまだ届かなかった。\n"
                "「どうして？」と友人は尋ねた。"
                "しかし理由は誰にも分からなかった。\n"
            ) * 48
            (self.corpus / f"fiction_{i:02d}.txt").write_text(source, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def analyze(self, model, **overrides):
        options = {
            "analysis_works": 6,
            "chunks_per_work": 1, "chunk_chars": 1800,
            "max_groups": 4, "verify_works": 2,
            "cluster_batch_size": 30, "cluster_rounds": 2,
            "llm_workers": 2, "retries": 0, "progress_interval": 0,
        }
        options.update(overrides)
        return llm_style.analyze(self.corpus, self.out, model, **options)

    def test_end_to_end_provenance_and_private_outputs(self):
        model = FixtureModel(delay=0.005)
        audit = self.analyze(model, llm_workers=3)
        self.assertGreater(model.peak, 1)
        self.assertLessEqual(model.peak, 3)
        self.assertEqual(audit["sampled_works"], 6)
        self.assertEqual(audit["selected_chunks"], 6)
        self.assertEqual(audit["discovered_observations"], 18)
        self.assertEqual(audit["verified_groups"], 3)
        self.assertEqual(audit["supported_by_dimension"]["emotion"], 1)
        self.assertTrue((self.out / "narrative_audit.json").exists())
        self.assertTrue((self.out / "qualitative_bible.md").exists())

        rules = json.loads((self.out / "narrative_rules.json").read_text())["rules"]
        self.assertEqual(rules[0]["status"], "model_supported")
        self.assertTrue(pastiche.is_guide_eligible(rules[0]))
        patterns = json.loads((self.out / "qualitative_patterns.json").read_text())["patterns"]
        self.assertEqual(sum(p["status"] == "model_supported" for p in patterns), 3)
        self.assertTrue(any(p["dimension"] == "lexicon" and p["anchors"] == ["駅"]
                            for p in patterns))
        artifacts = "\n".join(f.read_text(encoding="utf-8")
                              for f in self.out.glob("*.json"))
        self.assertNotIn("fiction_00", artifacts)
        self.assertNotIn("早朝の駅で、友人は約束の時間", artifacts)
        self.assertNotIn("いつもの列車が来なかった", artifacts)

        # The original guide builder must accept v2 provenance without a patch.
        p = subprocess.run(
            [sys.executable, str(SCRIPTS / "pastiche.py"), "profile",
             str(self.corpus), "--out", str(self.out)],
            capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        guide = subprocess.run(
            [sys.executable, str(SCRIPTS / "pastiche.py"), "guide",
             "--profile", str(self.out / "style_profile.json"),
             "--notes", str(self.out / "narrative_rules.json"),
             "--qualitative", str(self.out / "qualitative_bible.md"),
             "--out", str(self.out / "style_bible.md")],
            capture_output=True, text=True)
        self.assertEqual(guide.returncode, 0, guide.stderr)
        self.assertIn("情感の作り方",
                      (self.out / "style_bible.md").read_text(encoding="utf-8"))

    def test_resume_and_semantic_only_cache_invalidation(self):
        self.analyze(FixtureModel())
        cached = FixtureModel()
        replay = self.analyze(cached, llm_workers=4)
        self.assertEqual(replay["actual_llm_calls_this_run"]["observations"], 0)
        self.assertEqual(sum(replay["actual_llm_calls_this_run"].values()), 0)
        self.assertEqual(cached.calls, [])

        changed = self.analyze(FixtureModel(), cluster_batch_size=3)
        self.assertEqual(changed["actual_llm_calls_this_run"]["observations"], 0)
        self.assertGreater(changed["actual_llm_calls_this_run"]["clustering"], 0)

    def test_no_groups_are_reported_not_invented(self):
        audit = self.analyze(FixtureModel(no_groups=True))
        self.assertEqual(audit["cross_work_candidate_groups"], 0)
        self.assertEqual(audit["verified_groups"], 0)
        guide = (self.out / "qualitative_bible.md").read_text()
        self.assertIn("未検証の仮説を執筆指示に転用しない", guide)
        self.assertTrue(all(x["status"] == "hypothesis" for x in
                            json.loads((self.out / "qualitative_patterns.json").read_text())["patterns"]))

    def test_rejected_source_is_not_promoted(self):
        audit = self.analyze(FixtureModel(reject_evidence=True))
        self.assertEqual(audit["verified_groups"], 0)
        rules = json.loads((self.out / "narrative_rules.json").read_text())["rules"]
        self.assertTrue(all(x["status"] == "hypothesis" for x in rules))

    def test_invalid_cluster_ids_fail_and_do_not_corrupt_artifacts(self):
        with self.assertRaisesRegex(ValueError, "invented/duplicated IDs"):
            self.analyze(FixtureModel(invalid_groups=True))
        progress = json.loads((self.out / "narrative_progress.json").read_text())
        self.assertEqual(progress["state"], "failed")
        self.assertFalse((self.out / "qualitative_patterns.json").exists())


class ValidationTests(unittest.TestCase):
    def test_invalid_sources_and_verbatim_are_rejected(self):
        chunk = [
            ("W0001:S0001", "駅で昨日からずっと待ち続けていた。"),
            ("W0001:S0002", "やがて友人は手紙を見つけた。")]
        good = {"dimension": "narrative", "trigger": "人物が待機を続けている",
                "technique": "到着前に手掛かりを提示して先を予期させる",
                "effect": "読者が後続の展開に注意を向ける",
                "evidence_ids": ["W0001:S0001", "W0001:S0002"], "anchors": []}
        self.assertEqual(len(core.clean_observations(
            {"observations": [good]}, chunk)), 1)
        bogus = dict(good, evidence_ids=["W0001:S0001", "W9999:S0001"])
        with self.assertRaises(ValueError):
            core.clean_observations({"observations": [bogus]}, chunk)
        copied = dict(good, technique="駅で昨日からずっと待ち続けていた")
        with self.assertRaises(ValueError):
            core.clean_observations({"observations": [copied]}, chunk)

    def test_cluster_and_review_ids_are_strict(self):
        with self.assertRaisesRegex(ValueError, "invented/duplicated IDs"):
            core.parse_clusters({"groups": [
                {"ids": ["O00001", "O00001"], "label": "同じ操作を用いる"}]}, {"O00001"})
        with self.assertRaises(ValueError):
            core.parse_review(
                {"compatible": True, "accepted_ids": ["O00001", "O99999"]},
                {"O00001", "O00002"})
        with self.assertRaises(ValueError):
            core.cluster_panels([], 1, 0)

    def test_no_pyTorch_or_embedding_dependencies(self):
        self.assertNotIn("torch", core.__dict__)
        self.assertNotIn("onnxruntime", core.__dict__)


if __name__ == "__main__":
    unittest.main()

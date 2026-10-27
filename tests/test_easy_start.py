"""Smoke tests for the single-command onboarding path; no real model calls."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "pastiche-profile" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import run


class EasyStartTests(unittest.TestCase):
    def test_prefixed_openai_base_url(self):
        backend, endpoint, base, models = run.resolve_url("http://192.0.2.12/llm/v1", "auto")
        self.assertEqual(backend, "openai-compatible")
        self.assertEqual(endpoint, "http://192.0.2.12/llm/v1/chat/completions")
        self.assertEqual(base, "http://192.0.2.12/llm/v1")
        self.assertEqual(models, "http://192.0.2.12/llm/v1/models")

    def test_uses_model_id_from_matching_pi_config(self):
        with TemporaryDirectory() as temporary:
            home = Path(temporary)
            cfg = home / ".pi" / "agent" / "models.json"
            cfg.parent.mkdir(parents=True)
            cfg.write_text(json.dumps({"providers": {
                "chosen": {"baseUrl": "http://192.0.2.12/llm/v1", "api": "openai-completions",
                           "apiKey": "unused", "models": [{"id": "model-placeholder"}]},
                "other": {"baseUrl": "http://example.invalid/v1",
                          "models": [{"id": "not-selected"}]}
            }}), encoding="utf-8")
            with patch.object(Path, "home", return_value=home):
                self.assertEqual(run.model_id(None, "http://192.0.2.12/llm/v1",
                                               "openai-compatible",
                                               "http://192.0.2.12/llm/v1/models"), "model-placeholder")

    def test_check_sends_only_synthetic_request(self):
        with TemporaryDirectory() as temporary:
            missing = str(Path(temporary) / "not-a-corpus")
            with patch.object(run, "model_id", return_value="model-placeholder"), \
                 patch.object(run.narrative, "LocalModel") as model:
                model.return_value.ask.return_value = {"ok": True}
                result = run.main([missing, "--api-base", "http://127.0.0.1/llm/v1", "--check"])
                self.assertEqual(result, 0)
                assert model.return_value.ask.call_count == 1
                prompt = model.return_value.ask.call_args.args[0]
                self.assertIn("JSON", prompt)
                self.assertNotIn("not-a-corpus", prompt)

if __name__ == "__main__":
    unittest.main()

"""Synthetic observation retry/privacy/cache regressions; no private corpus."""
from __future__ import annotations
import copy
from contextlib import redirect_stderr
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import itertools
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/pastiche-profile/scripts"))
import corpus
import llm_runtime
import llm_style
import llm_style_core as core
from style_observe_recovery import call_observe, repair_prompt, CONTRACT_VERSION
from task_transport import SubagentModel, TasksPending, list_tasks, submit
from test_llm_style import FixtureModel


def chunk(work="W0001"):
    return [(work + ":S0001", "駅で昨日からずっと待ち続けていた。"),
            (work + ":S0002", "やがて友人は手紙を見つけた。")]


def good(work="W0001"):
    return {"dimension": "narrative", "trigger": "人物が待機を続けている",
            "technique": "到着前に手掛かりを提示して先を予期させる",
            "effect": "読者が後続の展開に注意を向ける",
            "evidence_ids": [work + ":S0001", work + ":S0002"], "anchors": []}


def reply(work="W0001"):
    return {"observations": [good(work)]}


def bad():
    return {"observations": [dict(good(), evidence_ids=["W0001:S0001", "PRIVATE_UNKNOWN"])]}


class Replies:
    backend = "fixture"
    model = "fixture"
    endpoint = "fixture"
    def __init__(self, replies):
        self.replies = iter(replies)
        self.prompts = []
    def ask(self, prompt):
        self.prompts.append(prompt)
        value = next(self.replies)
        if isinstance(value, BaseException):
            raise value
        return copy.deepcopy(value)


def legacy_clean(value, passage):
    # Frozen pre-repair contract as an independent regression oracle.
    if not isinstance(value, dict) or not isinstance(value.get("observations"), list):
        raise ValueError()
    allowed = {i for i, _ in passage}
    source = "".join(t for _, t in passage)
    kept = []
    for obj in value["observations"][:7]:
        if not isinstance(obj, dict) or obj.get("dimension") not in core.DIMENSIONS:
            continue
        fields = [obj.get(k) for k in ("trigger", "technique", "effect")]
        if not all(isinstance(v, str) and 4 <= len(v) <= 110 and v == v.strip()
                   and "\n" not in v and "\r" not in v for v in fields):
            continue
        if any(corpus._is_verbatim_fragment(v, passage) for v in fields):
            continue
        ids = obj.get("evidence_ids")
        if not isinstance(ids, list) or len(ids) < 2:
            continue
        if not all(isinstance(i, str) and i in allowed for i in ids) or len(set(ids)) < 2:
            continue
        anchors = obj.get("anchors", [])
        if not isinstance(anchors, list) or len(anchors) > 2:
            continue
        if obj["dimension"] != "lexicon":
            anchors = []
        if not all(isinstance(a, str) and 1 <= len(a) <= 12 and a in source
                   and not any(c.isspace() for c in a) for a in anchors):
            continue
        kept.append({k: obj[k] for k in ("dimension", "trigger", "technique", "effect")} |
                    {"evidence_ids": sorted(set(ids)), "anchors": list(dict.fromkeys(anchors))})
    if value["observations"] and not kept:
        raise ValueError()
    return kept


class ObservationContractTests(unittest.TestCase):
    def test_specific_reasons_and_private_values_are_not_echoed(self):
        cases = [
            ([], "observations_array_required"),
            ({"observations": ["PRIVATE_REJECTED"]}, "observation_object_required"),
            ({"observations": [dict(good(), dimension="PRIVATE_REJECTED")]}, "unknown_dimension"),
            ({"observations": [dict(good(), trigger=None)]}, "description_string_required"),
            ({"observations": [dict(good(), effect="短い語")]}, "description_length_4_to_110"),
            ({"observations": [dict(good(), technique=" leading space")]}, "description_outer_whitespace"),
            ({"observations": [dict(good(), technique="line\nbreak")]}, "description_single_line_required"),
            ({"observations": [dict(good(), technique=chunk()[0][1])]}, "source_verbatim_overlap"),
            ({"observations": [dict(good(), evidence_ids=None)]}, "evidence_array_required"),
            ({"observations": [dict(good(), evidence_ids=["W0001:S0001"])]}, "two_distinct_evidence_ids_required"),
            ({"observations": [dict(good(), evidence_ids=["W0001:S0001"]*2)]}, "two_distinct_evidence_ids_required"),
            (bad(), "unknown_or_invalid_evidence_id"),
            ({"observations": [dict(good(), evidence_ids=[{}, "W0001:S0001"])]}, "unknown_or_invalid_evidence_id"),
            ({"observations": [dict(good(), anchors=None)]}, "anchors_array_max_two"),
            ({"observations": [dict(good(), dimension="lexicon", anchors=["PRIVATE_REJECTED"])]}, "anchor_not_exact_short_source_form"),
        ]
        for value, code in cases:
            with self.subTest(code=code), self.assertRaises(core.ObservationValidationError) as cm:
                core.clean_observations(value, chunk())
            detail = cm.exception.diagnostic()
            self.assertIn(code, [i["code"] for i in detail["issues"]])
            self.assertNotIn("PRIVATE", str(cm.exception) + json.dumps(detail))
            self.assertNotIn(chunk()[0][1], json.dumps(detail, ensure_ascii=False))

    def test_partial_acceptance_deduplicates_and_records_rejection(self):
        valid = dict(good(), evidence_ids=["W0001:S0002", "W0001:S0001", "W0001:S0001"])
        accepted, info = core.check_observations({"observations": [bad()["observations"][0], valid]}, chunk())
        self.assertEqual(accepted, [good()])
        self.assertEqual((info["accepted_count"], info["rejected_count"]), (1, 1))
        self.assertEqual(info["issues"][0]["item_index"], 1)

    def test_empty_initial_reply_is_distinct_from_all_rejected(self):
        self.assertEqual(core.clean_observations({"observations": []}, chunk()), [])
        with self.assertRaises(core.ObservationValidationError):
            core.clean_observations(bad(), chunk())

    def test_seven_item_bound_and_ignored_count_are_preserved(self):
        accepted, info = core.check_observations({"observations": [good()]*9}, chunk())
        self.assertEqual(len(accepted), 7)
        self.assertEqual(info["ignored_count"], 2)
        with self.assertRaises(core.ObservationValidationError):
            core.clean_observations({"observations": bad()["observations"]*7+[good()]}, chunk())

    def test_description_boundaries_and_anchor_unicode(self):
        for text in ("四文字語", "界"*110):
            self.assertEqual(len(core.clean_observations({"observations": [dict(good(), trigger=text)]}, chunk())), 1)
        for text in ("三文字", "界"*111, " line", "line ", "line\nbreak", "line\rbreak"):
            with self.subTest(text=text), self.assertRaises(core.ObservationValidationError):
                core.clean_observations({"observations": [dict(good(), effect=text)]}, chunk())
        p = chunk() + [("W0001:S0003", "合成の記号♡を含む行。")]
        v = dict(good(), dimension="lexicon", anchors=["♡", "♡"])
        self.assertEqual(core.clean_observations({"observations": [v]}, p)[0]["anchors"], ["♡"])
        v["anchors"] = ["♥"]
        with self.assertRaises(core.ObservationValidationError):
            core.clean_observations({"observations": [v]}, p)

    def test_differential_contract_over_synthetic_values(self):
        dims = ["narrative", "lexicon", "PRIVATE", [], None]
        descriptions = ["normal abstract", "abc", "a"*111, None, " padded ", "line\nbreak", chunk()[0][1]]
        evidence = [["W0001:S0001", "W0001:S0002"], ["W0001:S0001"]*2, ["unknown", {}], None]
        anchors = [[], ["駅"], ["PRIVATE"], [None], ["x"]*3, None]
        checked = 0
        for dim, text, ids, anchor in itertools.product(dims, descriptions, evidence, anchors):
            v = {"observations": [dict(good(), dimension=dim, technique=text, evidence_ids=ids, anchors=anchor)]}
            try:
                expected = legacy_clean(v, chunk())
            except ValueError:
                with self.assertRaises(core.ObservationValidationError):
                    core.clean_observations(v, chunk())
            else:
                self.assertEqual(core.clean_observations(v, chunk()), expected)
            checked += 1
        self.assertEqual(checked, 840)

    def test_prompt_declares_literal_dimensions_and_single_line(self):
        text = core.observe_prompt("W0001", chunk())
        self.assertIn("dimension values: narrative, lexicon, emotion, voice, dialogue, sensory, pacing", text)
        self.assertIn("no leading or trailing whitespace", text)

    def test_cache_identity_and_version_are_unchanged(self):
        for effort in ("auto", "none"):
            m = llm_runtime.HTTPModel("openai-compatible", "fixture", "http://127.0.0.1:1/v1", reasoning_effort=effort)
            old = {"version": "1.0.1", "stage": "observe", "data": chunk(),
                   "model": m.model, "backend": m.backend, "endpoint": m.endpoint}
            if effort == "none":
                old["reasoning"] = {"reasoning_effort": "none", "chat_template_kwargs": {"enable_thinking": False}}
            expected = hashlib.sha256(json.dumps(old, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            self.assertEqual(core.digest("observe", chunk(), m), expected)


class ObservationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        self.reporter = llm_runtime.ProgressReporter(self.out, interval=0)
    def tearDown(self):
        self.tmp.cleanup()
    def invoke(self, model, retries=2, **kwargs):
        with redirect_stderr(io.StringIO()):
            return call_observe("W0001", chunk(), model, self.reporter, retries=retries,
                                chunk_index=1, retry_delay=0, **kwargs)
    def diagnostic(self):
        paths = list((self.out / "style_observe_diagnostics").glob("*.json"))
        self.assertEqual(len(paths), 1)
        return json.loads(paths[0].read_text())

    def test_invalid_result_gets_bounded_corrective_retry(self):
        model = Replies([bad(), reply()])
        self.assertEqual(self.invoke(model), reply())
        self.assertEqual(len(model.prompts), 2)
        self.assertIn("unknown_or_invalid_evidence_id", model.prompts[1])
        self.assertNotIn("PRIVATE_UNKNOWN", model.prompts[1])
        self.assertEqual(model.prompts[0].split("\nDATA ", 1)[1], model.prompts[1].split("\nDATA ", 1)[1])
        self.assertEqual([a["outcome"] for a in self.diagnostic()["attempts"]], ["invalid", "valid"])

    def test_all_rejections_exhaust_one_budget(self):
        model = Replies([bad()]*3)
        with self.assertRaises(core.ObservationValidationError):
            self.invoke(model)
        self.assertEqual(len(model.prompts), 3)
        self.assertEqual(self.diagnostic()["state"], "failed")

    def test_retries_zero_does_not_make_extra_call(self):
        model = Replies([bad()])
        with self.assertRaises(core.ObservationValidationError):
            self.invoke(model, retries=0)
        self.assertEqual(len(model.prompts), 1)

    def test_transport_and_schema_share_the_same_budget(self):
        model = Replies([llm_runtime.ModelRequestError("PRIVATE_ERROR"), bad(), reply()])
        calls = []
        self.assertEqual(self.invoke(model, on_request=lambda: calls.append(1)), reply())
        self.assertEqual(len(calls), 3)
        self.assertEqual([a["outcome"] for a in self.diagnostic()["attempts"]], ["model_error", "invalid", "valid"])
        self.assertNotIn("PRIVATE_ERROR", json.dumps(self.diagnostic()))
        self.assertNotIn("PRIVATE_ERROR", model.prompts[-1])

    def test_transport_error_does_not_reset_prior_validation_failure(self):
        model = Replies([bad(), llm_runtime.ModelRequestError("synthetic"), {"observations": []}])
        with self.assertRaisesRegex(core.ObservationValidationError, "empty_after_validation_failure"):
            self.invoke(model)
        self.assertEqual(len(model.prompts), 3)

    def test_nonretryable_transport_error_stops_immediately(self):
        model = Replies([llm_runtime.ModelRequestError("synthetic", transient=False)])
        with self.assertRaises(llm_runtime.ModelRequestError):
            self.invoke(model)
        self.assertEqual(len(model.prompts), 1)
        self.assertEqual(self.diagnostic()["state"], "failed")

    def test_partial_acceptance_is_not_retried_or_lost(self):
        model = Replies([{"observations": bad()["observations"]+[good()]}])
        self.assertEqual(self.invoke(model), reply())
        self.assertEqual(len(model.prompts), 1)
        self.assertEqual(self.diagnostic()["attempts"][0]["rejected_count"], 1)

    def test_genuine_empty_initial_answer_is_accepted(self):
        self.assertEqual(self.invoke(Replies([{"observations": []}])), {"observations": []})
        self.assertEqual(self.diagnostic()["state"], "completed")

    def test_empty_repair_is_not_a_false_success(self):
        model = Replies([bad(), {"observations": []}, {"observations": []}])
        with self.assertRaisesRegex(core.ObservationValidationError, "empty_after_validation_failure"):
            self.invoke(model)
        self.assertEqual(self.diagnostic()["state"], "failed")

    def test_empty_repair_can_be_followed_by_real_valid_observations(self):
        model = Replies([bad(), {"observations": []}, reply()])
        self.assertEqual(self.invoke(model), reply())
        self.assertEqual(len(model.prompts), 3)

    def test_missing_array_repair_cannot_become_empty_success(self):
        with self.assertRaisesRegex(core.ObservationValidationError, "empty_after_validation_failure"):
            self.invoke(Replies([{}, {"observations": []}]), retries=1)

    def test_diagnostics_include_no_source_or_response_strings(self):
        model = Replies([bad(), reply()])
        log = io.StringIO()
        with redirect_stderr(log):
            call_observe("W0001", chunk(), model, self.reporter, retries=2, chunk_index=1, retry_delay=0)
        output = log.getvalue() + json.dumps(self.diagnostic(), ensure_ascii=False)
        for marker in ("PRIVATE_UNKNOWN", chunk()[0][1], good()["trigger"], good()["technique"], good()["effect"]):
            self.assertNotIn(marker, output)

    def test_unknown_identifiers_fail_before_any_request(self):
        for work, position in [("../private", 1), ("W0001", 0), ("W0001", True)]:
            model = Replies([])
            with self.subTest(work=work, position=position), self.assertRaises(ValueError):
                call_observe(work, chunk(), model, self.reporter, retries=2, chunk_index=position)
            self.assertEqual(model.prompts, [])

    def test_keyboard_interrupt_is_not_reported_as_completed(self):
        with self.assertRaises(KeyboardInterrupt):
            self.invoke(Replies([KeyboardInterrupt()]))
        self.assertEqual(self.diagnostic()["state"], "interrupted")

    def test_subagent_repair_is_deterministic_and_resume_validated(self):
        m = SubagentModel(self.out / "queue", "fixture")
        with self.assertRaises(TasksPending):
            self.invoke(m)
        self.assertEqual(self.diagnostic()["state"], "waiting_for_tasks")
        first = list_tasks(m.task_dir)[0]["task_id"]
        submit(m.task_dir, first, bad())
        with self.assertRaises(TasksPending):
            self.invoke(m)
        pending = [t for t in list_tasks(m.task_dir) if t["state"] == "pending"]
        self.assertEqual(len(pending), 1)
        second = pending[0]["task_id"]
        with self.assertRaises(TasksPending):
            self.invoke(m)
        self.assertEqual([t["task_id"] for t in list_tasks(m.task_dir) if t["state"] == "pending"], [second])
        submit(m.task_dir, second, reply())
        self.assertEqual(self.invoke(m), reply())
        self.assertEqual(self.diagnostic()["state"], "completed")


class ObservationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        self.store = llm_runtime.CheckpointStore(self.out / "cache.sqlite3")
        self.reporter = llm_runtime.ProgressReporter(self.out, interval=0)
    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_existing_valid_cache_is_used_without_model_calls(self):
        m = Replies([])
        key = "scene:" + core.digest("observe", chunk(), m)
        self.store.put(key, {"value": [good()]})
        with redirect_stderr(io.StringIO()):
            items, _, calls, restored = llm_style._observations([("W0001", 1, chunk())], m, self.store, self.reporter, 4, 2)
        self.assertEqual((len(items), calls, restored), (1, 0, 1))
        self.assertEqual(m.prompts, [])

    def test_actual_request_count_includes_repair_and_cache_is_sanitized(self):
        m = Replies([bad(), reply()])
        with redirect_stderr(io.StringIO()):
            items, _, calls, restored = llm_style._observations([("W0001", 1, chunk())], m, self.store, self.reporter, 4, 2)
        self.assertEqual((len(items), calls, restored), (1, 2, 0))
        key = "scene:" + core.digest("observe", chunk(), m)
        self.assertEqual(self.store.get(key), {"value": [good()]})
        self.assertNotIn("PRIVATE_UNKNOWN", json.dumps(self.store.get(key)))

    def test_exhaustion_does_not_cache_an_empty_result(self):
        m = Replies([bad()]*3)
        with redirect_stderr(io.StringIO()), self.assertRaises(core.ObservationValidationError):
            llm_style._observations([("W0001", 1, chunk())], m, self.store, self.reporter, 1, 2)
        self.assertIsNone(self.store.get("scene:" + core.digest("observe", chunk(), m)))

    def test_parallel_successes_are_drained_before_failure(self):
        barrier = threading.Barrier(4)
        class Concurrent(Replies):
            def ask(self, prompt):
                import re
                work = re.search(r"\nDATA (W[0-9]+):", prompt).group(1)
                barrier.wait(timeout=5)
                if work == "W0001":
                    return bad()
                time.sleep(0.02)
                return reply(work)
        m = Concurrent([])
        jobs = [(f"W{i:04d}", 1, chunk(f"W{i:04d}")) for i in range(1, 5)]
        with redirect_stderr(io.StringIO()), self.assertRaises(core.ObservationValidationError):
            llm_style._observations(jobs, m, self.store, self.reporter, 4, 0)
        for work, _, passage in jobs:
            key = "scene:" + core.digest("observe", passage, m)
            self.assertEqual(self.store.get(key), None if work == "W0001" else {"value": [good(work)]})

    def test_loopback_http_off_controls_and_corrective_retry(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(body)
                value = bad() if len(requests) == 1 else reply()
                payload = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
        try:
            m = llm_runtime.HTTPModel("openai-compatible", "fixture", f"http://127.0.0.1:{srv.server_port}/v1", stream=False)
            with redirect_stderr(io.StringIO()):
                result = call_observe("W0001", chunk(), m, self.reporter, retries=2, chunk_index=1, retry_delay=0)
            self.assertEqual(result, reply())
            self.assertEqual(len(requests), 2)
            for r in requests:
                self.assertEqual(r["reasoning_effort"], "none")
                self.assertFalse(r["chat_template_kwargs"]["enable_thinking"])
            self.assertIn(CONTRACT_VERSION, requests[1]["messages"][1]["content"])
        finally:
            srv.shutdown(); srv.server_close(); t.join(3)

    def test_end_to_end_repair_has_no_blank_success_and_resumes(self):
        directory = self.out / "corpus"; directory.mkdir()
        for i in range(3):
            (directory / f"sample{i}.txt").write_text("駅で昨日から待ち続けていた。\nやがて友人は手紙を見つけた。\n"*8, encoding="utf-8")
        class RepairFixture(FixtureModel):
            def __init__(self):
                super().__init__(); self.broken = False
            def ask(self, prompt):
                if prompt.startswith("TASK=STYLE_OBSERVE"):
                    with self.lock:
                        if not self.broken:
                            self.broken = True
                            return bad()
                return super().ask(prompt)
        out = self.out / "profile"
        with redirect_stderr(io.StringIO()):
            audit = llm_style.analyze(directory, out, RepairFixture(), chunks_per_work=1, retries=2,
                                      progress_interval=0, llm_workers=4)
        self.assertEqual(audit["actual_llm_calls_this_run"]["observations"], 4)
        self.assertGreater(audit["discovered_observations"], 0)
        self.assertEqual(json.loads((out/"llm_progress.json").read_text())["state"], "completed")
        with redirect_stderr(io.StringIO()):
            second = llm_style.analyze(directory, out, FixtureModel(), chunks_per_work=1, retries=2,
                                      progress_interval=0, llm_workers=4)
        self.assertEqual(sum(second["actual_llm_calls_this_run"].values()), 0)


if __name__ == "__main__":
    unittest.main()

"""Bounded observation repair with safe diagnostics and unchanged cache keys.

Do not retain raw replies/source text or weaken evidence checks to get a result.
Transport and domain-validation failures share one request-attempt budget.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

import llm_runtime
import llm_style_core as core
import prose_contracts as prose
from task_transport import TasksPending

CONTRACT_VERSION = "observe-prose.v1"


def repair_prompt(work_id: str, chunk: list[tuple[str, str]],
                  feedback: dict[str, Any] | None, attempt: int) -> str:
    base = prose.observation_prompt(chunk)
    if feedback is None:
        return base
    prefix, data = base.split("\nPASSAGES=", 1)
    correction = (
        f" OUTPUT_REPAIR={CONTRACT_VERSION}; request_attempt={attempt}. "
        "The previous reply failed validation. VALIDATION_ERROR="
        + json.dumps(feedback, ensure_ascii=True, sort_keys=True)
        + ". Re-examine the same passages. Describe writing operations in prose, "
        "not IDs. Ground each observation with two to four EXACT short substrings "
        "from at least TWO DISTINCT passages. Each substring must locate only one "
        "passage. Never invent or paraphrase quotes. Python maps them to source "
        "references. Keep descriptions abstract, 4-110 characters on one line. "
        "Do not return an empty list just to conceal a failed validation."
    )
    return prefix + correction + "\nPASSAGES=" + data


def call_observe(work_id: str, chunk: list[tuple[str, str]], model: Any,
                 reporter: llm_runtime.ProgressReporter, *, retries: int,
                 chunk_index: int, record_status: bool = True,
                 retry_delay: float = 2.0,
                 on_request: Callable[[], None] | None = None) -> dict:
    if type(retries) is not int or retries < 0:
        raise ValueError("observe retries must be a nonnegative integer")
    if (not isinstance(work_id, str) or re.fullmatch(r"W[0-9]{4,}", work_id) is None
            or type(chunk_index) is not int or chunk_index < 1 or len(chunk) < 2):
        raise ValueError("invalid observation work/chunk identifier")
    fingerprint = core.digest("observe", chunk, model)
    diagnostic_path = reporter.out / "style_observe_diagnostics" / f"{fingerprint}.json"
    attempts: list[dict[str, Any]] = []
    label = f"STYLE OBSERVE {work_id} chunk {chunk_index}"
    feedback = None
    had_validation_failure = False

    def record(state: str) -> None:
        llm_runtime.atomic_json_write(diagnostic_path, {
            "schema": "pastiche.observe.diagnostic.v1", "contract": CONTRACT_VERSION,
            "work_id": work_id, "chunk_index": chunk_index, "segment_count": len(chunk),
            "maximum_attempts": retries + 1, "state": state, "attempts": attempts,
            "note": "Latest invocation; subagent responses can be replayed. "
                    "Only fixed codes, indices and counts; no source, raw replies or deployment identifiers.",
        })

    record("running")
    try:
        for index in range(retries + 1):
            attempt = index + 1
            prompt = repair_prompt(work_id, chunk, feedback, attempt)
            try:
                if on_request is not None:
                    on_request()
                raw = reporter.call(model, prompt, f"{label} request={attempt}/{retries + 1}",
                                    retries=0, record_status=record_status)
            except llm_runtime.ModelRequestError as exc:
                feedback = {"code": "model_transport_or_json_error"}
                attempts.append({"attempt": attempt, "outcome": "model_error", **feedback})
                record("retrying" if index < retries and exc.transient else "failed")
                if index >= retries or not exc.transient:
                    raise
                reporter.log(f"retrying {label}: {feedback['code']}")
                if retry_delay:
                    time.sleep(min(retry_delay * (2 ** index), 30.0))
                continue
            try:
                accepted, diagnostics = prose.bind_observations(raw, chunk)
                if had_validation_failure and not accepted:
                    diagnostics["issues"].append({"code": "empty_after_validation_failure"})
                    raise core.ObservationValidationError("empty_after_validation_failure", diagnostics)
            except core.ObservationValidationError as exc:
                had_validation_failure = True
                feedback = exc.diagnostic()
                attempts.append({"attempt": attempt, "outcome": "invalid", **feedback})
                reporter.log(f"invalid {label}: {exc.code}; "
                             f"issues={json.dumps(feedback['issues'], sort_keys=True)}")
                record("retrying" if index < retries else "failed")
                if index >= retries:
                    reporter.log("Observation recovery exhausted. Keep the checkpoint; rerun "
                                 "without --restart. Diagnostics: style_observe_diagnostics/")
                    raise
                reporter.log(f"repairing {label}: next request={attempt + 1}/{retries + 1}")
                continue
            attempts.append({"attempt": attempt, "outcome": "valid", **diagnostics})
            record("completed")
            reporter.log(f"validated {label}: observations={len(accepted)} "
                         f"rejected={diagnostics['rejected_count']}")
            return {"observations": accepted}
    except TasksPending:
        record("waiting_for_tasks")
        raise
    except BaseException as exc:
        record("interrupted" if isinstance(exc, KeyboardInterrupt) else "failed")
        raise
    raise AssertionError("unreachable observation retry state")

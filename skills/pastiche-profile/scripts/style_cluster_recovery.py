"""Bounded cluster repair without weakening evidence or invalidating observations.

Only fixed error codes and indices are persisted: no raw model answers, labels,
source passages, deployment names or endpoints. Each retry is a complete new
partition proposal, checked with the same strict validator as the first reply.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable

import llm_runtime
import llm_style_core as core
from task_transport import TasksPending

CONTRACT_VERSION = "cluster-repair.v1"


def repair_prompt(dimension: str, panel: list[dict[str, Any]],
                  feedback: dict[str, Any] | None, attempt: int) -> str:
    base = core.cluster_prompt(dimension, panel)
    if feedback is None:
        return base
    # Insert before ITEMS_JSON so that the document still ends in its input
    # array; never feed arbitrary rejected text back as corrective instructions.
    prefix, data = base.split("ITEMS_JSON=", 1)
    correction = (
        f"OUTPUT_REPAIR={CONTRACT_VERSION}; request_attempt={attempt}. "
        "Your previous reply failed the output contract. VALIDATION_ERROR="
        + json.dumps(feedback, ensure_ascii=True, sort_keys=True)
        + ". Regenerate the COMPLETE groups array from the supplied items. "
        "Use only exact supplied IDs, each at most once in the entire reply. "
        "Omit singletons; never force unrelated items into groups. Do not "
        "drop a legitimate group just to hide a format error. Labels must "
        "be 4-140 characters, single-line, without outer whitespace. "
        "Do not return commentary, explanations or Markdown. ITEMS_JSON="
    )
    return prefix + correction + data


def call_cluster(dimension: str, panel: list[dict[str, Any]], model: Any,
                 reporter: llm_runtime.ProgressReporter, *, retries: int,
                 round_number: int, panel_number: int,
                 record_status: bool = True, retry_delay: float = 2.0,
                 on_request: Callable[[], None] | None = None) -> dict:
    """One shared (1 + retries) budget for transport and schema failures.

    A subagent suspension does not consume attempts: replay consumes already
    answered tasks, then emits the deterministically derived repair task.
    Unknown IDs, overlapping membership and invalid labels never enter cache.
    """
    if type(retries) is not int or retries < 0:
        raise ValueError("cluster retries must be a nonnegative integer")
    if dimension not in core.DIMENSIONS or len(panel) < 2:
        raise ValueError("invalid clustering panel")
    allowed = {item["id"] for item in panel}
    if len(allowed) != len(panel):
        raise ValueError("duplicate input panel IDs")
    fingerprint = core.digest("cluster", {"dimension": dimension, "panel": panel}, model)
    diagnostic_path = reporter.out / "style_cluster_diagnostics" / f"{fingerprint}.json"
    attempts: list[dict[str, Any]] = []
    label = f"STYLE CLUSTER {dimension} round={round_number} panel={panel_number}"

    def record(state: str) -> None:
        llm_runtime.atomic_json_write(diagnostic_path, {
            "schema": "pastiche.cluster.diagnostic.v1", "contract": CONTRACT_VERSION,
            "dimension": dimension, "round": round_number, "panel": panel_number,
            "panel_size": len(panel), "maximum_attempts": retries + 1,
            "state": state, "attempts": attempts,
            "note": "Latest invocation; subagent responses may be replayed. "
                    "No source text, raw response, labels or deployment identifiers retained.",
        })

    feedback = None
    record("running")
    try:
        for index in range(retries + 1):
            attempt = index + 1
            prompt = repair_prompt(dimension, panel, feedback, attempt)
            try:
                if on_request is not None:
                    on_request()
                raw = reporter.call(
                    model, prompt, f"{label} request={attempt}/{retries + 1}",
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
                groups = core.parse_clusters(raw, allowed)
            except core.ClusterValidationError as exc:
                feedback = exc.diagnostic()
                attempts.append({"attempt": attempt, "outcome": "invalid", **feedback})
                reporter.log(f"invalid {label}: {exc.code}; "
                             f"group={exc.group_index}; member={exc.member_index}")
                record("retrying" if index < retries else "failed")
                if index >= retries:
                    reporter.log("Cluster recovery exhausted. Keep the checkpoint; rerun "
                                 "without --restart. Diagnostics: style_cluster_diagnostics/")
                    raise
                reporter.log(f"repairing {label}: next request={attempt + 1}/{retries + 1}")
                continue
            attempts.append({"attempt": attempt, "outcome": "valid"})
            record("completed")
            reporter.log(f"validated {label}: groups={len(groups)}")
            return {"groups": [{"ids": ids, "label": name} for ids, name in groups]}
    except TasksPending:
        record("waiting_for_tasks")
        raise
    except BaseException as exc:
        record("interrupted" if isinstance(exc, KeyboardInterrupt) else "failed")
        raise
    raise AssertionError("unreachable cluster retry state")

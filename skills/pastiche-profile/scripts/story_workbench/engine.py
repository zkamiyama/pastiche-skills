"""Backend-independent validated stage executor and durable task checkpoints."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import dataclass
import threading
import time
from typing import Any, Callable
import llm_runtime
from task_transport import TasksPending
from json_boundary import object_copy, validate_data
from . import VERSION
from .store import Store, uid, atomic_json

@dataclass(frozen=True)
class Task:
    id: str
    prompt: str
    validate: Callable[[Any], Any]

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id.strip() or len(self.id) > 400:
            raise ValueError('invalid task ID')
        if not isinstance(self.prompt, str) or not self.prompt or len(self.prompt) > 120_000:
            raise ValueError('task prompt exceeds the bounded execution contract')
        if not callable(self.validate):
            raise ValueError('task needs a domain validator')


class Engine:
    """Bounded provider-neutral execution; pending handoffs are resumable barriers."""
    def __init__(self, store: Store, model: Any, out, *, workers=1, retries=2, interval=15.0):
        if workers < 1 or retries < 0:
            raise ValueError("invalid worker/retry limit")
        self.store, self.model, self.out = store, model, out
        self.workers, self.retries = workers, retries
        self.reporter = llm_runtime.ProgressReporter(out, interval)
        self.local = threading.local()
        self.stats = {"attempts": 0, "completed_calls": 0, "cache_hits": 0}
        self.lock = threading.Lock()

    def key(self, task: Task, stage: str = "") -> str:
        return uid("task", VERSION, stage, self.model.backend, self.model.model,
                   getattr(self.model, "endpoint", "fixture"), getattr(self.model, "max_output_tokens", 0), task.prompt)

    def _invoke(self, task: Task) -> Any:
        if not hasattr(self.local, "model"):
            factory = getattr(self.model, "worker_copy", None)
            self.local.model = factory() if factory else self.model
        for attempt in range(self.retries + 1):
            with self.lock:
                self.stats["attempts"] += 1
            try:
                raw = self.reporter.call(self.local.model, task.prompt, task.id, retries=0, record_status=False)
                value = task.validate(object_copy(raw))
                validate_data(value)
                return value
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                if attempt >= self.retries or not getattr(exc, "transient", True):
                    raise ValueError(f"{task.id}: model response/transport failed validation ({type(exc).__name__})") from exc
                time.sleep(min(2**attempt, 8))
        raise AssertionError("unreachable")

    def run(self, stage: str, tasks: list[Task]) -> dict[str, Any]:
        if not isinstance(tasks, list) or any(not isinstance(t, Task) for t in tasks):
            raise ValueError('stage tasks must be a list of Task objects')
        if len({t.id for t in tasks}) != len(tasks):
            raise ValueError('duplicate task IDs would overwrite stage results')
        results, pending_jobs = {}, []
        for task in tasks:
            cached = self.store.get("tasks", self.key(task, stage))
            if cached is not None:
                try:
                    value = task.validate(cached)
                    validate_data(value)
                    results[task.id] = value
                    self.stats["cache_hits"] += 1
                    continue
                except (ValueError, TypeError, KeyError, AttributeError):
                    pass
            pending_jobs.append(task)
        todo = iter(pending_jobs)
        failure = None
        deferred = None
        def progress():
            atomic_json(self.out / "story_progress.json", {"state": "running", "stage": stage,
                         "total": len(tasks), "completed": len(results), **self.stats})
        progress()
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            pending = {}
            def submit():
                task = next(todo, None)
                if task is not None:
                    pending[pool.submit(self._invoke, task)] = task
            for _ in range(self.workers):
                submit()
            while pending:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    task = pending.pop(future)
                    try:
                        value = future.result()
                    except TasksPending as exc:
                        deferred = exc
                    except Exception as exc:
                        failure = failure or exc
                    else:
                        self.store.task_put(self.key(task, stage), value)
                        results[task.id] = value
                        self.stats["completed_calls"] += 1
                    if failure is None:
                        submit()
                    progress()
        if failure:
            atomic_json(self.out / "story_progress.json", {"state": "failed", "stage": stage,
                         "error_type": type(failure).__name__, "total": len(tasks),
                         "completed": len(results), **self.stats})
            raise failure
        if deferred:
            atomic_json(self.out / "story_progress.json", {"state": "waiting_for_tasks", "stage": stage,
                         "total": len(tasks), "completed": len(results), **self.stats})
            raise deferred
        atomic_json(self.out / "story_progress.json", {"state": "completed", "stage": stage,
                     "total": len(tasks), "completed": len(results), **self.stats})
        return results

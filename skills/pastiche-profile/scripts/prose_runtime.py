"""Bounded prose calls with Python task binding, safe diagnostics and cache.

SQLite remains main-thread-only. Atomic per-task JSON contains validated
abstract descriptions or verdicts, never prompts, excerpts or model IDs.
"""
from __future__ import annotations
import json
import time
from typing import Any, Callable
import llm_runtime
import llm_style_core as core
import prose_contracts as contracts
from task_transport import TasksPending

def fingerprint(kind: str, binding: Any, model: Any, *, text_output: bool = False) -> str:
    payload = {'contract': contracts.VERSION, 'kind': kind, 'binding': binding,
               'max_output_tokens': getattr(model, 'max_output_tokens', 0)}
    if text_output: payload['response_mode'] = contracts.SYNTHESIS_TEXT_VERSION
    return core.digest('python-owned-task', payload, model)

def load_cached_prose(model: Any, reporter: llm_runtime.ProgressReporter, *, kind: str,
                      binding: Any, parser: Callable[[Any], dict],
                      cache_validator: Callable[[Any], dict] | None = None,
                      text_output: bool = False, retries: int = 0) -> dict | None:
    """Read/revalidate a bound result; never issue a request on a cache miss."""
    key = fingerprint(kind, binding, model, text_output=text_output)
    cache = reporter.out / 'style_binding_cache' / (key + '.json')
    diagnostic = reporter.out / 'style_binding_diagnostics' / (key + '.json')
    if getattr(reporter, "reuse_prose_cache", True) and cache.is_file():
        try:
            value = json.loads(cache.read_text(encoding='utf-8'))
            if isinstance(value, dict) and value.get('contract') == contracts.VERSION and value.get('binding_sha256') == key:
                checked = (cache_validator or parser)(value['value'])
                llm_runtime.atomic_json_write(diagnostic, {
                    'schema': 'pastiche.python-binding.diagnostic.v1', 'contract': contracts.VERSION,
                    'kind': kind, 'binding_sha256': key, 'state': 'completed',
                    'cache_hit': True, 'maximum_attempts': retries+1, 'attempts': []})
                return checked
        except (OSError, ValueError, TypeError, KeyError): pass
    return None


class _TextResponseAdapter:
    """Use the same progress callbacks without changing a worker's JSON mode."""
    def __init__(self, model: Any): self.model = model
    @property
    def on_chunk(self): return getattr(self.model, 'on_chunk', None)
    @on_chunk.setter
    def on_chunk(self, value): self.model.on_chunk = value
    def ask(self, prompt: str) -> str:
        method = getattr(self.model, 'ask_text', None)
        if not callable(method):
            raise llm_runtime.ModelRequestError('executor does not support plain-text requests', transient=False)
        return method(prompt)


def call_prose(model: Any, reporter: llm_runtime.ProgressReporter, *, kind: str,
               binding: Any, prompt: str, parser: Callable[[Any], dict], label: str,
               retries: int, on_request: Callable[[], None] | None = None,
               retry_delay: float = 2.0,
               cache_validator: Callable[[Any], dict] | None = None,
               text_output: bool = False) -> dict:
    if type(retries) is not int or retries < 0: raise ValueError('invalid prose request budget')
    if type(text_output) is not bool or (text_output and cache_validator is None):
        raise ValueError('text requests require a cache envelope validator')
    key = fingerprint(kind, binding, model, text_output=text_output)
    cache = reporter.out / 'style_binding_cache' / (key + '.json')
    diagnostic = reporter.out / 'style_binding_diagnostics' / (key + '.json')
    cached = load_cached_prose(model, reporter, kind=kind, binding=binding,
                              parser=parser, cache_validator=cache_validator, text_output=text_output, retries=retries)
    if cached is not None: return cached
    request_model = _TextResponseAdapter(model) if text_output else model
    attempts = []
    def record(state: str):
        llm_runtime.atomic_json_write(diagnostic, {
            'schema': 'pastiche.python-binding.diagnostic.v1', 'contract': contracts.VERSION,
            'kind': kind, 'binding_sha256': key, 'state': state,
            'maximum_attempts': retries + 1, 'attempts': attempts,
            'note': 'Fixed codes only; no source, prompt, response prose or deployment identifiers.'})
    record('running'); feedback = None
    try:
        for attempt in range(1, retries + 2):
            actual_prompt = prompt
            if feedback:
                actual_prompt += ('\nOUTPUT_REPAIR: ' + json.dumps(feedback, sort_keys=True) + '. Re-examine the SAME '
                    'bound task; correct the identified field using the stated type and '
                    'inclusive character bounds, without changing a verdict just to pass validation. '
                    + ('Return only the requested plain prose; no JSON, keys or metadata. ' if text_output
                       else 'Return only the required prose/verdict fields. ') +
                    'Do not invent identifiers, quotations or successful results.')
            try:
                if on_request: on_request()
                raw = reporter.call(request_model, actual_prompt, f'{label} request={attempt}/{retries+1}',
                                    retries=0, record_status=False)
                value = parser(raw)
            except llm_runtime.ModelRequestError as exc:
                feedback = {'code': 'model_transport_or_json_error'}
                attempts.append({'attempt': attempt, 'outcome': 'model_error', **feedback})
                record('retrying' if attempt <= retries and exc.transient else 'failed')
                if attempt > retries or not exc.transient: raise
                if retry_delay: time.sleep(min(retry_delay * 2**(attempt-1), 8))
                continue
            except contracts.ProseError as exc:
                feedback = exc.diagnostic()
                attempts.append({'attempt': attempt, 'outcome': 'invalid', **feedback})
                reporter.log(f'invalid {label}: ' + str(exc))
                record('retrying' if attempt <= retries else 'failed')
                if attempt > retries: raise
                continue
            attempts.append({'attempt': attempt, 'outcome': 'valid'})
            llm_runtime.atomic_json_write(cache, {
                'contract': contracts.VERSION, 'binding_sha256': key, 'value': value})
            record('completed')
            return value
    except TasksPending:
        record('waiting_for_tasks'); raise
    except BaseException as exc:
        record('interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed'); raise
    raise AssertionError('unreachable prose retry state')

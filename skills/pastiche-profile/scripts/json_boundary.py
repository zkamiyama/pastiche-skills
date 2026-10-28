"""Strict JSON at untrusted boundaries and atomic, private file publication.

Duplicate members and nonfinite numbers are rejected rather than choosing an
interpretation. Internal JSON writers keep Python's tuple-to-array convention;
callback results must already consist exclusively of JSON data types.
"""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any

MAX_BYTES = 8_000_000
MAX_DEPTH = 64
MAX_NODES = 1_000_000


def _pairs(items: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate JSON member')
        result[key] = value
    return result


def _constant(_value: str) -> None:
    raise ValueError('nonfinite JSON number')


def validate_data(value: Any) -> None:
    """Bound nesting/work and reject Python-only values, cycles and NaN/Inf."""
    stack = [(value, 0)]
    visited = 0
    while stack:
        item, depth = stack.pop()
        visited += 1
        if depth > MAX_DEPTH or visited > MAX_NODES:
            raise ValueError('JSON nesting or node budget exceeded')
        kind = type(item)
        if item is None or kind in (bool, int, str):
            if kind is str:
                try:
                    item.encode('utf-8')
                except UnicodeError as exc:
                    raise ValueError('invalid Unicode in JSON') from exc
            continue
        if kind is float:
            if not math.isfinite(item):
                raise ValueError('nonfinite JSON number')
        elif kind is list:
            if visited + len(stack) + len(item) > MAX_NODES:
                raise ValueError('JSON node budget exceeded')
            stack.extend((v, depth+1) for v in item)
        elif kind is dict:
            if any(type(k) is not str for k in item):
                raise ValueError('JSON object keys must be strings')
            if visited + len(stack) + 2*len(item) > MAX_NODES:
                raise ValueError('JSON node budget exceeded')
            stack.extend((v, depth+1) for pair in item.items() for v in pair)
        else:
            raise ValueError('value is not inert JSON data')


def loads(raw: str | bytes, *, maximum: int = MAX_BYTES) -> Any:
    try:
        size = len(raw.encode('utf-8')) if isinstance(raw, str) else len(raw)
        if size > maximum:
            raise ValueError('JSON byte budget exceeded')
        value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
        validate_data(value)
        return value
    except (RecursionError, UnicodeError, TypeError) as exc:
        raise ValueError('malformed or excessively nested JSON') from exc


def object_copy(value: Any) -> dict:
    if type(value) is not dict:
        raise ValueError('response must be a JSON object')
    validate_data(value)
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    return loads(encoded)


def read_object(path: Path, *, maximum: int = MAX_BYTES) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError('missing or linked JSON file')
    with path.open('rb') as stream:
        raw = stream.read(maximum+1)
    value = loads(raw, maximum=maximum)
    if not isinstance(value, dict):
        raise ValueError('JSON file must contain an object')
    return value


def atomic_json(path: Path, value: Any, *, replace: bool = True,
                maximum: int | None = None) -> None:
    """Private unique temporary file, then atomic replace or no-clobber publish.

    link() creates the final name only if it does not exist. A check followed
    by replace() would race with another submitter. Unsupported filesystems
    fail closed; never fall back to a racy overwrite.
    """
    path = Path(path)
    if path.is_symlink():
        raise ValueError('linked output file is not allowed')
    data = (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)+'\n').encode('utf-8')
    if maximum is not None and len(data) > maximum:
        raise ValueError('JSON byte budget exceeded')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.'+path.name+'.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(name, path)
        else:
            os.link(name, path)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass

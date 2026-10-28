"""Deterministic corpus segmentation and style-sample selection."""
from __future__ import annotations
from pathlib import Path
import re
from typing import Any
from pastiche import input_files, read_text

def split_segments(text: str, max_segment_chars: int = 500) -> list[str]:
    """Stable non-empty line/sentence segments; do not persist segment text."""
    units: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Avoid indiscriminate chopping except on genuinely long paragraphs.
        parts = [line] if len(line) <= max_segment_chars else re.findall(r"[^。！？!?]+[。！？!?]?", line)
        for part in parts:
            part = part.strip()
            for i in range(0, len(part), max_segment_chars):
                unit = part[i:i+max_segment_chars]
                if unit:
                    units.append(unit)
    return units


def chunk_segments(units: list[str], work_id: str, chunk_chars: int) -> list[list[tuple[str, str]]]:
    current: list[tuple[str, str]] = []
    count = 0
    chunks: list[list[tuple[str, str]]] = []
    for i, unit in enumerate(units, 1):
        eid = f"{work_id}:S{i:04d}"
        needed = len(unit) + len(eid) + 4
        if current and count + needed > chunk_chars:
            chunks.append(current)
            current, count = [], 0
        current.append((eid, unit))
        count += needed
    if current:
        chunks.append(current)
    return chunks


def enumerated_corpus(corpus: Path, min_chars: int) -> list[tuple[str, Path, list[str]]]:
    items = []
    for path in input_files(corpus):
        try:
            raw = read_text(path)
        except (OSError, ValueError):
            continue
        if len("".join(raw.split())) < min_chars:
            continue
        units = split_segments(raw)
        if units:
            items.append((f"W{len(items)+1:04d}", path, units))
    return items

def stratified_indices(n:int,k:int)->list[int]:
    if n<=0 or k<=0:
        return []
    if k>=n:
        return list(range(n))
    if k==1:
        return [n//2]
    return sorted({round(i*(n-1)/(k-1)) for i in range(k)})


def select_chunks(chunks:list, max_count:int, work_pos:int)->list[tuple[int,Any]]:
    if not chunks:
        return []
    if len(chunks)<=max_count:
        return list(enumerate(chunks,1))
    if max_count==1:
        return [(len(chunks)//2+1,chunks[len(chunks)//2])]
    # Rotate which phase is skipped for each work, rather than always ignoring the middle.
    if max_count==2:
        anchor=[0,len(chunks)//2,len(chunks)-1]
        picks=sorted({anchor[i] for i in range(3) if i!=work_pos%3})
    else:
        picks=stratified_indices(len(chunks), max_count)
    return [(i+1,chunks[i]) for i in picks]


def _is_verbatim_fragment(text:str, chunk:list[tuple[str,str]], min_span:int=12)->bool:
    # Prevent saving long exact source excerpts presented as "abstract rules".
    compact_rule="".join(text.split())
    original="".join("".join(t.split()) for _,t in chunk)
    if len(compact_rule)<min_span:
        return len(compact_rule)>=5 and compact_rule in original
    return any(compact_rule[i:i+min_span] in original for i in range(len(compact_rule)-min_span+1))

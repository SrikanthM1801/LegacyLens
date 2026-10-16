"""
Split an Artifact into overlapping, sentence-aware Chunks.

Text is cut into units (lines, then sentences, then words only if a single
sentence exceeds the chunk size). Units are packed greedily up to
`chunk_size_chars`; the next chunk starts with the trailing units of the previous
one, up to `chunk_overlap_chars`, so facts straddling a boundary stay retrievable.
Chat/email threads split on message lines, so messages are rarely cut in half.
Chunk ids are deterministic (`<artifact_id>_c<ordinal>`) so re-ingest is idempotent.
"""

from __future__ import annotations

import re
from typing import Optional

from app.config import get_settings
from app.models import Artifact, Chunk

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _units(text: str, size: int) -> list[str]:
    """Break text into units no longer than `size`, each keeping its trailing separator."""
    units: list[str] = []
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        sep = line[len(stripped):]
        if len(line) <= size:
            units.append(line)
            continue
        sentences = _SENTENCE_END.split(stripped)
        for i, sent in enumerate(sentences):
            tail = " " if i < len(sentences) - 1 else sep
            unit = sent + tail
            if len(unit) <= size:
                units.append(unit)
                continue
            # A single over-long sentence: fall back to word boundaries.
            words, cur = unit.split(" "), ""
            for w in words:
                piece = w + " "
                while len(piece) > size:  # a single token longer than a chunk
                    if cur:
                        units.append(cur)
                        cur = ""
                    units.append(piece[:size])
                    piece = piece[size:]
                if len(cur) + len(piece) > size and cur:
                    units.append(cur)
                    cur = ""
                cur += piece
            if cur:
                units.append(cur)
    return units


def split_text(text: str, size: int, overlap: int) -> list[str]:
    if size <= 0:
        raise ValueError("size must be positive")
    overlap = max(0, min(overlap, size // 2))  # overlap must leave room for progress
    text = text.strip()
    if not text:
        return []
    out: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for unit in _units(text, size):
        if cur and cur_len + len(unit) > size:
            out.append("".join(cur).strip())
            # Seed the next chunk with trailing units up to `overlap` chars.
            keep: list[str] = []
            kept = 0
            for u in reversed(cur):
                if kept + len(u) > overlap or kept + len(u) + len(unit) > size:
                    break
                keep.insert(0, u)
                kept += len(u)
            cur, cur_len = keep, kept
        cur.append(unit)
        cur_len += len(unit)
    tail = "".join(cur).strip()
    if tail and (not out or tail != out[-1]):
        out.append(tail)
    return [c for c in out if c]


def chunk_artifact(a: Artifact, size: Optional[int] = None, overlap: Optional[int] = None) -> list[Chunk]:
    s = get_settings()
    pieces = split_text(a.body, size or s.chunk_size_chars, s.chunk_overlap_chars if overlap is None else overlap)
    return [
        Chunk(
            id=f"{a.id}_c{i}",
            artifact_id=a.id,
            artifact_type=a.type,
            ordinal=i,
            text=text,
            title=a.title,
            author=a.author,
            timestamp=a.timestamp,
            url_or_path=a.url_or_path,
        )
        for i, text in enumerate(pieces)
    ]


def chunk_artifacts(artifacts: list[Artifact]) -> list[Chunk]:
    return [c for a in artifacts for c in chunk_artifact(a)]

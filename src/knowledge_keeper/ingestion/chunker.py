"""Section-aware chunking.

Sections (slides, pages, headed sections) are natural semantic units, so we
never merge text across section boundaries. Oversized sections are split on
sentence boundaries with overlap. Token counts are estimated at ~4 chars per
token, which is close enough for sizing decisions.
"""
from __future__ import annotations

import re

from ..config import ChunkingConfig
from ..models import Chunk, SourceDocument
from .extractors import RawSection

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _split_long(text: str, target_tokens: int, overlap_tokens: int) -> list[str]:
    sentences = _SENTENCE_SPLIT.split(text)
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for sent in sentences:
        t = estimate_tokens(sent)
        if current and current_tokens + t > target_tokens:
            chunks.append(" ".join(current))
            # overlap: carry trailing sentences forward
            keep: list[str] = []
            kept = 0
            for s in reversed(current):
                kept += estimate_tokens(s)
                keep.insert(0, s)
                if kept >= overlap_tokens:
                    break
            current = keep
            current_tokens = kept
        current.append(sent)
        current_tokens += t
    if current:
        chunks.append(" ".join(current))
    return chunks


def chunk_document(
    doc: SourceDocument,
    sections: list[RawSection],
    cfg: ChunkingConfig,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    ordinal = 0
    pending_text = ""
    pending_locs: list[str] = []

    def emit(text: str, location: str):
        nonlocal ordinal
        text = text.strip()
        if not text:
            return
        chunks.append(
            Chunk(
                chunk_id=Chunk.make_id(doc.doc_id, ordinal),
                doc_id=doc.doc_id,
                doc_title=doc.title,
                doc_type=doc.doc_type,
                text=text,
                location=location,
                author=doc.author,
                modified_at=doc.modified_at,
                ordinal=ordinal,
                token_estimate=estimate_tokens(text),
            )
        )
        ordinal += 1

    def flush_pending():
        nonlocal pending_text, pending_locs
        if pending_text.strip():
            loc = pending_locs[0] if len(pending_locs) == 1 else f"{pending_locs[0]}–{pending_locs[-1]}"
            emit(pending_text, loc)
        pending_text = ""
        pending_locs = []

    for section in sections:
        tokens = estimate_tokens(section.text)
        if tokens >= cfg.min_tokens and tokens <= cfg.target_tokens:
            flush_pending()
            emit(section.text, section.location)
        elif tokens > cfg.target_tokens:
            flush_pending()
            for piece in _split_long(section.text, cfg.target_tokens, cfg.overlap_tokens):
                emit(piece, section.location)
        else:
            # Tiny section (e.g. a title-only slide): coalesce with neighbors.
            pending_text = (pending_text + "\n\n" + section.text).strip()
            pending_locs.append(section.location)
            if estimate_tokens(pending_text) >= cfg.min_tokens:
                flush_pending()
    flush_pending()
    return chunks

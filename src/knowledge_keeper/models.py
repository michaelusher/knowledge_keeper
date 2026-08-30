"""Core data models for knowledge-keeper.

Everything downstream (RAG, gap analysis, doc generation) depends on the
metadata captured here at ingestion time. Do not drop fields casually:
`modified_at` powers staleness detection, `author` powers bus-factor
analysis, and `location` powers citations.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class DocType(str, Enum):
    PPTX = "pptx"
    DOCX = "docx"
    PDF = "pdf"
    MARKDOWN = "markdown"
    TEXT = "text"
    HTML = "html"
    UNKNOWN = "unknown"


class SourceDocument(BaseModel):
    """A file discovered by the ingestion pipeline."""

    doc_id: str
    path: str
    title: str
    doc_type: DocType
    author: Optional[str] = None
    created_at: Optional[datetime] = None
    modified_at: Optional[datetime] = None
    sha256: str
    extra: dict = Field(default_factory=dict)

    @staticmethod
    def make_id(path: str, sha256: str) -> str:
        return hashlib.sha1(f"{path}:{sha256}".encode()).hexdigest()[:16]


class Chunk(BaseModel):
    """A retrievable unit of text with full provenance."""

    chunk_id: str
    doc_id: str
    doc_title: str
    doc_type: DocType
    text: str
    # Human-readable locator for citations: "slide 12", "page 3", "section 2.1"
    location: str
    author: Optional[str] = None
    modified_at: Optional[datetime] = None
    ordinal: int = 0
    token_estimate: int = 0
    extra: dict = Field(default_factory=dict)

    @staticmethod
    def make_id(doc_id: str, ordinal: int) -> str:
        return f"{doc_id}-{ordinal:05d}"


class RetrievedChunk(BaseModel):
    chunk: Chunk
    score: float


class Citation(BaseModel):
    doc_title: str
    location: str
    doc_id: str
    chunk_id: str


class RagAnswer(BaseModel):
    question: str
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    retrieved: list[RetrievedChunk] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Knowledge map & gap analysis models
# --------------------------------------------------------------------------

class TopicCoverage(BaseModel):
    """How well a topic/system/procedure is covered by the corpus."""

    topic: str
    kind: str = "topic"  # topic | system | procedure | role
    doc_ids: list[str] = Field(default_factory=list)
    doc_titles: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    mention_count: int = 0
    explained: bool = False  # mentioned AND actually documented somewhere
    newest_modified: Optional[datetime] = None
    summary: Optional[str] = None


class Finding(BaseModel):
    """A gap, risk, or issue surfaced by analysis."""

    kind: str  # gap | bus_factor | stale | conflict | orphan
    severity: str  # high | medium | low
    title: str
    detail: str
    topic: Optional[str] = None
    doc_ids: list[str] = Field(default_factory=list)
    doc_titles: list[str] = Field(default_factory=list)


class KnowledgeMap(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    documents: list[SourceDocument] = Field(default_factory=list)
    topics: list[TopicCoverage] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)

"""RAG answering with mandatory citations.

Retrieval is hybrid (delegated to the store). Answers cite sources as
[doc_title, location] so every claim traces back to a file — essential for
a knowledge-preservation system where trust depends on provenance.
Without an LLM configured, returns retrieved passages directly.
"""
from __future__ import annotations

import re
from typing import Optional

from .config import Config
from .llm import LlmClient
from .models import Citation, RagAnswer, RetrievedChunk
from .stores.base import Embedder, VectorStore

_SYSTEM = """You are a knowledge assistant answering questions strictly from the provided \
organizational documents. Rules:
- Answer only from the provided passages. If they don't contain the answer, say so \
and name the closest related material.
- Cite every factual claim inline as [S<n>] where <n> is the passage number.
- If passages conflict, say so explicitly and cite both.
- Be concise and concrete."""


def _format_context(retrieved: list[RetrievedChunk]) -> str:
    lines = []
    for i, r in enumerate(retrieved, start=1):
        c = r.chunk
        modified = c.modified_at.date().isoformat() if c.modified_at else "unknown date"
        lines.append(
            f"[S{i}] {c.doc_title} ({c.location}; author: {c.author or 'unknown'}; "
            f"modified: {modified})\n{c.text}"
        )
    return "\n\n".join(lines)


class RagEngine:
    def __init__(self, cfg: Config, store: VectorStore, embedder: Embedder, llm: Optional[LlmClient]):
        self.cfg = cfg
        self.store = store
        self.embedder = embedder
        self.llm = llm

    def retrieve(self, question: str, top_k: int = 8, doc_ids: Optional[list[str]] = None) -> list[RetrievedChunk]:
        qvec = self.embedder.embed_query(question)
        return self.store.search(qvec, question, top_k=top_k, doc_ids=doc_ids or None)

    def synthesize(self, question: str, retrieved: list[RetrievedChunk]) -> tuple[str, list[int]]:
        """Ask the LLM to answer from the passages. Returns (answer, cited passage
        numbers, 1-based). Raises if the LLM call fails — callers decide whether to
        fall back to showing passages."""
        if self.llm is None:
            raise RuntimeError("No LLM configured")
        context = _format_context(retrieved)
        answer = self.llm.complete(_SYSTEM, f"Passages:\n\n{context}\n\nQuestion: {question}")
        cited = sorted({
            int(n)
            for group in re.findall(r"\[(S\d+(?:\s*,\s*S?\d+)*)\]", answer)
            for n in re.findall(r"\d+", group)
            if 1 <= int(n) <= len(retrieved)
        })
        return answer, cited

    def query(self, question: str, top_k: int = 8, doc_ids: Optional[list[str]] = None) -> RagAnswer:
        retrieved = self.retrieve(question, top_k=top_k, doc_ids=doc_ids)

        if not retrieved:
            return RagAnswer(question=question, answer="No relevant material found in the knowledge base.")

        if self.llm is None:
            # Offline mode: return the passages with provenance.
            body = "\n\n".join(
                f"[{r.chunk.doc_title} — {r.chunk.location}] (score {r.score:.2f})\n{r.chunk.text[:600]}"
                for r in retrieved[:4]
            )
            answer = "No LLM configured — top matching passages:\n\n" + body
            citations = [self._citation(r) for r in retrieved[:4]]
            return RagAnswer(question=question, answer=answer, citations=citations, retrieved=retrieved)

        answer, cited = self.synthesize(question, retrieved)
        citations = [self._citation(retrieved[i - 1]) for i in cited] or [
            self._citation(r) for r in retrieved[:3]
        ]
        return RagAnswer(question=question, answer=answer, citations=citations, retrieved=retrieved)

    @staticmethod
    def _citation(r: RetrievedChunk) -> Citation:
        return Citation(
            doc_title=r.chunk.doc_title,
            location=r.chunk.location,
            doc_id=r.chunk.doc_id,
            chunk_id=r.chunk.chunk_id,
        )

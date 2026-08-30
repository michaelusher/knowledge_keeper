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

    def query(self, question: str, top_k: int = 8) -> RagAnswer:
        qvec = self.embedder.embed_query(question)
        retrieved = self.store.search(qvec, question, top_k=top_k)

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

        context = _format_context(retrieved)
        answer = self.llm.complete(_SYSTEM, f"Passages:\n\n{context}\n\nQuestion: {question}")

        cited_ids = {int(m) for m in re.findall(r"\[S(\d+)\]", answer)}
        citations = [
            self._citation(r)
            for i, r in enumerate(retrieved, start=1)
            if i in cited_ids
        ] or [self._citation(r) for r in retrieved[:3]]
        return RagAnswer(question=question, answer=answer, citations=citations, retrieved=retrieved)

    @staticmethod
    def _citation(r: RetrievedChunk) -> Citation:
        return Citation(
            doc_title=r.chunk.doc_title,
            location=r.chunk.location,
            doc_id=r.chunk.doc_id,
            chunk_id=r.chunk.chunk_id,
        )

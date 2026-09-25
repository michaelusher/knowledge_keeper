"""Provider-agnostic interfaces for embeddings and vector search.

Concrete implementations: local (offline dev), Azure AI Search, AWS
(Bedrock embeddings + OpenSearch Serverless).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from ..models import Chunk, RetrievedChunk


class Embedder(ABC):
    dimensions: int

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        ...

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text])[0]


class VectorStore(ABC):
    @abstractmethod
    def ensure_index(self) -> None:
        ...

    @abstractmethod
    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        ...

    @abstractmethod
    def delete_document(self, doc_id: str) -> None:
        """Remove all chunks for a doc (re-ingestion of changed files)."""

    @abstractmethod
    def search(
        self,
        query_vector: list[float],
        query_text: str,
        top_k: int = 8,
        doc_ids: Optional[list[str]] = None,
    ) -> list[RetrievedChunk]:
        """Hybrid search where supported; vector-only otherwise.
        `doc_ids` restricts results to those documents (None = whole corpus)."""

    @abstractmethod
    def all_chunks(self) -> list[Chunk]:
        """Full corpus scan for gap analysis."""

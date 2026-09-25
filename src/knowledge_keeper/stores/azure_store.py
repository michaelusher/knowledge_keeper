"""Azure implementation: Azure OpenAI embeddings + Azure AI Search hybrid search.

Requires extras: pip install "knowledge-keeper[azure]"
Auth: API keys via config/env, or DefaultAzureCredential when keys are empty
(managed identity / az login).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..config import AzureConfig
from ..models import Chunk, DocType, RetrievedChunk
from .base import Embedder, VectorStore


class AzureOpenAIEmbedder(Embedder):
    def __init__(self, cfg: AzureConfig):
        from openai import AzureOpenAI

        self.cfg = cfg
        self.dimensions = cfg.embedding_dimensions
        self.client = AzureOpenAI(
            azure_endpoint=cfg.openai_endpoint,
            api_key=cfg.openai_api_key or None,
            api_version=cfg.openai_api_version,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 64):
            batch = texts[i : i + 64]
            resp = self.client.embeddings.create(
                model=self.cfg.embedding_deployment,
                input=batch,
                dimensions=self.dimensions,
            )
            out.extend(d.embedding for d in resp.data)
        return out


class AzureSearchStore(VectorStore):
    def __init__(self, cfg: AzureConfig):
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents import SearchClient
        from azure.search.documents.indexes import SearchIndexClient

        self.cfg = cfg
        if cfg.search_api_key:
            cred = AzureKeyCredential(cfg.search_api_key)
        else:
            from azure.identity import DefaultAzureCredential

            cred = DefaultAzureCredential()
        self.index_client = SearchIndexClient(cfg.search_endpoint, cred)
        self.client = SearchClient(cfg.search_endpoint, cfg.search_index, cred)

    def ensure_index(self) -> None:
        from azure.search.documents.indexes.models import (
            HnswAlgorithmConfiguration,
            SearchableField,
            SearchField,
            SearchFieldDataType,
            SearchIndex,
            SemanticConfiguration,
            SemanticField,
            SemanticPrioritizedFields,
            SemanticSearch,
            SimpleField,
            VectorSearch,
            VectorSearchProfile,
        )

        if self.cfg.search_index in [n for n in self.index_client.list_index_names()]:
            return

        fields = [
            SimpleField(name="chunk_id", type=SearchFieldDataType.String, key=True),
            SimpleField(name="doc_id", type=SearchFieldDataType.String, filterable=True),
            SearchableField(name="doc_title", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="doc_type", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SearchableField(name="text", type=SearchFieldDataType.String),
            SimpleField(name="location", type=SearchFieldDataType.String),
            SimpleField(name="author", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SimpleField(name="modified_at", type=SearchFieldDataType.DateTimeOffset, filterable=True, sortable=True),
            SimpleField(name="ordinal", type=SearchFieldDataType.Int32),
            SearchField(
                name="vector",
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                searchable=True,
                vector_search_dimensions=self.cfg.embedding_dimensions,
                vector_search_profile_name="default-profile",
            ),
        ]
        index = SearchIndex(
            name=self.cfg.search_index,
            fields=fields,
            vector_search=VectorSearch(
                algorithms=[HnswAlgorithmConfiguration(name="default-hnsw")],
                profiles=[VectorSearchProfile(name="default-profile", algorithm_configuration_name="default-hnsw")],
            ),
            semantic_search=SemanticSearch(
                configurations=[
                    SemanticConfiguration(
                        name="default-semantic",
                        prioritized_fields=SemanticPrioritizedFields(
                            title_field=SemanticField(field_name="doc_title"),
                            content_fields=[SemanticField(field_name="text")],
                        ),
                    )
                ]
            ),
        )
        self.index_client.create_index(index)

    @staticmethod
    def _to_doc(chunk: Chunk, vector: list[float]) -> dict:
        return {
            "chunk_id": chunk.chunk_id,
            "doc_id": chunk.doc_id,
            "doc_title": chunk.doc_title,
            "doc_type": chunk.doc_type.value,
            "text": chunk.text,
            "location": chunk.location,
            "author": chunk.author or "",
            "modified_at": chunk.modified_at.isoformat() if chunk.modified_at else None,
            "ordinal": chunk.ordinal,
            "vector": vector,
        }

    @staticmethod
    def _from_doc(d: dict) -> Chunk:
        modified = d.get("modified_at")
        return Chunk(
            chunk_id=d["chunk_id"],
            doc_id=d["doc_id"],
            doc_title=d.get("doc_title", ""),
            doc_type=DocType(d.get("doc_type", "unknown")),
            text=d.get("text", ""),
            location=d.get("location", ""),
            author=d.get("author") or None,
            modified_at=datetime.fromisoformat(modified) if isinstance(modified, str) else modified,
            ordinal=d.get("ordinal", 0),
        )

    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        docs = [self._to_doc(c, v) for c, v in zip(chunks, vectors)]
        for i in range(0, len(docs), 500):
            self.client.merge_or_upload_documents(docs[i : i + 500])

    def delete_document(self, doc_id: str) -> None:
        results = self.client.search(search_text="*", filter=f"doc_id eq '{doc_id}'", select=["chunk_id"], top=1000)
        keys = [{"chunk_id": r["chunk_id"]} for r in results]
        if keys:
            self.client.delete_documents(keys)

    def search(
        self,
        query_vector: list[float],
        query_text: str,
        top_k: int = 8,
        doc_ids: Optional[list[str]] = None,
    ) -> list[RetrievedChunk]:
        from azure.search.documents.models import VectorizedQuery

        filter_expr = None
        if doc_ids:
            filter_expr = "search.in(doc_id, '" + ",".join(doc_ids) + "', ',')"
        results = self.client.search(
            filter=filter_expr,
            search_text=query_text,
            vector_queries=[VectorizedQuery(vector=query_vector, k_nearest_neighbors=top_k, fields="vector")],
            query_type="semantic",
            semantic_configuration_name="default-semantic",
            top=top_k,
        )
        out = []
        for r in results:
            score = r.get("@search.reranker_score") or r.get("@search.score") or 0.0
            out.append(RetrievedChunk(chunk=self._from_doc(dict(r)), score=float(score)))
        return out

    def all_chunks(self) -> list[Chunk]:
        out: list[Chunk] = []
        results = self.client.search(search_text="*", select=[
            "chunk_id", "doc_id", "doc_title", "doc_type", "text", "location", "author", "modified_at", "ordinal",
        ], top=1000)
        out.extend(self._from_doc(dict(r)) for r in results)
        return out

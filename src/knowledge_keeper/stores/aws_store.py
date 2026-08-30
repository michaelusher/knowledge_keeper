"""AWS implementation: Bedrock (Titan) embeddings + OpenSearch Serverless kNN.

Requires extras: pip install "knowledge-keeper[aws]"
Auth: standard boto3 credential chain (env vars, profile, instance role).
The OpenSearch collection must be a VECTORSEARCH collection with a data
access policy granting the caller read/write (see infra/aws/main.tf).
"""
from __future__ import annotations

import json
from datetime import datetime

from ..config import AwsConfig
from ..models import Chunk, DocType, RetrievedChunk
from .base import Embedder, VectorStore


class BedrockEmbedder(Embedder):
    def __init__(self, cfg: AwsConfig):
        import boto3

        self.cfg = cfg
        self.dimensions = cfg.embedding_dimensions
        self.client = boto3.client("bedrock-runtime", region_name=cfg.region)

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:  # Titan embed API is single-input
            body = json.dumps({"inputText": text[:8000], "dimensions": self.dimensions})
            resp = self.client.invoke_model(modelId=self.cfg.embedding_model_id, body=body)
            payload = json.loads(resp["body"].read())
            out.append(payload["embedding"])
        return out


class OpenSearchStore(VectorStore):
    def __init__(self, cfg: AwsConfig):
        import boto3
        from opensearchpy import OpenSearch, RequestsHttpConnection
        from requests_aws4auth import AWS4Auth

        self.cfg = cfg
        session = boto3.Session()
        creds = session.get_credentials()
        auth = AWS4Auth(
            creds.access_key, creds.secret_key, cfg.region, "aoss",
            session_token=creds.token,
        )
        host = cfg.opensearch_endpoint.replace("https://", "")
        self.client = OpenSearch(
            hosts=[{"host": host, "port": 443}],
            http_auth=auth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
        )

    def ensure_index(self) -> None:
        if self.client.indices.exists(index=self.cfg.opensearch_index):
            return
        self.client.indices.create(
            index=self.cfg.opensearch_index,
            body={
                "settings": {"index": {"knn": True}},
                "mappings": {
                    "properties": {
                        "doc_id": {"type": "keyword"},
                        "doc_title": {"type": "text"},
                        "doc_type": {"type": "keyword"},
                        "text": {"type": "text"},
                        "location": {"type": "keyword"},
                        "author": {"type": "keyword"},
                        "modified_at": {"type": "date"},
                        "ordinal": {"type": "integer"},
                        "vector": {
                            "type": "knn_vector",
                            "dimension": self.cfg.embedding_dimensions,
                            "method": {"name": "hnsw", "engine": "faiss", "space_type": "cosinesimil"},
                        },
                    }
                },
            },
        )

    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        from opensearchpy.helpers import bulk

        actions = []
        for c, v in zip(chunks, vectors):
            actions.append(
                {
                    "_index": self.cfg.opensearch_index,
                    "_id": c.chunk_id,
                    "doc_id": c.doc_id,
                    "doc_title": c.doc_title,
                    "doc_type": c.doc_type.value,
                    "text": c.text,
                    "location": c.location,
                    "author": c.author or "",
                    "modified_at": c.modified_at.isoformat() if c.modified_at else None,
                    "ordinal": c.ordinal,
                    "vector": v,
                }
            )
        bulk(self.client, actions)

    def delete_document(self, doc_id: str) -> None:
        self.client.delete_by_query(
            index=self.cfg.opensearch_index,
            body={"query": {"term": {"doc_id": doc_id}}},
        )

    @staticmethod
    def _from_hit(hit: dict) -> Chunk:
        src = hit["_source"]
        modified = src.get("modified_at")
        return Chunk(
            chunk_id=hit["_id"],
            doc_id=src["doc_id"],
            doc_title=src.get("doc_title", ""),
            doc_type=DocType(src.get("doc_type", "unknown")),
            text=src.get("text", ""),
            location=src.get("location", ""),
            author=src.get("author") or None,
            modified_at=datetime.fromisoformat(modified) if modified else None,
            ordinal=src.get("ordinal", 0),
        )

    def search(self, query_vector: list[float], query_text: str, top_k: int = 8) -> list[RetrievedChunk]:
        # Hybrid: kNN + BM25 text match, merged by normalized rank.
        body = {
            "size": top_k,
            "query": {
                "bool": {
                    "should": [
                        {"knn": {"vector": {"vector": query_vector, "k": top_k}}},
                        {"match": {"text": {"query": query_text, "boost": 0.5}}},
                    ]
                }
            },
            "_source": {"excludes": ["vector"]},
        }
        resp = self.client.search(index=self.cfg.opensearch_index, body=body)
        return [
            RetrievedChunk(chunk=self._from_hit(h), score=float(h["_score"]))
            for h in resp["hits"]["hits"]
        ]

    def all_chunks(self) -> list[Chunk]:
        out: list[Chunk] = []
        resp = self.client.search(
            index=self.cfg.opensearch_index,
            body={"size": 1000, "query": {"match_all": {}}, "_source": {"excludes": ["vector"]}},
        )
        out.extend(self._from_hit(h) for h in resp["hits"]["hits"])
        return out

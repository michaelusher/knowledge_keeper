"""Offline dev implementation: deterministic hashing embedder + numpy store.

The hashing embedder is a feature-hashed bag-of-words with sublinear TF and
L2 normalization. It is NOT semantically comparable to real embedding models
— it exists so the whole pipeline (ingest -> store -> retrieve -> analyze)
runs with zero cloud credentials. Swap to Azure/AWS for real deployments.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Optional

import numpy as np

from ..models import Chunk, RetrievedChunk
from .base import Embedder, VectorStore

_TOKEN = re.compile(r"[a-z0-9]{2,}")

_STOP = frozenset(
    "the a an and or of to in for on with is are was were be been this that "
    "these those it its as at by from we you they i not no if then than so "
    "can will should would may might must do does did have has had".split()
)


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


class HashingEmbedder(Embedder):
    def __init__(self, dimensions: int = 512):
        self.dimensions = dimensions

    def _bucket(self, token: str) -> tuple[int, float]:
        h = hashlib.md5(token.encode()).digest()
        idx = int.from_bytes(h[:4], "little") % self.dimensions
        sign = 1.0 if h[4] % 2 == 0 else -1.0
        return idx, sign

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = np.zeros(self.dimensions, dtype=np.float32)
            counts: dict[str, int] = {}
            for tok in _tokens(text):
                counts[tok] = counts.get(tok, 0) + 1
            for tok, c in counts.items():
                idx, sign = self._bucket(tok)
                vec[idx] += sign * (1.0 + math.log(c))
                # cheap bigram-ish signal via prefix
                idx2, sign2 = self._bucket(tok[:4])
                vec[idx2] += 0.3 * sign2
            norm = float(np.linalg.norm(vec))
            if norm > 0:
                vec /= norm
            out.append(vec.tolist())
        return out


class LocalVectorStore(VectorStore):
    """JSONL chunk store + .npy matrix, cosine + keyword hybrid search."""

    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir) / "local_store"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.chunks_file = self.dir / "chunks.jsonl"
        self.vectors_file = self.dir / "vectors.npy"
        self._chunks: list[Chunk] = []
        self._matrix: np.ndarray | None = None
        self._load()

    # ------------------------------------------------------------ persistence
    def _load(self):
        if self.chunks_file.exists():
            self._chunks = [
                Chunk(**json.loads(line))
                for line in self.chunks_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        if self.vectors_file.exists():
            self._matrix = np.load(self.vectors_file)

    def _save(self):
        with open(self.chunks_file, "w", encoding="utf-8") as f:
            for c in self._chunks:
                f.write(c.model_dump_json() + "\n")
        if self._matrix is not None:
            np.save(self.vectors_file, self._matrix)

    # -------------------------------------------------------------- interface
    def ensure_index(self) -> None:
        pass

    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        if not chunks:
            return
        new_ids = {c.chunk_id for c in chunks}
        keep = [i for i, c in enumerate(self._chunks) if c.chunk_id not in new_ids]
        self._chunks = [self._chunks[i] for i in keep]
        if self._matrix is not None and len(keep) > 0:
            self._matrix = self._matrix[keep]
        elif self._matrix is not None:
            self._matrix = None

        add = np.array(vectors, dtype=np.float32)
        self._chunks.extend(chunks)
        self._matrix = add if self._matrix is None else np.vstack([self._matrix, add])
        self._save()

    def delete_document(self, doc_id: str) -> None:
        keep = [i for i, c in enumerate(self._chunks) if c.doc_id != doc_id]
        if len(keep) == len(self._chunks):
            return
        self._chunks = [self._chunks[i] for i in keep]
        self._matrix = self._matrix[keep] if (self._matrix is not None and keep) else None
        self._save()

    def search(
        self,
        query_vector: list[float],
        query_text: str,
        top_k: int = 8,
        doc_ids: Optional[list[str]] = None,
    ) -> list[RetrievedChunk]:
        if not self._chunks or self._matrix is None:
            return []
        q = np.array(query_vector, dtype=np.float32)
        cosine = self._matrix @ q  # vectors are L2-normalized

        # keyword overlap score for hybrid ranking
        q_tokens = set(_tokens(query_text))
        kw = np.zeros(len(self._chunks), dtype=np.float32)
        if q_tokens:
            for i, c in enumerate(self._chunks):
                overlap = q_tokens & set(_tokens(c.text))
                kw[i] = len(overlap) / len(q_tokens)

        score = 0.6 * cosine + 0.4 * kw
        if doc_ids:
            allowed = set(doc_ids)
            mask = np.array([c.doc_id in allowed for c in self._chunks])
            score = np.where(mask, score, -np.inf)
        order = np.argsort(-score)[:top_k]
        return [
            RetrievedChunk(chunk=self._chunks[i], score=float(score[i]))
            for i in order
            if np.isfinite(score[i]) and score[i] > 0
        ]

    def all_chunks(self) -> list[Chunk]:
        return list(self._chunks)

"""Ingestion pipeline.

Walks a source directory, extracts + chunks supported files, embeds, and
upserts into the configured vector store. A manifest (path -> sha256) makes
re-runs incremental: unchanged files are skipped, changed files are deleted
and re-indexed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config
from ..models import SourceDocument
from ..stores.base import Embedder, VectorStore
from .chunker import chunk_document
from .extractors import EXTRACTORS, extract


@dataclass
class IngestReport:
    ingested: list[str] = field(default_factory=list)
    skipped_unchanged: list[str] = field(default_factory=list)
    skipped_unsupported: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    total_chunks: int = 0


class IngestionPipeline:
    def __init__(self, cfg: Config, store: VectorStore, embedder: Embedder):
        self.cfg = cfg
        self.store = store
        self.embedder = embedder
        self.manifest_path = cfg.provider_data_path / "manifest.json"
        self.docs_path = cfg.provider_data_path / "documents.jsonl"

    # ------------------------------------------------------------- manifest
    def _load_manifest(self) -> dict[str, dict]:
        if self.manifest_path.exists():
            return json.loads(self.manifest_path.read_text())
        return {}

    def _save_manifest(self, manifest: dict) -> None:
        self.manifest_path.write_text(json.dumps(manifest, indent=2))

    def _record_document(self, doc: SourceDocument) -> None:
        """Append/replace doc metadata in a registry used by gap analysis."""
        docs: dict[str, dict] = {}
        if self.docs_path.exists():
            for line in self.docs_path.read_text().splitlines():
                if line.strip():
                    d = json.loads(line)
                    docs[d["path"]] = d
        docs[doc.path] = json.loads(doc.model_dump_json())
        with open(self.docs_path, "w") as f:
            for d in docs.values():
                f.write(json.dumps(d) + "\n")

    def load_documents(self) -> list[SourceDocument]:
        if not self.docs_path.exists():
            return []
        return [
            SourceDocument(**json.loads(line))
            for line in self.docs_path.read_text().splitlines()
            if line.strip()
        ]

    # --------------------------------------------------------------- ingest
    def ingest_directory(self, source_dir: str, progress=None) -> IngestReport:
        report = IngestReport()
        manifest = self._load_manifest()
        self.store.ensure_index()

        files = sorted(
            p for p in Path(source_dir).rglob("*")
            if p.is_file() and not p.name.startswith(("~$", "."))
        )
        for path in files:
            if path.suffix.lower() not in EXTRACTORS:
                report.skipped_unsupported.append(str(path))
                continue
            try:
                result = extract(path)
                if result is None:
                    report.skipped_unsupported.append(str(path))
                    continue
                doc, sections = result

                prior = manifest.get(str(path))
                if prior and prior.get("sha256") == doc.sha256:
                    report.skipped_unchanged.append(str(path))
                    continue
                if prior and prior.get("doc_id"):
                    self.store.delete_document(prior["doc_id"])

                chunks = chunk_document(doc, sections, self.cfg.chunking)
                if chunks:
                    vectors = self.embedder.embed([c.text for c in chunks])
                    self.store.upsert(chunks, vectors)

                manifest[str(path)] = {"sha256": doc.sha256, "doc_id": doc.doc_id}
                self._record_document(doc)
                report.ingested.append(str(path))
                report.total_chunks += len(chunks)
                if progress:
                    progress(str(path), len(chunks))
            except Exception as exc:  # keep going; one bad file shouldn't kill a run
                report.failed[str(path)] = f"{type(exc).__name__}: {exc}"

        self._save_manifest(manifest)
        return report

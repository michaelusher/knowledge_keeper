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
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        return {}

    def _save_manifest(self, manifest: dict) -> None:
        self.manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    def _record_document(self, doc: SourceDocument) -> None:
        """Append/replace doc metadata in a registry used by gap analysis."""
        docs: dict[str, dict] = {}
        if self.docs_path.exists():
            for line in self.docs_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    d = json.loads(line)
                    docs[d["path"]] = d
        docs[doc.path] = json.loads(doc.model_dump_json())
        with open(self.docs_path, "w", encoding="utf-8") as f:
            for d in docs.values():
                f.write(json.dumps(d) + "\n")

    def load_documents(self) -> list[SourceDocument]:
        if not self.docs_path.exists():
            return []
        return [
            SourceDocument(**json.loads(line))
            for line in self.docs_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    # --------------------------------------------------------------- ingest
    def ingest_directory(self, source_dir: str, progress=None) -> IngestReport:
        files = sorted(
            p for p in Path(source_dir).rglob("*")
            if p.is_file() and not p.name.startswith(("~$", "."))
        )
        return self.ingest_files(files, progress=progress)

    def ingest_files(self, paths, progress=None) -> IngestReport:
        """Ingest specific files. Unchanged files (same SHA-256) are skipped;
        changed files replace their previous chunks."""
        report = IngestReport()
        manifest = self._load_manifest()
        self.store.ensure_index()

        for raw in paths:
            path = Path(raw)
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

                manifest[str(path)] = {"sha256": doc.sha256, "doc_id": doc.doc_id, "chunks": len(chunks)}
                self._replace_document(doc, old_doc_id=prior.get("doc_id") if prior else None)
                report.ingested.append(str(path))
                report.total_chunks += len(chunks)
                if progress:
                    progress(str(path), len(chunks))
            except Exception as exc:  # keep going; one bad file shouldn't kill a run
                report.failed[str(path)] = f"{type(exc).__name__}: {exc}"

        self._save_manifest(manifest)
        return report

    def _replace_document(self, doc: SourceDocument, old_doc_id: str | None) -> None:
        if old_doc_id:
            self._drop_from_registry(old_doc_id)
        self._record_document(doc)

    def _drop_from_registry(self, doc_id: str) -> None:
        remaining = [d for d in self.load_documents() if d.doc_id != doc_id]
        with open(self.docs_path, "w", encoding="utf-8") as f:
            for d in remaining:
                f.write(d.model_dump_json() + "\n")

    # --------------------------------------------------------------- remove
    def chunk_counts(self) -> dict[str, int]:
        """doc_id -> number of indexed chunks, from the manifest."""
        return {
            v.get("doc_id"): int(v.get("chunks", 0))
            for v in self._load_manifest().values()
            if v.get("doc_id")
        }

    def remove_document(self, doc_id: str) -> SourceDocument | None:
        """Delete a document's chunks, registry entry, and manifest entry.
        Invalidates the knowledge map, which no longer matches the corpus."""
        doc = next((d for d in self.load_documents() if d.doc_id == doc_id), None)
        self.store.delete_document(doc_id)
        self._drop_from_registry(doc_id)
        manifest = self._load_manifest()
        for key in [k for k, v in manifest.items() if v.get("doc_id") == doc_id]:
            manifest.pop(key)
        self._save_manifest(manifest)
        kmap = self.cfg.provider_data_path / "knowledge_map.json"
        if kmap.exists():
            kmap.unlink()
        return doc

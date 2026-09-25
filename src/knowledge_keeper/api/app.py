"""HTTP API for knowledge-keeper.

Endpoints:
  POST /ask                    RAG query with citations
  POST /ingest                 ingest a server-side directory
  POST /analyze                rebuild knowledge map + findings
  GET  /findings               current findings
  GET  /topics                 topic coverage
  GET  /documents              document inventory
  GET  /report                 Markdown report
  GET  /healthz
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from ..config import Config, load_config
from ..factory import build_all
from ..ingestion.pipeline import IngestionPipeline
from ..models import KnowledgeMap, RagAnswer
from ..rag import RagEngine

app = FastAPI(title="knowledge-keeper", version="0.1.0")


@lru_cache
def get_cfg() -> Config:
    return load_config(os.environ.get("KK_CONFIG_PATH"))


@lru_cache
def get_components():
    return build_all(get_cfg())


def _kmap() -> KnowledgeMap:
    path = get_cfg().provider_data_path / "knowledge_map.json"
    if not path.exists():
        raise HTTPException(404, "No knowledge map yet — POST /analyze first.")
    return KnowledgeMap(**json.loads(path.read_text(encoding="utf-8")))


class AskRequest(BaseModel):
    question: str
    top_k: int = 8


class IngestRequest(BaseModel):
    source_dir: str


@app.get("/healthz")
def healthz():
    return {"status": "ok", "provider": get_cfg().provider}


@app.post("/ask", response_model=RagAnswer)
def ask(req: AskRequest):
    store, embedder, llm = get_components()
    engine = RagEngine(get_cfg(), store, embedder, llm)
    return engine.query(req.question, top_k=req.top_k)


@app.post("/ingest")
def ingest(req: IngestRequest):
    if not os.path.isdir(req.source_dir):
        raise HTTPException(400, f"Not a directory: {req.source_dir}")
    store, embedder, _ = get_components()
    pipeline = IngestionPipeline(get_cfg(), store, embedder)
    report = pipeline.ingest_directory(req.source_dir)
    return {
        "ingested": len(report.ingested),
        "unchanged": len(report.skipped_unchanged),
        "unsupported": len(report.skipped_unsupported),
        "failed": report.failed,
        "chunks": report.total_chunks,
    }


@app.post("/analyze")
def analyze():
    from ..analysis.gap_analysis import run_gap_analysis
    from ..analysis.knowledge_map import build_knowledge_map

    cfg = get_cfg()
    store, embedder, llm = get_components()
    if not cfg.analysis.use_llm:
        llm = None
    pipeline = IngestionPipeline(cfg, store, embedder)
    chunks = store.all_chunks()
    if not chunks:
        raise HTTPException(400, "Store is empty — ingest documents first.")
    kmap = build_knowledge_map(pipeline.load_documents(), chunks, llm)
    kmap = run_gap_analysis(kmap, chunks, cfg.analysis, llm)
    (cfg.provider_data_path / "knowledge_map.json").write_text(kmap.model_dump_json(indent=2), encoding="utf-8")
    return {"topics": len(kmap.topics), "findings": len(kmap.findings)}


@app.get("/findings")
def findings(severity: str | None = None):
    kmap = _kmap()
    items = kmap.findings
    if severity:
        items = [f for f in items if f.severity == severity]
    return items


@app.get("/topics")
def topics():
    return _kmap().topics


@app.get("/documents")
def documents():
    return _kmap().documents


@app.get("/report")
def report():
    from ..analysis.doc_generator import generate_report

    cfg = get_cfg()
    store, _, llm = get_components()
    if not cfg.analysis.use_llm:
        llm = None
    md = generate_report(_kmap(), store.all_chunks(), llm)
    return {"markdown": md}

"""End-to-end smoke test in local (offline) mode:
extract -> chunk -> embed -> store -> retrieve -> map -> gaps -> report.
Run: pytest tests/ -v
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from knowledge_keeper.analysis.doc_generator import generate_report
from knowledge_keeper.analysis.gap_analysis import run_gap_analysis
from knowledge_keeper.analysis.knowledge_map import build_knowledge_map
from knowledge_keeper.config import Config
from knowledge_keeper.factory import build_all
from knowledge_keeper.ingestion.pipeline import IngestionPipeline
from knowledge_keeper.rag import RagEngine


@pytest.fixture(scope="module")
def corpus(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("corpus")
    script = Path(__file__).parent.parent / "scripts" / "make_sample_corpus.py"
    subprocess.run([sys.executable, str(script), str(out)], check=True)
    return out


@pytest.fixture(scope="module")
def env(tmp_path_factory, corpus):
    cfg = Config(provider="local", data_dir=str(tmp_path_factory.mktemp("data")))
    store, embedder, llm = build_all(cfg)
    pipeline = IngestionPipeline(cfg, store, embedder)
    report = pipeline.ingest_directory(str(corpus))
    return cfg, store, embedder, llm, pipeline, report


def test_ingestion(env):
    _, _, _, _, _, report = env
    assert len(report.ingested) == 4
    assert not report.failed
    assert report.total_chunks >= 6


def test_incremental_reingest(env, corpus):
    cfg, store, embedder, _, pipeline, _ = env
    report2 = pipeline.ingest_directory(str(corpus))
    assert len(report2.ingested) == 0
    assert len(report2.skipped_unchanged) == 4


def test_metadata_preserved(env):
    _, store, *_ = env
    chunks = store.all_chunks()
    authors = {c.author for c in chunks if c.author}
    assert "Priya Sharma" in authors
    assert any(c.location.startswith("slide") for c in chunks)
    assert any("Speaker notes" in c.text for c in chunks), "pptx notes must be captured"


def test_retrieval(env):
    cfg, store, embedder, llm, *_ = env
    engine = RagEngine(cfg, store, embedder, llm)
    result = engine.query("What is the billing queue and who consumes it?")
    assert result.retrieved, "should retrieve something"
    top_titles = [r.chunk.doc_title for r in result.retrieved[:3]]
    assert any("OrderFlow" in t or "FAQ" in t for t in top_titles)


def test_gap_analysis_finds_planted_issues(env):
    cfg, store, _, _, pipeline, _ = env
    chunks = store.all_chunks()
    kmap = build_knowledge_map(pipeline.load_documents(), chunks, llm=None)
    kmap = run_gap_analysis(kmap, chunks, cfg.analysis, llm=None)
    kinds = {f.kind for f in kmap.findings}
    assert "stale" in kinds, "backdated runbook should be flagged stale"
    assert "bus_factor" in kinds or "gap" in kinds
    assert len(kmap.topics) > 5


def test_report_generation(env):
    cfg, store, _, _, pipeline, _ = env
    chunks = store.all_chunks()
    kmap = build_knowledge_map(pipeline.load_documents(), chunks, llm=None)
    kmap = run_gap_analysis(kmap, chunks, cfg.analysis, llm=None)
    md = generate_report(kmap, chunks, llm=None)
    assert "# System Knowledge Report" in md
    assert "Document Inventory" in md
    assert "Gaps, Risks & Issues" in md


def test_provider_state_isolation(tmp_path, corpus):
    """Switching providers must never share manifests or doc registries —
    otherwise a fresh backend would skip files as 'unchanged'."""
    from knowledge_keeper.config import get_active_provider, set_active_provider

    cfg_local = Config(provider="local", data_dir=str(tmp_path))
    store, embedder, _ = build_all(cfg_local)
    IngestionPipeline(cfg_local, store, embedder).ingest_directory(str(corpus))
    assert (tmp_path / "local" / "manifest.json").exists()

    # A different provider sees a clean slate (simulated with a second namespace)
    cfg_other = Config(provider="aws", data_dir=str(tmp_path))
    assert not (cfg_other.provider_data_path / "manifest.json").exists()

    # Active-provider persistence round-trips
    set_active_provider("aws", str(tmp_path))
    assert get_active_provider(str(tmp_path)) == "aws"

"""Application service shared by the desktop GUI (and usable from scripts).

Wraps config, components, ingestion, RAG, analysis, and settings behind one
object with plain-dict results. Long operations (ingest, analyze, report) run
as background jobs so the web UI can poll progress instead of hanging.

A *workspace* is a folder holding everything for one knowledge base:

    <workspace>/config.yaml      settings (written by the Settings page)
    <workspace>/kk_data/         indexes, manifests, knowledge maps (per provider)
    <workspace>/documents/       files you upload or drop in
    <workspace>/reports/         generated Markdown reports

The GUI process chdirs into its workspace, so relative paths in config.yaml
(like the default data_dir ./kk_data) resolve there — the same way the CLI
resolves them against the folder you run `kk` from.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

import yaml

from . import keystore
from .config import Config, load_config, set_active_provider
from .ingestion.extractors import EXTRACTORS
from .ingestion.pipeline import IngestionPipeline
from .models import KnowledgeMap
from .rag import RagEngine
from .stores.base import Embedder, VectorStore

PROVIDERS = ("local", "azure", "aws")
LLM_PROVIDERS = ("none", "gemini", "anthropic", "azure_openai", "bedrock")
SUPPORTED_EXTENSIONS = sorted(EXTRACTORS)

# Settings the GUI may change, as dotted config paths.
EDITABLE_SETTINGS = {
    "llm.provider": str,
    "gemini.model": str,
    "anthropic.model": str,
    "azure.search_endpoint": str,
    "azure.openai_endpoint": str,
    "azure.chat_deployment": str,
    "azure.embedding_deployment": str,
    "aws.region": str,
    "aws.opensearch_endpoint": str,
    "aws.chat_model_id": str,
    "analysis.stale_days": int,
}


def app_version() -> str:
    try:
        from importlib.metadata import version

        return version("knowledge-keeper")
    except Exception:
        return "dev"


def default_workspace() -> Path:
    return Path.home() / "KnowledgeKeeper"


def resolve_workspace(explicit: Optional[str] = None, cwd: Optional[Path] = None) -> Path:
    """--workspace > KK_HOME > current folder if it already holds a knowledge base
    (config.yaml or kk_data/) > ~/KnowledgeKeeper."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    if os.environ.get("KK_HOME"):
        return Path(os.environ["KK_HOME"]).expanduser().resolve()
    cwd = cwd or Path.cwd()
    if (cwd / "config.yaml").exists() or (cwd / "kk_data").is_dir():
        return cwd.resolve()
    return default_workspace()


class BusyError(RuntimeError):
    """Raised when a job is started while another one is still running."""


class ServiceError(RuntimeError):
    """A problem worth showing to the user as-is."""


# ------------------------------------------------------------------ jobs ---

class Job:
    def __init__(self, kind: str, label: str):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.label = label
        self.status = "queued"  # queued | running | done | error
        self.created = time.time()
        self.started: Optional[float] = None
        self.finished: Optional[float] = None
        self.lines: list[str] = []
        self.progress: Optional[float] = None
        self.result: Optional[dict] = None
        self.error: Optional[str] = None

    def log(self, message: str) -> None:
        self.lines.append(str(message))
        del self.lines[:-500]

    def set_progress(self, done: int, total: int) -> None:
        self.progress = (done / total) if total else None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "status": self.status,
            "progress": self.progress,
            "log": self.lines,
            "result": self.result,
            "error": self.error,
            "elapsed": round((self.finished or time.time()) - (self.started or self.created), 1),
        }


class JobManager:
    """Runs at most one job at a time in a background thread."""

    def __init__(self, keep: int = 25):
        self._jobs: "OrderedDict[str, Job]" = OrderedDict()
        self._lock = threading.Lock()
        self._keep = keep

    def current(self) -> Optional[Job]:
        for job in reversed(self._jobs.values()):
            if job.status in ("queued", "running"):
                return job
        return None

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def start(self, kind: str, label: str, fn: Callable[[Job], Optional[dict]]) -> Job:
        with self._lock:
            running = self.current()
            if running:
                raise BusyError(f"Please wait — '{running.label}' is still running.")
            job = Job(kind, label)
            self._jobs[job.id] = job
            while len(self._jobs) > self._keep:
                self._jobs.popitem(last=False)

        def run():
            job.status = "running"
            job.started = time.time()
            try:
                job.result = fn(job) or {}
                job.status = "done"
            except Exception as exc:  # surfaced to the UI
                job.error = f"{exc}" if isinstance(exc, ServiceError) else f"{type(exc).__name__}: {exc}"
                job.log(f"Error: {job.error}")
                job.status = "error"
            finally:
                job.finished = time.time()

        threading.Thread(target=run, name=f"kk-job-{kind}", daemon=True).start()
        return job


# --------------------------------------------------------- locked store ---

class _LockedStore(VectorStore):
    """Serializes access to a vector store so a question asked mid-ingest never
    sees a half-updated index. Only store calls are locked — slow LLM calls
    happen outside the lock."""

    def __init__(self, inner: VectorStore, lock: threading.RLock):
        self._inner = inner
        self._lock = lock

    def ensure_index(self):
        with self._lock:
            return self._inner.ensure_index()

    def upsert(self, chunks, vectors):
        with self._lock:
            return self._inner.upsert(chunks, vectors)

    def delete_document(self, doc_id):
        with self._lock:
            return self._inner.delete_document(doc_id)

    def search(self, query_vector, query_text, top_k=8, doc_ids=None):
        with self._lock:
            return self._inner.search(query_vector, query_text, top_k=top_k, doc_ids=doc_ids)

    def all_chunks(self):
        with self._lock:
            return self._inner.all_chunks()


# --------------------------------------------------------------- service ---

class KnowledgeKeeper:
    def __init__(self, workspace: Path, share: Optional[dict] = None):
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        os.chdir(self.workspace)
        self.documents_dir = self.workspace / "documents"
        self.reports_dir = self.workspace / "reports"
        self.documents_dir.mkdir(exist_ok=True)
        self.reports_dir.mkdir(exist_ok=True)
        self.share = share or {}
        self.jobs = JobManager()
        self._store_lock = threading.RLock()
        self._components: Optional[tuple] = None
        self._components_stamp: Optional[float] = None
        self.llm_error: Optional[str] = None
        self.backend_error: Optional[str] = None
        keystore.load_into_env()
        self.reload()

    # ----------------------------------------------------------- config
    @property
    def config_path(self) -> Path:
        return self.workspace / "config.yaml"

    def reload(self) -> None:
        self.cfg: Config = load_config(str(self.config_path))
        self._components = None

    def _local_stamp(self) -> Optional[float]:
        if self.cfg.provider != "local":
            return None
        f = self.cfg.provider_data_path / "local_store" / "chunks.jsonl"
        return f.stat().st_mtime if f.exists() else 0.0

    def _get_components(self) -> tuple[VectorStore, Embedder, Optional[object]]:
        """Build (store, embedder, llm) lazily; rebuild if the local index was
        changed by another process (e.g. the CLI)."""
        stamp = self._local_stamp()
        if self._components is not None and stamp == self._components_stamp:
            return self._components

        from .factory import build_store_and_embedder
        from .llm import build_llm

        try:
            store, embedder = build_store_and_embedder(self.cfg)
            self.backend_error = None
        except ImportError as exc:
            extra = "azure" if self.cfg.provider == "azure" else "aws"
            self.backend_error = (
                f"The {self.cfg.provider} backend needs extra packages: "
                f"pipx inject knowledge-keeper '.[{extra}]' (or pip install -e '.[{extra}]'). ({exc})"
            )
            raise ServiceError(self.backend_error) from exc
        except Exception as exc:
            self.backend_error = f"Could not connect to the {self.cfg.provider} backend: {exc}"
            raise ServiceError(self.backend_error) from exc

        llm = None
        try:
            llm = build_llm(self.cfg)
            self.llm_error = None
        except Exception as exc:
            self.llm_error = str(exc)

        self._components = (_LockedStore(store, self._store_lock), embedder, llm)
        self._components_stamp = stamp
        return self._components

    def _pipeline(self) -> IngestionPipeline:
        store, embedder, _ = self._get_components()
        return IngestionPipeline(self.cfg, store, embedder)

    def _registry(self) -> IngestionPipeline:
        """Pipeline used only for reading registries — needs no backend connection."""
        return IngestionPipeline(self.cfg, None, None)  # type: ignore[arg-type]

    # ----------------------------------------------------------- status
    def _llm_model(self) -> str:
        p = self.cfg.llm.provider
        return {
            "gemini": self.cfg.gemini.model,
            "anthropic": self.cfg.anthropic.model,
            "azure_openai": self.cfg.azure.chat_deployment,
            "bedrock": self.cfg.aws.chat_model_id,
        }.get(p, "")

    def _backend_rows(self) -> list[dict]:
        def has(mod: str) -> bool:
            try:
                return importlib.util.find_spec(mod) is not None
            except (ImportError, ValueError):
                return False

        def doc_count(provider: str) -> int:
            f = self.cfg.data_path / provider / "documents.jsonl"
            if not f.exists():
                return 0
            return sum(1 for line in f.read_text(encoding="utf-8").splitlines() if line.strip())

        azure_conf = bool(self.cfg.azure.search_endpoint and "YOUR-" not in self.cfg.azure.search_endpoint)
        aws_conf = bool(self.cfg.aws.opensearch_endpoint and "YOUR-" not in self.cfg.aws.opensearch_endpoint)
        return [
            {"name": "local", "label": "This computer (free, offline)", "installed": True,
             "configured": True, "documents": doc_count("local")},
            {"name": "azure", "label": "Azure AI Search", "installed": has("azure.search.documents") and has("openai"),
             "configured": azure_conf, "documents": doc_count("azure")},
            {"name": "aws", "label": "AWS OpenSearch + Bedrock", "installed": has("boto3") and has("opensearchpy"),
             "configured": aws_conf, "documents": doc_count("aws")},
        ]

    def status(self) -> dict:
        llm_ready = False
        if self.cfg.llm.provider != "none":
            try:
                _, _, llm = self._get_components()
                llm_ready = llm is not None
            except ServiceError:
                pass
        docs = self._registry().load_documents()
        current = self.jobs.current()
        kmap = self.cfg.provider_data_path / "knowledge_map.json"
        return {
            "app": "knowledge-keeper",
            "version": app_version(),
            "platform": {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux"),
            "workspace": str(self.workspace),
            "documents_dir": str(self.documents_dir),
            "reports_dir": str(self.reports_dir),
            "provider": self.cfg.provider,
            "backend_error": self.backend_error,
            "backends": self._backend_rows(),
            "llm": {
                "provider": self.cfg.llm.provider,
                "model": self._llm_model(),
                "ready": llm_ready,
                "error": self.llm_error if self.cfg.llm.provider != "none" else None,
            },
            "keys": [keystore.describe(n) for n in keystore.ALLOWED_KEYS],
            "keystore_path": str(keystore.keystore_path()),
            "document_count": len(docs),
            "has_analysis": kmap.exists(),
            "latest_report": self._latest_report_name(),
            "supported_extensions": SUPPORTED_EXTENSIONS,
            "share": self.share,
            "job": current.to_dict() if current else None,
        }

    # -------------------------------------------------------- documents
    def list_documents(self) -> list[dict]:
        registry = self._registry()
        counts = registry.chunk_counts()
        out = []
        for d in registry.load_documents():
            path = Path(d.path)
            out.append({
                "doc_id": d.doc_id,
                "title": d.title,
                "filename": path.name,
                "path": str(path),
                "type": d.doc_type.value,
                "author": d.author,
                "modified_at": d.modified_at.isoformat() if d.modified_at else None,
                "chunks": counts.get(d.doc_id),
                "in_library": self._in_library(path),
                "file_exists": path.exists(),
            })
        out.sort(key=lambda x: x["title"].lower())
        return out

    def _in_library(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.documents_dir.resolve())
            return True
        except ValueError:
            return False

    def save_upload(self, filename: str, data: bytes) -> Path:
        """Store an uploaded file in the documents folder. Identical re-uploads
        reuse the existing file; a different file with the same name gets a suffix."""
        name = Path(filename or "upload").name
        name = re.sub(r"[^\w.\-() ,'&]+", "_", name).strip() or "upload"
        if Path(name).suffix.lower() not in EXTRACTORS:
            raise ServiceError(
                f"'{name}' isn't a supported type. Supported: {', '.join(SUPPORTED_EXTENSIONS)}"
            )
        target = self.documents_dir / name
        digest = hashlib.sha256(data).hexdigest()
        n = 2
        while target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() == digest:
                return target
            target = self.documents_dir / f"{Path(name).stem} ({n}){Path(name).suffix}"
            n += 1
        target.write_bytes(data)
        return target

    def start_ingest(self, paths: Iterable[Path], label: str) -> Job:
        paths = [Path(p) for p in paths]

        def run(job: Job) -> dict:
            pipeline = self._pipeline()
            totals = {"ingested": 0, "unchanged": 0, "unsupported": 0, "failed": 0, "chunks": 0}
            failures: dict[str, str] = {}
            job.log(f"Indexing {len(paths)} file(s) into the {self.cfg.provider} knowledge base…")
            for i, path in enumerate(paths, start=1):
                job.set_progress(i - 1, len(paths))
                rep = pipeline.ingest_files([path])
                if rep.ingested:
                    totals["ingested"] += 1
                    totals["chunks"] += rep.total_chunks
                    note = f"✓ {path.name} — {rep.total_chunks} passages"
                    if rep.total_chunks == 0:
                        note += " (no text found — if this is a scanned PDF it needs OCR first)"
                    job.log(note)
                elif rep.skipped_unchanged:
                    totals["unchanged"] += 1
                    job.log(f"= {path.name} — already indexed, unchanged")
                elif rep.failed:
                    totals["failed"] += 1
                    failures[path.name] = next(iter(rep.failed.values()))
                    job.log(f"✗ {path.name} — {failures[path.name]}")
                else:
                    totals["unsupported"] += 1
                    job.log(f"– {path.name} — skipped (unsupported type)")
            job.set_progress(len(paths), len(paths))
            job.log(
                f"Done: {totals['ingested']} added, {totals['unchanged']} unchanged, "
                f"{totals['failed']} failed, {totals['chunks']} passages indexed."
            )
            return {**totals, "failures": failures}

        return self.jobs.start("ingest", label, run)

    def start_import_folder(self, folder: str) -> Job:
        root = Path(folder).expanduser()
        if not root.is_dir():
            raise ServiceError(f"Folder not found: {root}")
        files = sorted(
            p for p in root.rglob("*")
            if p.is_file() and not p.name.startswith(("~$", ".")) and p.suffix.lower() in EXTRACTORS
        )
        if not files:
            raise ServiceError(
                f"No supported files in {root}. Supported: {', '.join(SUPPORTED_EXTENSIONS)}"
            )
        return self.start_ingest(files, f"Import folder {root.name}")

    def start_rescan(self) -> Job:
        files = sorted(
            p for p in self.documents_dir.rglob("*")
            if p.is_file() and not p.name.startswith(("~$", ".")) and p.suffix.lower() in EXTRACTORS
        )
        if not files:
            raise ServiceError(
                f"The documents folder is empty. Add files by uploading, or copy them into {self.documents_dir}"
            )
        return self.start_ingest(files, "Scan documents folder")

    def start_demo(self) -> Job:
        from .sample_corpus import generate

        files = generate(self.documents_dir / "demo")
        return self.start_ingest(files, "Load demo documents")

    def remove_document(self, doc_id: str, delete_file: bool = True) -> dict:
        if self.jobs.current():
            raise BusyError("Please wait for the current task to finish.")
        doc = self._pipeline().remove_document(doc_id)
        if doc is None:
            raise ServiceError("That document isn't in the knowledge base.")
        deleted = False
        path = Path(doc.path)
        if delete_file and self._in_library(path) and path.exists():
            path.unlink()
            deleted = True
        return {"removed": doc.title, "file_deleted": deleted}

    # ---------------------------------------------------------------- ask
    def ask(self, question: str, doc_ids: Optional[list[str]] = None, top_k: int = 8) -> dict:
        question = (question or "").strip()
        if not question:
            raise ServiceError("Type a question first.")
        store, embedder, llm = self._get_components()
        engine = RagEngine(self.cfg, store, embedder, llm)
        retrieved = engine.retrieve(question, top_k=top_k, doc_ids=doc_ids)

        passages = [
            {
                "n": i,
                "doc_id": r.chunk.doc_id,
                "doc_title": r.chunk.doc_title,
                "location": r.chunk.location,
                "author": r.chunk.author,
                "modified_at": r.chunk.modified_at.isoformat() if r.chunk.modified_at else None,
                "text": r.chunk.text,
                "score": round(r.score, 3),
            }
            for i, r in enumerate(retrieved, start=1)
        ]
        result = {
            "question": question,
            "mode": "passages",
            "answer": None,
            "cited": [],
            "passages": passages,
            "note": None,
            "scoped": bool(doc_ids),
        }
        if not retrieved:
            result["note"] = "Nothing in the knowledge base matched this question."
            return result
        if llm is None:
            result["note"] = (
                "No AI model is connected, so these are the best-matching passages rather than a written answer. "
                "Connect a free Gemini key in Settings to get answers."
                if self.cfg.llm.provider == "none"
                else f"The AI model isn't ready ({self.llm_error}). Showing the best-matching passages instead."
            )
            return result
        try:
            answer, cited = engine.synthesize(question, retrieved)
        except Exception as exc:
            result["note"] = f"The AI model returned an error, so here are the matching passages instead. {exc}"
            return result
        result.update(mode="answer", answer=answer, cited=cited)
        return result

    # ----------------------------------------------------------- analyze
    def _kmap_path(self) -> Path:
        return self.cfg.provider_data_path / "knowledge_map.json"

    def start_analyze(self, use_llm: bool = True) -> Job:
        def run(job: Job) -> dict:
            return self._analyze(job, use_llm)

        return self.jobs.start("analyze", "Analyze knowledge base", run)

    def _analyze(self, job: Job, use_llm: bool) -> dict:
        from .analysis.gap_analysis import run_gap_analysis
        from .analysis.knowledge_map import build_knowledge_map

        store, embedder, llm = self._get_components()
        if not use_llm or not self.cfg.analysis.use_llm:
            llm = None
        registry = IngestionPipeline(self.cfg, store, embedder)
        documents = registry.load_documents()
        chunks = store.all_chunks()
        if not chunks:
            raise ServiceError("The knowledge base is empty — add documents first.")
        mode = "with the AI model" if llm else "with built-in heuristics (no AI model)"
        job.log(f"Mapping {len(documents)} document(s) / {len(chunks)} passages {mode}…")
        if llm is not None:
            llm.on_status = job.log
        try:
            done = {"n": 0}

            def mapped(title):
                done["n"] += 1
                job.set_progress(done["n"], max(1, len(documents)))
                job.log(f"Mapped: {title}")

            kmap = build_knowledge_map(documents, chunks, llm, progress=mapped)
            job.log("Checking for gaps, single-author topics, stale docs" + (", and contradictions…" if llm else "…"))
            kmap = run_gap_analysis(
                kmap, chunks, self.cfg.analysis, llm,
                progress=lambda t: job.log(f"Compared sources on: {t}"),
            )
        finally:
            if llm is not None:
                llm.on_status = print
        self._kmap_path().write_text(kmap.model_dump_json(indent=2), encoding="utf-8")
        high = sum(1 for f in kmap.findings if f.severity == "high")
        job.log(f"Found {len(kmap.topics)} topics and {len(kmap.findings)} findings ({high} high severity).")
        if len(documents) < 2:
            job.log("Tip: most findings compare documents against each other — add related documents for richer results.")
        return {"topics": len(kmap.topics), "findings": len(kmap.findings), "high": high}

    def get_analysis(self) -> Optional[dict]:
        path = self._kmap_path()
        if not path.exists():
            return None
        kmap = KnowledgeMap(**json.loads(path.read_text(encoding="utf-8")))
        return {
            "generated_at": kmap.generated_at.isoformat(),
            "document_count": len(kmap.documents),
            "topics": [json.loads(t.model_dump_json()) for t in kmap.topics],
            "findings": [json.loads(f.model_dump_json()) for f in kmap.findings],
        }

    # ------------------------------------------------------------ report
    def _latest_report_name(self) -> Optional[str]:
        reports = sorted(self.reports_dir.glob("report-*.md"))
        return reports[-1].name if reports else None

    def latest_report(self) -> Optional[dict]:
        name = self._latest_report_name()
        if not name:
            return None
        path = self.reports_dir / name
        return {"name": name, "path": str(path), "markdown": path.read_text(encoding="utf-8")}

    def start_report(self, use_llm: bool = True) -> Job:
        def run(job: Job) -> dict:
            from .analysis.doc_generator import generate_report

            if not self._kmap_path().exists():
                job.log("No analysis yet — running it first.")
                self._analyze(job, use_llm)
            store, _, llm = self._get_components()
            if not use_llm or not self.cfg.analysis.use_llm:
                llm = None
            kmap = KnowledgeMap(**json.loads(self._kmap_path().read_text(encoding="utf-8")))
            job.log("Writing report" + (" (the AI model drafts a section per documented topic)…" if llm else "…"))
            if llm is not None:
                llm.on_status = job.log
            try:
                md = generate_report(
                    kmap, store.all_chunks(), llm,
                    progress=lambda t: job.log(f"Wrote section: {t}"),
                )
            finally:
                if llm is not None:
                    llm.on_status = print
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = self.reports_dir / f"report-{stamp}.md"
            path.write_text(md, encoding="utf-8")
            job.log(f"Saved {path.name}")
            return {"name": path.name, "path": str(path)}

        return self.jobs.start("report", "Generate report", run)

    # ---------------------------------------------------------- settings
    def get_settings(self) -> dict:
        cfg = self.cfg.model_dump()
        values = {}
        for dotted in EDITABLE_SETTINGS:
            node = cfg
            for part in dotted.split("."):
                node = node[part]
            values[dotted] = node
        return {"provider": self.cfg.provider, "values": values, "llm_providers": list(LLM_PROVIDERS)}

    def update_settings(self, provider: Optional[str] = None, values: Optional[dict] = None) -> dict:
        if self.jobs.current():
            raise BusyError("Please wait for the current task to finish before changing settings.")
        data: dict = {}
        if self.config_path.exists():
            data = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        for dotted, raw in (values or {}).items():
            if dotted not in EDITABLE_SETTINGS:
                raise ServiceError(f"Unknown setting: {dotted}")
            value = EDITABLE_SETTINGS[dotted](raw) if raw is not None else raw
            if dotted == "llm.provider" and value not in LLM_PROVIDERS:
                raise ServiceError(f"Unknown AI provider: {value}")
            node = data
            parts = dotted.split(".")
            for part in parts[:-1]:
                child = node.get(part)
                if not isinstance(child, dict):
                    child = {}
                    node[part] = child
                node = child
            node[parts[-1]] = value
        if provider:
            if provider not in PROVIDERS:
                raise ServiceError(f"Unknown storage backend: {provider}")
            data["provider"] = provider
            set_active_provider(provider, self.cfg.data_dir)
        header = "# Written by the knowledge-keeper app. API keys are NOT stored here.\n"
        self.config_path.write_text(header + yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        self.reload()
        return self.get_settings()

    def save_key(self, name: str, value: str, remember: bool = True) -> dict:
        try:
            keystore.save(name, value, remember=remember)
        except ValueError as exc:
            raise ServiceError(str(exc)) from exc
        self.reload()
        return keystore.describe(name)

    def forget_key(self, name: str) -> dict:
        if name not in keystore.ALLOWED_KEYS:
            raise ServiceError(f"Unsupported key name: {name}")
        keystore.forget(name)
        self.reload()
        return keystore.describe(name)

    def test_llm(self) -> dict:
        if self.cfg.llm.provider == "none":
            return {"ok": False, "error": "No AI provider selected."}
        from .llm import build_llm

        started = time.time()
        try:
            llm = build_llm(self.cfg)
            reply = llm.complete(
                "You are a connectivity check. Reply with exactly one word.", "Say: ready"
            )
            return {"ok": True, "reply": reply.strip()[:200], "seconds": round(time.time() - started, 1)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:600]}

    # ------------------------------------------------------ local extras
    def open_folder(self, which: str) -> dict:
        target = {"workspace": self.workspace, "documents": self.documents_dir,
                  "reports": self.reports_dir}.get(which)
        if target is None:
            raise ServiceError(f"Unknown folder: {which}")
        try:
            if sys.platform == "win32":
                os.startfile(str(target))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                opener = shutil.which("xdg-open") or shutil.which("gio")
                if not opener:
                    raise ServiceError(f"No file manager launcher found. The folder is {target}")
                args = [opener, "open", str(target)] if opener.endswith("gio") else [opener, str(target)]
                subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError(f"Couldn't open the folder ({exc}). It's at {target}") from exc
        return {"opened": str(target)}

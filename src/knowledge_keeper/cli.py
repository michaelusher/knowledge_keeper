"""knowledge-keeper CLI.

  kk ingest ./docs            # extract, chunk, embed, index (incremental)
  kk ask "How does X work?"   # RAG query with citations
  kk analyze                  # build knowledge map + run gap analysis
  kk report                   # generate Markdown system report
  kk serve                    # start the headless JSON API
  kk gui                      # open the web app in your browser (same as kk-gui)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .config import get_active_provider, load_config, set_active_provider
from .factory import build_all
from .ingestion.pipeline import IngestionPipeline
from .models import KnowledgeMap
from .rag import RagEngine

app = typer.Typer(add_completion=False, help="Knowledge preservation: ingest, RAG, gap analysis.")
console = Console()

_CONFIG_OPT = typer.Option(None, "--config", "-c", help="Path to config.yaml")

_state: dict = {"provider": None}


@app.callback()
def main(
    provider: Optional[str] = typer.Option(
        None, "--provider", "-p",
        help="Backend for this invocation: local | azure | aws (overrides `kk use` and config.yaml)",
    ),
):
    if provider and provider not in ("local", "azure", "aws"):
        console.print(f"[red]Unknown provider '{provider}'. Choose: local, azure, aws[/red]")
        raise typer.Exit(1)
    _state["provider"] = provider


def _cfg(config: Optional[str]):
    return load_config(config, provider=_state["provider"])


def _kmap_path(cfg) -> Path:
    return cfg.provider_data_path / "knowledge_map.json"


@app.command()
def use(provider: str, config: Optional[str] = _CONFIG_OPT):
    """Set the default backend for all future commands: local | azure | aws.

    Persists in kk_data/.active_provider. Override per-command with -p."""
    if provider not in ("local", "azure", "aws"):
        console.print(f"[red]Unknown provider '{provider}'. Choose: local, azure, aws[/red]")
        raise typer.Exit(1)
    cfg = load_config(config)
    set_active_provider(provider, cfg.data_dir)
    console.print(f"Default provider set to [bold cyan]{provider}[/bold cyan].")
    console.print(f"[dim]State for each provider is isolated under {cfg.data_dir}/<provider>/ — "
                  f"remember to `kk ingest` after first switching to a new backend.[/dim]")


@app.command()
def status(config: Optional[str] = _CONFIG_OPT):
    """Show active provider, configuration readiness, and state for each backend."""
    import importlib.util

    cfg = _cfg(config)
    persisted = get_active_provider(cfg.data_dir)

    console.print(f"\n[bold]Active provider:[/bold] [cyan]{cfg.provider}[/cyan]"
                  + (f"  [dim](set via `kk use {persisted}`)[/dim]" if persisted == cfg.provider and not _state["provider"] else "")
                  + (f"  [dim](-p flag override)[/dim]" if _state["provider"] else ""))
    console.print(f"[bold]LLM:[/bold] {cfg.llm.provider}\n")

    table = Table(title="Backends")
    table.add_column("Provider")
    table.add_column("Dependencies")
    table.add_column("Configured")
    table.add_column("Ingested docs")

    def _has(mod: str) -> bool:
        try:
            return importlib.util.find_spec(mod) is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            return False

    def _docs_count(provider: str) -> str:
        f = cfg.data_path / provider / "documents.jsonl"
        if not f.exists():
            return "—"
        return str(sum(1 for line in f.read_text(encoding="utf-8").splitlines() if line.strip()))

    azure_deps = _has("azure.search.documents") and _has("openai")
    aws_deps = _has("boto3") and _has("opensearchpy")
    azure_conf = bool(cfg.azure.search_endpoint and "YOUR-" not in cfg.azure.search_endpoint)
    aws_conf = bool(cfg.aws.opensearch_endpoint and "YOUR-" not in cfg.aws.opensearch_endpoint)

    rows = [
        ("local", True, True),
        ("azure", azure_deps, azure_conf),
        ("aws", aws_deps, aws_conf),
    ]
    for name, deps, configured in rows:
        marker = "[cyan]▶[/cyan] " if name == cfg.provider else "  "
        table.add_row(
            f"{marker}{name}",
            "[green]installed[/green]" if deps else f"[yellow]pip install -e '.\\[{name}]'[/yellow]",
            "[green]yes[/green]" if configured else "[yellow]no[/yellow]",
            _docs_count(name),
        )
    console.print(table)

    if cfg.provider == "azure" and not azure_deps:
        console.print("\n[yellow]Install dependencies first: pip install -e '.\\[azure]'[/yellow]")
    elif cfg.provider == "aws" and not aws_deps:
        console.print("\n[yellow]Install dependencies first: pip install -e '.\\[aws]'[/yellow]")
    elif cfg.provider == "azure" and not azure_conf or cfg.provider == "aws" and not aws_conf:
        console.print(f"\n[yellow]Fill in the {cfg.provider} section of config.yaml (endpoints still have placeholders).[/yellow]")
    elif cfg.provider != "local":
        console.print(f"\n[green]Ready.[/green] Verify with a real call: kk ask \"test\"")


@app.command()
def remove(filename: str, config: Optional[str] = _CONFIG_OPT):
    """Remove one document from the index by filename (or a unique part of it)."""
    cfg = _cfg(config)
    store, embedder, _ = build_all(cfg)
    pipeline = IngestionPipeline(cfg, store, embedder)
    docs = pipeline.load_documents()

    matches = [d for d in docs if filename.lower() in Path(d.path).name.lower()]
    if not matches:
        console.print(f"[red]No indexed document matches '{filename}'.[/red] Indexed:")
        for d in docs:
            console.print(f"  - {Path(d.path).name}")
        raise typer.Exit(1)
    if len(matches) > 1:
        console.print(f"[yellow]'{filename}' matches multiple documents — be more specific:[/yellow]")
        for d in matches:
            console.print(f"  - {Path(d.path).name}")
        raise typer.Exit(1)

    doc = matches[0]
    had_map = _kmap_path(cfg).exists()
    pipeline.remove_document(doc.doc_id)
    remaining = pipeline.load_documents()
    if had_map:
        console.print("[dim]Knowledge map invalidated — run `kk analyze` to rebuild.[/dim]")
    console.print(f"[green]Removed[/green] {Path(doc.path).name} ({len(remaining)} document(s) remain).")


@app.command()
def ingest(source_dir: str, config: Optional[str] = _CONFIG_OPT):
    """Ingest all supported files under SOURCE_DIR (pptx, docx, pdf, md, txt)."""
    cfg = _cfg(config)
    store, embedder, _ = build_all(cfg)
    pipeline = IngestionPipeline(cfg, store, embedder)

    console.print(f"[bold]Ingesting[/bold] {source_dir} -> provider=[cyan]{cfg.provider}[/cyan]")
    report = pipeline.ingest_directory(
        source_dir,
        progress=lambda path, n: console.print(f"  [green]✓[/green] {Path(path).name} ({n} chunks)"),
    )
    for path in report.skipped_unchanged:
        console.print(f"  [dim]= {Path(path).name} (unchanged)[/dim]")
    for path, err in report.failed.items():
        console.print(f"  [red]✗ {Path(path).name}: {err}[/red]")
    console.print(
        f"\n[bold]{len(report.ingested)}[/bold] ingested, {len(report.skipped_unchanged)} unchanged, "
        f"{len(report.skipped_unsupported)} unsupported, {len(report.failed)} failed, "
        f"[bold]{report.total_chunks}[/bold] chunks indexed."
    )


@app.command()
def ask(question: str, top_k: int = 8, config: Optional[str] = _CONFIG_OPT):
    """Ask a question against the knowledge base."""
    cfg = _cfg(config)
    store, embedder, llm = build_all(cfg)
    engine = RagEngine(cfg, store, embedder, llm)
    result = engine.query(question, top_k=top_k)

    console.print(f"\n[bold]{result.answer}[/bold]\n")
    if result.citations:
        table = Table(title="Sources")
        table.add_column("Document")
        table.add_column("Location")
        for c in result.citations:
            table.add_row(c.doc_title, c.location)
        console.print(table)


@app.command()
def analyze(config: Optional[str] = _CONFIG_OPT, no_llm: bool = typer.Option(False, help="Force heuristic-only analysis")):
    """Build the knowledge map and run gap analysis. Writes knowledge_map.json."""
    from .analysis.gap_analysis import run_gap_analysis
    from .analysis.knowledge_map import build_knowledge_map

    cfg = _cfg(config)
    store, embedder, llm = build_all(cfg)
    if no_llm or not cfg.analysis.use_llm:
        llm = None
    pipeline = IngestionPipeline(cfg, store, embedder)
    documents = pipeline.load_documents()
    chunks = store.all_chunks()
    if not chunks:
        console.print("[red]No chunks in the store. Run `kk ingest` first.[/red]")
        raise typer.Exit(1)

    mode = "LLM-assisted" if llm else "heuristic (no LLM)"
    console.print(f"Building knowledge map from {len(documents)} docs / {len(chunks)} chunks ({mode})…")
    kmap = build_knowledge_map(documents, chunks, llm, progress=lambda t: console.print(f"  [dim]mapped: {t}[/dim]"))
    kmap = run_gap_analysis(kmap, chunks, cfg.analysis, llm, progress=lambda t: console.print(f"  [dim]checked conflicts: {t}[/dim]"))

    out = _kmap_path(cfg)
    out.write_text(kmap.model_dump_json(indent=2), encoding="utf-8")
    console.print(f"\n[green]Knowledge map written to {out}[/green]")

    high = sum(1 for f in kmap.findings if f.severity == "high")
    console.print(f"{len(kmap.topics)} topics, [bold]{len(kmap.findings)}[/bold] findings ([red]{high} high[/red]).")
    for f in kmap.findings[:10]:
        console.print(f"  [{'red' if f.severity == 'high' else 'yellow'}]{f.severity.upper():6}[/] {f.title}")


@app.command()
def report(
    output: str = typer.Option("system_report.md", "--output", "-o"),
    config: Optional[str] = _CONFIG_OPT,
    no_llm: bool = typer.Option(False, help="Skip LLM-synthesized sections"),
):
    """Generate the Markdown system documentation + gap report."""
    from .analysis.doc_generator import generate_report

    cfg = _cfg(config)
    store, _, llm = build_all(cfg)
    if no_llm or not cfg.analysis.use_llm:
        llm = None
    kmap_file = _kmap_path(cfg)
    if not kmap_file.exists():
        console.print("[red]No knowledge map found. Run `kk analyze` first.[/red]")
        raise typer.Exit(1)
    kmap = KnowledgeMap(**json.loads(kmap_file.read_text(encoding="utf-8")))
    chunks = store.all_chunks()

    md = generate_report(kmap, chunks, llm, progress=lambda t: console.print(f"  [dim]wrote section: {t}[/dim]"))
    Path(output).write_text(md, encoding="utf-8")
    console.print(f"[green]Report written to {output}[/green]")


@app.command()
def gui(
    share: bool = typer.Option(False, help="Allow other devices on your network (access code required)"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't open a browser window"),
    port: int = typer.Option(8765),
    workspace: Optional[str] = typer.Option(None, help="Knowledge-base folder (default: ~/KnowledgeKeeper)"),
    install_shortcut: bool = typer.Option(False, "--install-shortcut", help="Add Knowledge Keeper to your app menu and exit"),
):
    """Open Knowledge Keeper as a local website in your browser."""
    from .gui import main as gui_main

    argv = ["--port", str(port)]
    if share:
        argv.append("--share")
    if no_browser:
        argv.append("--no-browser")
    if workspace:
        argv += ["--workspace", workspace]
    if install_shortcut:
        argv.append("--install-shortcut")
    raise typer.Exit(gui_main(argv))


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8000, config: Optional[str] = _CONFIG_OPT):
    """Run the HTTP API (docs at /docs)."""
    import os

    import uvicorn

    if config:
        os.environ["KK_CONFIG_PATH"] = config
    if _state["provider"]:
        os.environ["KK_PROVIDER"] = _state["provider"]
    uvicorn.run("knowledge_keeper.api.app:app", host=host, port=port)


if __name__ == "__main__":
    app()

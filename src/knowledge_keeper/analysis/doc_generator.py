"""Generate system documentation + gap report (Markdown) from the knowledge map.

With an LLM, each well-covered topic gets a synthesized section written from
its actual source passages with per-source attribution. Without one, the
report is inventory + findings only.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from ..llm import LlmClient
from ..models import Chunk, KnowledgeMap

_WRITER_SYSTEM = """You write a concise section of internal system documentation from source passages.
Rules: only state what the passages support; attribute claims inline like (source: <doc title>);
note explicitly if sources disagree; 150-300 words; plain prose, no headers."""

_SEVERITY_ICON = {"high": "🔴", "medium": "🟠", "low": "🟡"}


def _findings_table(kmap: KnowledgeMap) -> list[str]:
    lines = []
    if not kmap.findings:
        return ["_No findings — either the corpus is in great shape or the analysis hasn't run with enough signal._"]
    by_kind: dict[str, int] = defaultdict(int)
    for f in kmap.findings:
        by_kind[f.kind] += 1
    summary = ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in sorted(by_kind.items()))
    lines.append(f"**{len(kmap.findings)} findings** ({summary})\n")
    for f in kmap.findings:
        icon = _SEVERITY_ICON.get(f.severity, "⚪")
        lines.append(f"### {icon} {f.title}")
        lines.append(f"*{f.kind.replace('_', ' ')} — {f.severity} severity*\n")
        lines.append(f.detail)
        if f.doc_titles:
            lines.append(f"\nRelated documents: {', '.join(sorted(set(f.doc_titles)))}")
        lines.append("")
    return lines


def generate_report(
    kmap: KnowledgeMap,
    chunks: list[Chunk],
    llm: Optional[LlmClient] = None,
    max_llm_sections: int = 25,
    progress=None,
) -> str:
    now = datetime.now(timezone.utc)
    lines: list[str] = [
        "# System Knowledge Report",
        f"_Generated {now.date().isoformat()} from {len(kmap.documents)} documents / {len(chunks)} chunks._",
        "",
        "## 1. Document Inventory",
        "",
        "| Document | Type | Author | Last Modified |",
        "|---|---|---|---|",
    ]
    for d in sorted(kmap.documents, key=lambda d: d.title.lower()):
        modified = d.modified_at.date().isoformat() if d.modified_at else "—"
        lines.append(f"| {d.title} | {d.doc_type.value} | {d.author or '—'} | {modified} |")

    lines += ["", "## 2. Knowledge Coverage", ""]
    explained = [t for t in kmap.topics if t.explained]
    mentioned_only = [t for t in kmap.topics if not t.explained]
    lines.append(f"{len(explained)} topics documented; {len(mentioned_only)} mentioned but not documented.")
    lines += ["", "| Topic | Kind | Documented? | Docs | Authors | Freshest |", "|---|---|---|---|---|---|"]
    for t in kmap.topics[:60]:
        fresh = t.newest_modified.date().isoformat() if t.newest_modified else "—"
        lines.append(
            f"| {t.topic} | {t.kind} | {'yes' if t.explained else '**no**'} | "
            f"{len(t.doc_ids)} | {', '.join(t.authors) or '—'} | {fresh} |"
        )

    # LLM-synthesized topic sections
    if llm is not None and explained:
        lines += ["", "## 3. System Documentation (synthesized)", ""]
        chunks_by_doc: dict[str, list[Chunk]] = defaultdict(list)
        for c in chunks:
            chunks_by_doc[c.doc_id].append(c)
        for t in explained[:max_llm_sections]:
            key = t.topic.lower()
            passages = []
            for doc_id, title in zip(t.doc_ids, t.doc_titles):
                relevant = [c for c in chunks_by_doc.get(doc_id, []) if key in c.text.lower()]
                text = "\n".join(c.text for c in relevant[:3])[:2500]
                if text:
                    passages.append(f"--- {title}:\n{text}")
            if not passages:
                continue
            section = llm.complete(
                _WRITER_SYSTEM,
                f"Topic: {t.topic} ({t.kind})\n\nSource passages:\n\n" + "\n\n".join(passages),
            )
            if progress:
                progress(t.topic)
            lines += [f"### {t.topic}", "", section.strip(), ""]

    section_num = "4" if llm is not None else "3"
    lines += ["", f"## {section_num}. Gaps, Risks & Issues", ""]
    lines += _findings_table(kmap)
    return "\n".join(lines)

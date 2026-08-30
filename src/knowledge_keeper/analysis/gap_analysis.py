"""Gap & risk analysis over the knowledge map.

Heuristic passes (always run):
  1. Coverage gaps  — topics mentioned repeatedly but never explained anywhere.
  2. Bus factor     — topics whose entire documentation traces to <= N authors.
  3. Staleness      — topics whose newest covering document is older than a cutoff.
  4. Single source  — topics documented in exactly one document (no redundancy).
  5. Orphan docs    — documents with no author metadata (untraceable knowledge).

LLM pass (when configured):
  6. Conflict detection — for topics covered by multiple documents, compare the
     relevant passages and flag contradictions (versions, procedures, owners).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..config import AnalysisConfig
from ..llm import LlmClient
from ..models import Chunk, Finding, KnowledgeMap

_CONFLICT_SYSTEM = """You compare passages from different organizational documents about the same topic.
Identify genuine contradictions: different procedures for the same task, different stated owners,
different versions/values presented as current, incompatible instructions.
Return JSON: {"conflicts": [{"description": "one-sentence description of the contradiction",
"severity": "high"|"medium"|"low"}]}
Return {"conflicts": []} if there is no real contradiction. Do not flag mere differences in detail level."""


def run_gap_analysis(
    kmap: KnowledgeMap,
    chunks: list[Chunk],
    cfg: AnalysisConfig,
    llm: Optional[LlmClient] = None,
    progress=None,
) -> KnowledgeMap:
    findings: list[Finding] = []
    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(days=cfg.stale_days)

    multi_doc_topics = [t for t in kmap.topics if len(t.doc_ids) >= 2]

    for t in kmap.topics:
        # 1. mentioned often, explained nowhere
        if not t.explained and t.mention_count >= cfg.min_mentions_for_gap:
            findings.append(Finding(
                kind="gap",
                severity="high" if t.mention_count >= 2 * cfg.min_mentions_for_gap else "medium",
                title=f"'{t.topic}' is referenced but never documented",
                detail=(
                    f"Referenced {t.mention_count} time(s) across {len(t.doc_ids)} document(s) "
                    f"but no document actually explains it. Knowledge likely lives only in people's heads."
                ),
                topic=t.topic, doc_ids=t.doc_ids, doc_titles=t.doc_titles,
            ))

        # 2. bus factor — only for topics that matter across the corpus
        # (single-doc topics are already covered by the redundancy check below)
        if (
            t.explained
            and t.authors
            and len(t.authors) <= cfg.bus_factor_threshold
            and len(t.doc_ids) >= 2
        ):
            findings.append(Finding(
                kind="bus_factor",
                severity="high" if len(t.doc_ids) == 1 else "medium",
                title=f"'{t.topic}' depends on a single author",
                detail=(
                    f"All documentation for this topic traces to {', '.join(t.authors)}. "
                    f"If they leave, this knowledge has no other documented custodian."
                ),
                topic=t.topic, doc_ids=t.doc_ids, doc_titles=t.doc_titles,
            ))

        # 3. staleness
        if t.explained and t.newest_modified and t.newest_modified < stale_cutoff:
            age_days = (now - t.newest_modified).days
            findings.append(Finding(
                kind="stale",
                severity="medium" if age_days < 2 * cfg.stale_days else "high",
                title=f"'{t.topic}' documentation is {age_days // 30} months old",
                detail=(
                    f"Newest covering document was last modified {t.newest_modified.date().isoformat()}. "
                    f"Verify it still reflects the current system."
                ),
                topic=t.topic, doc_ids=t.doc_ids, doc_titles=t.doc_titles,
            ))

        # 4. single source of truth (no redundancy)
        if t.explained and len(t.doc_ids) == 1 and t.mention_count >= 2:
            findings.append(Finding(
                kind="gap",
                severity="low",
                title=f"'{t.topic}' has a single point of documentation",
                detail=f"Documented only in '{t.doc_titles[0]}'. Loss or rot of that one file loses the topic.",
                topic=t.topic, doc_ids=t.doc_ids, doc_titles=t.doc_titles,
            ))

    # 5. orphan documents
    for d in kmap.documents:
        if not d.author:
            findings.append(Finding(
                kind="orphan",
                severity="low",
                title=f"'{d.title}' has no author metadata",
                detail="No author recorded — no one to ask when the content stops making sense.",
                doc_ids=[d.doc_id], doc_titles=[d.title],
            ))

    # 6. LLM conflict detection on multi-document topics
    if llm is not None and multi_doc_topics:
        chunks_by_doc: dict[str, list[Chunk]] = defaultdict(list)
        for c in chunks:
            chunks_by_doc[c.doc_id].append(c)

        for t in multi_doc_topics[:40]:  # bound the number of LLM calls
            passages = []
            key = t.topic.lower()
            for doc_id, title in zip(t.doc_ids, t.doc_titles):
                relevant = [c for c in chunks_by_doc.get(doc_id, []) if key in c.text.lower()]
                text = "\n".join(c.text for c in relevant[:3])[:3000]
                if text:
                    passages.append(f"--- From '{title}':\n{text}")
            if len(passages) < 2:
                continue
            payload = llm.complete_json(
                _CONFLICT_SYSTEM,
                f"Topic: {t.topic}\n\n" + "\n\n".join(passages),
            )
            if progress:
                progress(t.topic)
            if isinstance(payload, dict):
                for c in payload.get("conflicts", []):
                    if isinstance(c, dict) and c.get("description"):
                        findings.append(Finding(
                            kind="conflict",
                            severity=str(c.get("severity", "medium")),
                            title=f"Conflicting documentation on '{t.topic}'",
                            detail=str(c["description"]),
                            topic=t.topic, doc_ids=t.doc_ids, doc_titles=t.doc_titles,
                        ))

    severity_rank = {"high": 0, "medium": 1, "low": 2}
    kmap.findings = sorted(findings, key=lambda f: (severity_rank.get(f.severity, 3), f.kind))
    return kmap

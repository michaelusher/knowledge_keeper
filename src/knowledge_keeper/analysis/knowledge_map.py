"""Build a corpus-wide knowledge map: which systems/procedures/topics exist,
which documents cover them, who wrote them, and how fresh coverage is.

Two modes:
- LLM mode: per-document structured extraction (systems, procedures, roles,
  plus topics *mentioned* vs *explained*) merged into corpus-wide coverage.
- Heuristic mode (no LLM): TF-IDF salient-term extraction. Coarser, but
  still powers staleness/bus-factor/coverage analysis offline.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Optional

from ..llm import LlmClient
from ..models import Chunk, KnowledgeMap, SourceDocument, TopicCoverage

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9\-]{2,}")
_STOP = frozenset(
    """the and for that with this from are was were will would can could should has have had not you your our their
    all any each which when where what how why who been being also more most other some such than then there these
    those into over under between during before after above below out off own same too very just don now use used
    using new one two may might must shall its it's per via etc""".split()
)

_EXTRACT_SYSTEM = """You analyze an organizational document and extract a structured knowledge inventory.
Return JSON with this exact shape:
{
  "topics": [
    {
      "name": "short canonical name",
      "kind": "system" | "procedure" | "role" | "topic",
      "explained": true if this document actually documents/explains it, false if merely mentioned,
      "summary": "one sentence on what this document says about it (or null if merely mentioned)"
    }
  ]
}
Guidelines: canonicalize names (e.g. "the billing system", "BillSys" -> one name you choose consistently);
8-20 topics per document; prefer nouns for systems, verb phrases for procedures."""


def _extract_topics_llm(llm: LlmClient, doc: SourceDocument, text: str) -> list[dict]:
    payload = llm.complete_json(
        _EXTRACT_SYSTEM,
        f"Document: {doc.title} (type: {doc.doc_type.value}, author: {doc.author or 'unknown'})\n\n{text[:24000]}",
    )
    if isinstance(payload, dict) and isinstance(payload.get("topics"), list):
        return [t for t in payload["topics"] if isinstance(t, dict) and t.get("name")]
    return []


def _extract_topics_tfidf(doc_texts: dict[str, str], top_n: int = 12) -> dict[str, list[dict]]:
    """Salient terms per doc via TF-IDF over the corpus. `explained` is
    approximated: a term is 'explained' in the doc where it is most salient."""
    tf: dict[str, Counter] = {}
    df: Counter = Counter()
    for doc_id, text in doc_texts.items():
        words = [w.lower() for w in _WORD.findall(text) if w.lower() not in _STOP]
        # unigrams + bigrams
        grams = words + [f"{a} {b}" for a, b in zip(words, words[1:])]
        counts = Counter(grams)
        tf[doc_id] = counts
        df.update(set(counts))

    n_docs = max(1, len(doc_texts))
    scores: dict[str, dict[str, float]] = {}
    for doc_id, counts in tf.items():
        scores[doc_id] = {
            term: (1 + math.log(c)) * math.log(1 + n_docs / df[term])
            for term, c in counts.items()
            if c >= 2
        }

    best_doc_for_term: dict[str, str] = {}
    for doc_id, s in scores.items():
        for term, val in s.items():
            if term not in best_doc_for_term or val > scores[best_doc_for_term[term]].get(term, 0):
                best_doc_for_term[term] = doc_id

    out: dict[str, list[dict]] = {}
    for doc_id, s in scores.items():
        # Keep only terms with corpus-level significance: present in >= 2 docs,
        # or salient bigrams (multi-word terms are usually real concepts).
        filtered = {t: v for t, v in s.items() if df[t] >= 2 or (" " in t and v > 2.0)}
        top = sorted(filtered.items(), key=lambda kv: -kv[1])[:top_n]
        out[doc_id] = [
            {
                "name": term,
                "kind": "topic",
                "explained": best_doc_for_term.get(term) == doc_id,
                "summary": None,
            }
            for term, _ in top
        ]
    return out


def _canonical(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def build_knowledge_map(
    documents: list[SourceDocument],
    chunks: list[Chunk],
    llm: Optional[LlmClient],
    progress=None,
) -> KnowledgeMap:
    doc_texts: dict[str, str] = defaultdict(str)
    for c in sorted(chunks, key=lambda c: (c.doc_id, c.ordinal)):
        doc_texts[c.doc_id] += c.text + "\n"

    per_doc_topics: dict[str, list[dict]] = {}
    if llm is not None:
        for doc in documents:
            text = doc_texts.get(doc.doc_id, "")
            if not text.strip():
                continue
            per_doc_topics[doc.doc_id] = _extract_topics_llm(llm, doc, text)
            if progress:
                progress(doc.title)
    else:
        per_doc_topics = _extract_topics_tfidf({d.doc_id: doc_texts[d.doc_id] for d in documents if doc_texts.get(d.doc_id)})

    docs_by_id = {d.doc_id: d for d in documents}
    coverage: dict[str, TopicCoverage] = {}
    display_name: dict[str, str] = {}

    for doc_id, topics in per_doc_topics.items():
        doc = docs_by_id.get(doc_id)
        if doc is None:
            continue
        for t in topics:
            key = _canonical(str(t["name"]))
            if not key:
                continue
            display_name.setdefault(key, str(t["name"]))
            cov = coverage.setdefault(
                key,
                TopicCoverage(topic=display_name[key], kind=str(t.get("kind", "topic"))),
            )
            cov.mention_count += 1
            if doc_id not in cov.doc_ids:
                cov.doc_ids.append(doc_id)
                cov.doc_titles.append(doc.title)
            if doc.author and doc.author not in cov.authors:
                cov.authors.append(doc.author)
            if t.get("explained"):
                cov.explained = True
                if t.get("summary") and not cov.summary:
                    cov.summary = str(t["summary"])
            if doc.modified_at and (cov.newest_modified is None or doc.modified_at > cov.newest_modified):
                cov.newest_modified = doc.modified_at

    topics_sorted = sorted(coverage.values(), key=lambda t: (-len(t.doc_ids), -t.mention_count))
    return KnowledgeMap(documents=documents, topics=topics_sorted)

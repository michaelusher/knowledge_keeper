"""File-type-specific extractors.

Each extractor returns (SourceDocument, list[RawSection]) where a RawSection
is a (location, text) pair — e.g. ("slide 4", "..."), ("page 2", "...").
Locations survive all the way into citations, and document metadata
(author, modified date) survives into gap analysis.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from ..models import DocType, SourceDocument


@dataclass
class RawSection:
    location: str
    text: str


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _fs_times(path: Path) -> tuple[datetime, datetime]:
    st = path.stat()
    return (
        datetime.fromtimestamp(st.st_ctime, tz=timezone.utc),
        datetime.fromtimestamp(st.st_mtime, tz=timezone.utc),
    )


def _base_doc(path: Path, doc_type: DocType, title: Optional[str] = None) -> SourceDocument:
    sha = _sha256(path)
    created, modified = _fs_times(path)
    return SourceDocument(
        doc_id=SourceDocument.make_id(str(path), sha),
        path=str(path),
        title=title or path.stem.replace("_", " ").replace("-", " ").strip(),
        doc_type=doc_type,
        created_at=created,
        modified_at=modified,
        sha256=sha,
    )


def _clean(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------- PPTX -----

def extract_pptx(path: Path) -> tuple[SourceDocument, list[RawSection]]:
    from pptx import Presentation

    prs = Presentation(str(path))
    doc = _base_doc(path, DocType.PPTX)

    core = prs.core_properties
    if core.author:
        doc.author = core.author
    if core.title:
        doc.title = core.title
    if core.modified:
        doc.modified_at = core.modified.replace(tzinfo=core.modified.tzinfo or timezone.utc)
    if core.created:
        doc.created_at = core.created.replace(tzinfo=core.created.tzinfo or timezone.utc)

    sections: list[RawSection] = []
    for i, slide in enumerate(prs.slides, start=1):
        parts: list[str] = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                t = shape.text_frame.text.strip()
                if t:
                    parts.append(t)
            if getattr(shape, "has_table", False) and shape.has_table:
                for row in shape.table.rows:
                    cells = [c.text.strip() for c in row.cells]
                    parts.append(" | ".join(c for c in cells if c))
        # Speaker notes often carry the *actual* knowledge on training decks.
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                parts.append(f"[Speaker notes] {notes}")
        text = _clean("\n".join(parts))
        if text:
            sections.append(RawSection(location=f"slide {i}", text=text))
    return doc, sections


# ---------------------------------------------------------------- DOCX -----

def extract_docx(path: Path) -> tuple[SourceDocument, list[RawSection]]:
    import docx

    d = docx.Document(str(path))
    doc = _base_doc(path, DocType.DOCX)

    core = d.core_properties
    if core.author:
        doc.author = core.author
    if core.title:
        doc.title = core.title
    if core.modified:
        doc.modified_at = core.modified.replace(tzinfo=core.modified.tzinfo or timezone.utc)

    sections: list[RawSection] = []
    current_heading = "Introduction"
    buf: list[str] = []

    def flush():
        text = _clean("\n".join(buf))
        if text:
            sections.append(RawSection(location=f"section: {current_heading}", text=text))
        buf.clear()

    for para in d.paragraphs:
        style = (para.style.name or "").lower() if para.style else ""
        text = para.text.strip()
        if not text:
            continue
        if style.startswith("heading"):
            flush()
            current_heading = text
            buf.append(text)
        else:
            buf.append(text)
    for table in d.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            buf.append(" | ".join(c for c in cells if c))
    flush()
    return doc, sections


# ----------------------------------------------------------------- PDF -----

def extract_pdf(path: Path) -> tuple[SourceDocument, list[RawSection]]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    doc = _base_doc(path, DocType.PDF)

    meta = reader.metadata or {}
    author = str(meta.get("/Author") or "").strip()
    if author and author.lower() not in {"(anonymous)", "anonymous", "unknown"}:
        doc.author = author
    title = str(meta.get("/Title") or "").strip()
    # Junk titles are common in PDFs: blank, "(anonymous)", "untitled", tool names.
    if title and title.lower() not in {"(anonymous)", "anonymous", "untitled", "unknown"}:
        doc.title = title

    sections: list[RawSection] = []
    for i, page in enumerate(reader.pages, start=1):
        text = _clean(page.extract_text() or "")
        if text:
            sections.append(RawSection(location=f"page {i}", text=text))
    return doc, sections


# ------------------------------------------------------- Markdown/Text -----

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def extract_markdown(path: Path) -> tuple[SourceDocument, list[RawSection]]:
    doc = _base_doc(path, DocType.MARKDOWN)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    # A leading "# Title" names the document better than its filename does.
    first = next((ln.strip() for ln in lines if ln.strip()), "")
    if first.startswith("# "):
        doc.title = first[2:].strip() or doc.title

    sections: list[RawSection] = []
    heading = doc.title
    buf: list[str] = []

    def flush():
        text = _clean("\n".join(buf))
        if text:
            sections.append(RawSection(location=f"section: {heading}", text=text))
        buf.clear()

    for line in lines:
        m = _MD_HEADING.match(line)
        if m:
            flush()
            heading = m.group(2).strip()
            buf.append(heading)
        else:
            buf.append(line)
    flush()
    return doc, sections


def extract_text(path: Path) -> tuple[SourceDocument, list[RawSection]]:
    doc = _base_doc(path, DocType.TEXT)
    text = _clean(path.read_text(encoding="utf-8", errors="replace"))
    return doc, [RawSection(location="full text", text=text)] if text else (doc, [])


# ------------------------------------------------------------- Registry ----

EXTRACTORS: dict[str, Callable[[Path], tuple[SourceDocument, list[RawSection]]]] = {
    ".pptx": extract_pptx,
    ".docx": extract_docx,
    ".pdf": extract_pdf,
    ".md": extract_markdown,
    ".markdown": extract_markdown,
    ".txt": extract_text,
}


def extract(path: Path) -> Optional[tuple[SourceDocument, list[RawSection]]]:
    fn = EXTRACTORS.get(path.suffix.lower())
    if fn is None:
        return None
    return fn(path)

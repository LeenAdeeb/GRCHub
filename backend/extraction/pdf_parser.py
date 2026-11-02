"""PDF text extraction preserving physical and printed page numbers and line indentation (PyMuPDF)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf

_PAGE_OF = re.compile(r"^\s*Page\s+(\d+)\s+of\s+\d+\s*$", re.M)


@dataclass(frozen=True)
class Page:
    index: int                              # 1-based physical page
    label: str | None                       # page number printed on the page, if detected
    text: str                               # plain text in PDF content order
    lines: tuple[tuple[float, str], ...]    # (x0, text) per line, same order; x0 = left indent


def _label(text: str) -> str | None:
    m = _PAGE_OF.search(text)
    if m:
        return m.group(1)
    for line in text.splitlines()[:14]:
        if line.strip().isdigit():
            return line.strip()
    return None


def extract_pages(pdf_path: Path) -> list[Page]:
    """Pages with no text layer come back with empty text so callers can detect that OCR is needed."""
    pages = []
    with pymupdf.open(pdf_path) as doc:
        for i, p in enumerate(doc, 1):
            text = p.get_text("text")
            lines = []
            for block in p.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    t = "".join(s["text"] for s in line["spans"]).strip()
                    if t:
                        lines.append((line["bbox"][0], t))
            pages.append(Page(i, _label(text), text, tuple(lines)))
    return pages

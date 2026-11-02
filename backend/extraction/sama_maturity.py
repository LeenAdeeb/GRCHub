"""Extraction of the SAMA CSF maturity model (section 2.4) from the real PDF.

Table 1 has three columns: level (x0 < 140), "Definition and Criteria" (140 <= x0 < 350) and
"Explanation" (x0 >= 350). Bullets start with "•"; other lines continue the previous bullet of the
same column. Sections 2.4, 2.4.1, 2.4.2 and 2.4.3 are narrative text, stored verbatim.
Whitespace runs are collapsed to a single space; no words are changed.
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

from backend.extraction.common import word_diff
from backend.extraction.pdf_parser import Page
from backend.extraction.sama import page_lines

CRITERIA_X0 = 140.0
EXPLANATION_X0 = 350.0
SECTION_HEAD = re.compile(r"^(2\.4(?:\.\d)?)\s+(\S.*)$")


class MaturityLevel(BaseModel):
    level: int
    name: str
    criteria: list[str]
    explanation: list[str]
    source_document_sha256: str
    page_index: int
    page_label: str | None
    extraction_confidence: float = Field(ge=0, le=1)
    parse_notes: list[str] = Field(default_factory=list)


class Section(BaseModel):
    section_id: str
    title: str
    original_text: str
    source_document_sha256: str
    page_index: int
    page_label: str | None


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _region(pages: list[Page]) -> list[tuple[Page, float, str]]:
    """Lines of section 2.4 up to (not including) '3 Control domains'."""
    out: list[tuple[Page, float, str]] = []
    started = False
    for page in pages:
        for x0, line in page_lines(page):
            if not started and SECTION_HEAD.match(line) and line.startswith("2.4 "):
                started = True
            if started and line == "3 Control domains":
                return out
            if started:
                out.append((page, x0, line))
    return out


def extract_maturity(pages: list[Page], source_sha256: str) -> tuple[list[MaturityLevel], list[Section], list[str]]:
    """Returns (levels, sections, skipped_lines). skipped_lines are labels not stored as content."""
    region = _region(pages)
    if not region:
        raise ValueError("section 2.4 not found")
    skipped: list[str] = []
    levels: list[MaturityLevel] = []
    sections: list[Section] = []

    # ---- table
    start = next(i for i, (_, x, t) in enumerate(region) if t == "Explanation" and x >= EXPLANATION_X0)
    end = next(i for i, (_, _, t) in enumerate(region) if t.startswith("Table 1 - "))
    for _, _, t in region[start - 2:start + 1]:
        skipped.append(t)              # "Maturity Level", "Definition and Criteria", "Explanation"
    skipped.append(region[end][2])     # table caption
    cur: dict | None = None

    def flush() -> None:
        nonlocal cur
        if cur:
            levels.append(MaturityLevel(
                level=cur["level"], name=_norm(" ".join(cur["name"])),
                criteria=[_norm(b) for b in cur["criteria"]], explanation=[_norm(b) for b in cur["explanation"]],
                source_document_sha256=source_sha256, page_index=cur["page"].index, page_label=cur["page"].label,
                extraction_confidence=0.9 if cur["criteria"] and cur["explanation"] else 0.5,
                parse_notes=cur["notes"]))
        cur = None

    for page, x0, line in region[start + 1:end]:
        if x0 < CRITERIA_X0:
            if line.isdigit():
                flush()
                cur = {"level": int(line), "name": [], "criteria": [], "explanation": [], "page": page, "notes": []}
            elif cur:
                cur["name"].append(line)
            continue
        if cur is None:
            continue
        column = "criteria" if x0 < EXPLANATION_X0 else "explanation"
        if line.startswith("•"):
            text = line.lstrip("• \t")
            bullets = cur[column]
            if bullets and not re.search(r"[.;:!?]$", bullets[-1]) and text[:1].islower():
                # The source prints a stray bullet in the middle of a sentence; keep the sentence whole.
                bullets[-1] += " " + text
                cur["notes"].append(f"Source prints a stray bullet before '{text[:25]}'; merged into the previous bullet")
            else:
                bullets.append(text)
        elif cur[column]:
            cur[column][-1] += " " + line
    flush()

    # ---- narrative sections
    heading_idx = [i for i, (_, _, t) in enumerate(region) if SECTION_HEAD.match(t)]
    blocks: dict[str, dict] = {}
    current: str | None = None
    for i, (page, x0, line) in enumerate(region):
        if start - 2 <= i <= end:
            continue                    # table and its caption
        m = SECTION_HEAD.match(line) if i in heading_idx else None
        if m:
            current = m.group(1)
            blocks[current] = {"title": m.group(2).strip(), "lines": [], "page": page}
            continue                    # the heading is stored as the section id and title
        if current:
            blocks[current]["lines"].append(line)
    for sid, b in blocks.items():
        sections.append(Section(section_id=sid, title=b["title"], original_text=_norm(" ".join(b["lines"])),
                                source_document_sha256=source_sha256, page_index=b["page"].index,
                                page_label=b["page"].label))
    return levels, sections, skipped


def validate_maturity(pages: list[Page], levels: list[MaturityLevel], sections: list[Section],
                      skipped: list[str]) -> list[str]:
    issues: list[str] = []
    if [l.level for l in levels] != list(range(len(levels))) or len(levels) != 6:
        issues.append(f"expected levels 0-5 in order, found {[l.level for l in levels]}")
    for l in levels:
        if not l.name or not l.criteria or not l.explanation:
            issues.append(f"level {l.level}: missing name, criteria or explanation")
    expected_sections = ["2.4", "2.4.1", "2.4.2", "2.4.3"]
    if [s.section_id for s in sections] != expected_sections:
        issues.append(f"expected sections {expected_sections}, found {[s.section_id for s in sections]}")
    for s in sections:
        if not s.original_text:
            issues.append(f"section {s.section_id}: empty text")

    lines = [t for _, _, t in _region(pages)]
    texts = [f"{l.level} {l.name} " + " ".join(l.criteria) + " " + " ".join(l.explanation) for l in levels]
    texts += [f"{s.section_id} {s.title} {s.original_text}" for s in sections] + skipped
    lost, extra = word_diff(lines, texts)
    if lost:
        issues.append(f"source words not accounted for: {dict(list(lost.items())[:25])}")
    if extra:
        issues.append(f"output words not in source: {dict(list(extra.items())[:25])}")
    return issues

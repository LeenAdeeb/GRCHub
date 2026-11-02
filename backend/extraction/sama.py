"""Deterministic parser for the SAMA Cyber Security Framework v1.0 (English PDF).

Section 3 holds the control domains. Each subdomain (e.g. 3.1.2) has a Principle, an Objective and
numbered "Control considerations". The PDF prints no ID per consideration, so the ID used here is
DERIVED as "<subdomain>-<n>" (e.g. 3.1.2-1) and flagged id_is_derived=True.

Main considerations start at the left margin; nested lists (a., b., 1., 2.) are indented, so
indentation (not just numbering) decides where a new consideration begins.

Not parsed yet: the maturity model tables in section 2.4 (levels 0-5 and the level 3/4/5 criteria).
The framework assesses maturity, so these must be modeled before assessments are built.
"""
from __future__ import annotations

import re

from backend.extraction.common import word_diff
from backend.extraction.pdf_parser import Page
from backend.normalization.schemas import Requirement

NOISE = re.compile(r"^(Version 1\.0|Page \d+ of \d+)$")
DOM = re.compile(r"^3\.(\d{1,2})\s+(\S.*)$")
SEC = re.compile(r"^(3\.\d{1,2}\.\d{1,2}(?:\.\d{1,2})?)\s+(\S.*)$")   # 3.1.2 or 3.2.1.1
HAS_WORD = re.compile(r"[A-Za-z0-9]")
CONS = re.compile(r"^(\d{1,2})\.\s+(\S.*)$")
APPENDIX_A = re.compile(r"^Appendix A\b", re.M)
MAIN_X0_MAX = 80.0   # main items sit at x0~72; nested lists at 89 and 106


def controls_region(pages: list[Page]) -> list[Page]:
    start = next(p.index for p in pages if p.index > 5 and re.search(r"^3 Control domains\s*$", p.text, re.M))
    end = next(p.index for p in pages if p.index > start and APPENDIX_A.search(p.text))
    return [p for p in pages if start <= p.index < end]


def page_lines(page: Page) -> list[tuple[float, str]]:
    # Lines without any letter or digit are bullet glyphs (private-use characters), not content.
    return [(x, t) for x, t in page.lines if not NOISE.match(t) and HAS_WORD.search(t)]


def extract_sama(pages: list[Page], framework: str, version: str, source_sha256: str):
    """Returns (requirements, ambiguous_lines, skipped_lines).

    skipped_lines are source lines deliberately not stored as requirement text (domain headings and
    introductions, and the Principle/Objective/Control considerations labels); they exist so the
    validator can prove that every source word is accounted for.
    """
    reqs: list[Requirement] = []
    ambiguous: list[str] = []
    skipped: list[str] = []
    domain = sec_id = sec_title = None
    principle: list[str] = []
    objective: list[str] = []
    mode = None
    expected = 1
    cur: dict | None = None

    def flush() -> None:
        nonlocal cur
        if cur:
            text = " ".join(cur["lines"]).strip()
            is_ref = cur["n"] == "ref"
            reqs.append(Requirement(
                framework=framework, version=version, control_id=f"{sec_id}-{cur['n']}",
                printed_id="(unnumbered)" if is_ref else str(cur["n"]), id_is_derived=True,
                kind="control_reference" if is_ref else "control_consideration",
                level=sec_id.count(".") + 1,
                parse_notes=["Source has no numbered considerations here; text is an unnumbered reference list"] if is_ref else [],
                parent_control_id=sec_id, domain=domain, subdomain_id=sec_id, subdomain_title=sec_title,
                principle=" ".join(principle), objective=" ".join(objective), original_text=text,
                source_document_sha256=source_sha256, page_index=cur["page"].index,
                page_label=cur["page"].label, extraction_confidence=0.9 if len(text) >= 20 else 0.5,
            ))
        cur = None

    for page in controls_region(pages):
        for x0, line in page_lines(page):
            if line == "3 Control domains":
                skipped.append(line); continue
            m = SEC.match(line)
            if m:
                flush(); sec_id, sec_title = m.group(1), m.group(2).strip()
                principle, objective, mode, expected = [], [], "head", 1
                continue
            m = DOM.match(line)
            if m:
                flush(); domain = m.group(2).strip(); sec_id = None; mode = None
                skipped.append(line); continue
            if sec_id is None:
                skipped.append(line)   # domain introduction paragraph
                continue
            low = line.lower()
            if low == "principle":
                mode = "principle"; skipped.append(line); continue
            if low == "objective":
                mode = "objective"; skipped.append(line); continue
            if low == "control considerations":
                flush(); mode = "cons"; skipped.append(line); continue
            if mode == "principle":
                principle.append(line)
            elif mode == "objective":
                objective.append(line)
            elif mode == "cons":
                m = CONS.match(line)
                if m and x0 < MAIN_X0_MAX:
                    if int(m.group(1)) != expected:
                        ambiguous.append(f"p{page.index} {sec_id}: expected {expected}, found '{line[:70]}'")
                    flush()
                    cur = {"n": expected, "page": page, "lines": [m.group(2)]}
                    expected += 1
                else:
                    if x0 < MAX_CONT_X0 and cur is not None and not m:
                        ambiguous.append(f"p{page.index} {sec_id}: unindented non-numbered line '{line[:70]}'")
                    if cur is None and expected == 1:
                        cur = {"n": "ref", "page": page, "lines": [line]}   # unnumbered content
                    elif cur:
                        cur["lines"].append(line)
                    else:
                        ambiguous.append(f"p{page.index} {sec_id}: orphan line '{line[:70]}'")
            else:
                skipped.append(line)   # text between heading and Principle label
    flush()
    return reqs, ambiguous, skipped


MAX_CONT_X0 = MAIN_X0_MAX


def toc_sections(pages: list[Page]) -> dict[str, str]:
    """Subdomain IDs and titles from the document's own table of contents (pages 3-4)."""
    lines = [t for p in pages if p.index in (3, 4) for _, t in page_lines(p)]
    out = {}
    for i, ln in enumerate(lines[:-1]):
        if re.fullmatch(r"3\.\d{1,2}\.\d{1,2}", ln):
            out[ln] = re.sub(r"\.{3,}.*", "", lines[i + 1]).strip()
    return out


def validate_sama(pages: list[Page], reqs: list[Requirement], ambiguous: list[str], skipped: list[str]) -> list[str]:
    issues: list[str] = []
    toc = toc_sections(pages)
    all_parsed: dict[str, str] = {}
    for r in reqs:
        all_parsed.setdefault(r.subdomain_id, r.subdomain_title)
    parsed = {k: v for k, v in all_parsed.items() if k.count(".") == 2}   # TOC lists 3-level sections only
    for k in all_parsed:
        if k.count(".") == 3 and k.rsplit(".", 1)[0] not in all_parsed:
            issues.append(f"{k}: parent section {k.rsplit('.', 1)[0]} not found")
    if set(toc) != set(parsed):
        issues.append(f"subdomains differ from TOC: only in TOC={sorted(set(toc) - set(parsed))} "
                      f"only parsed={sorted(set(parsed) - set(toc))}")
    for sid, title in toc.items():
        if sid in parsed and re.sub(r"\s+", " ", parsed[sid]) != re.sub(r"\s+", " ", title):
            issues.append(f"{sid}: title differs from TOC: '{parsed[sid]}' vs '{title}'")
    for sid in all_parsed:
        first = next(r for r in reqs if r.subdomain_id == sid)
        if not (first.principle and first.objective):
            issues.append(f"{sid}: missing principle or objective")
    if ambiguous:
        issues.append(f"{len(ambiguous)} ambiguous lines: {ambiguous[:10]}")

    lines = [t for p in controls_region(pages) for _, t in page_lines(p)]
    texts = [f"{r.printed_id if r.kind == 'control_consideration' else ''} {r.original_text}" for r in reqs] + skipped
    texts += list(dict.fromkeys(f"{r.principle} {r.objective} {r.subdomain_id} {r.subdomain_title}" for r in reqs))
    lost, extra = word_diff(lines, texts)
    if lost:
        issues.append(f"source words not accounted for: {dict(list(lost.items())[:25])}")
    if extra:
        issues.append(f"output words not in source: {dict(list(extra.items())[:25])}")
    return issues

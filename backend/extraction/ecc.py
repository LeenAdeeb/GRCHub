"""Deterministic parser for NCA ECC (ECC-2:2024) English PDF.

Layout observed in the real document:
- each control ID (e.g. 1-1-1) is on its own line, with the control text on the following lines;
- sub-controls are printed as 1.5.3.1 with text on the same line;
- the domain banner ("2 / Cybersecurity Defense") is at the top of the page but comes LAST in the
  PDF text order, so banners are handled per page, before the page's other lines;
- subdomain 1-7 is printed as "1.7" (dot) in the source; it is normalized and flagged.
Only the controls section is parsed (appendices contain similar-looking IDs). No LLM is used.
"""
from __future__ import annotations

import re
from collections import Counter

from backend.extraction.common import word_diff
from backend.extraction.pdf_parser import Page
from backend.normalization.schemas import Requirement

ARABIC = re.compile(r"[؀-ۿﭐ-﷿ﹰ-﻿]")
NOISE = {
    "Essential Cybersecurity Controls",
    "Document classification: Public",
    "TLP: White",
    "The Essential Cybersecurity Controls (ECC)",
    "Details of the Essential Cybersecurity Controls (ECC)",
}
DOMAINS = {
    "Cybersecurity Governance": "1",
    "Cybersecurity Defense": "2",
    "Cybersecurity Resilience": "3",
    "Third-Party and Cloud Computing Cybersecurity": "4",
}
ID2 = re.compile(r"^(\d)[-.](\d{1,2})$")
ID3 = re.compile(r"^(\d)-(\d{1,2})-(\d{1,2})$")
SUB = re.compile(r"^(\d)\.(\d{1,2})\.(\d{1,2})\.(\d{1,2})\s+(\S.*)$")
APPENDIX = re.compile(r"^\s*Appendix\s*\(?A\)?", re.M)


def controls_region(pages: list[Page]) -> list[Page]:
    start = next(p.index for p in pages if re.search(r"^\s*1-1-1\s*$", p.text, re.M))
    end = next((p.index for p in pages if p.index > start and APPENDIX.search(p.text)), pages[-1].index + 1)
    return [p for p in pages if start <= p.index < end]


def page_content(page: Page) -> tuple[str | None, list[str]]:
    """Return (domain banner on this page or None, content lines in text order without noise)."""
    lines = []
    for n, raw in enumerate(page.text.splitlines()):
        line = raw.replace("﻿", "").strip()
        if not line or ARABIC.search(line) or line in NOISE:
            continue
        if line.isdigit() and n < 14:  # printed page number in the header
            continue
        lines.append(line)
    banner = next((ln for ln in lines if ln in DOMAINS), None)
    if banner:
        k = lines.index(banner)
        digit = DOMAINS[banner]
        drop = {k} | {j for j in (k - 2, k - 1, k + 1, k + 2) if 0 <= j < len(lines) and lines[j] == digit}
        lines = [ln for j, ln in enumerate(lines) if j not in drop]
    return banner, lines


def extract_ecc(pages: list[Page], framework: str, version: str, source_sha256: str) -> list[Requirement]:
    reqs: list[Requirement] = []
    domain = sub_id = sub_title = objective = None
    sub_notes: list[str] = []
    title_lines: list[str] = []
    obj_lines: list[str] = []
    mode = None
    cur: dict | None = None
    last_control: str | None = None

    def flush() -> None:
        nonlocal cur
        if cur:
            text = " ".join(cur["lines"]).strip()
            reqs.append(Requirement(
                framework=framework, version=version, control_id=cur["id"], printed_id=cur["printed"],
                kind=cur["kind"], level=cur["id"].count("-") + 1, parent_control_id=cur.get("parent"),
                domain=domain, subdomain_id=sub_id, subdomain_title=sub_title, objective=objective,
                original_text=text, source_document_sha256=source_sha256,
                page_index=cur["page"].index, page_label=cur["page"].label,
                extraction_confidence=0.95 if len(text) >= 20 else 0.5,
                parse_notes=list(sub_notes),
            ))
        cur = None

    for page in controls_region(pages):
        banner, lines = page_content(page)
        if banner:
            flush(); domain = banner; sub_id = None; mode = None
        for line in lines:
            m = ID2.match(line)
            if m and domain:
                flush()
                sub_id = f"{m.group(1)}-{m.group(2)}"
                sub_notes = [f"Subdomain header printed as '{line}' in the source; normalized to '{sub_id}'"] if "." in line else []
                title_lines, obj_lines, mode = [], [], "title"
                continue
            if mode == "title":
                if line.startswith("Objective"):
                    sub_title = " ".join(title_lines)
                    rest = line[len("Objective"):].strip()
                    obj_lines = [rest] if rest else []
                    mode = "objective"
                else:
                    title_lines.append(line)
                continue
            if mode == "objective":
                if line == "Controls":
                    objective = " ".join(obj_lines)
                    mode = "controls"
                else:
                    obj_lines.append(line)
                continue
            if mode != "controls":
                continue
            if ID3.match(line):
                flush()
                last_control = line
                cur = {"id": line, "printed": line, "kind": "control", "page": page, "lines": []}
                continue
            m = SUB.match(line)
            if m and last_control:
                flush()
                printed = ".".join(m.group(i) for i in range(1, 5))
                cur = {"id": printed.replace(".", "-"), "printed": printed, "kind": "sub_control",
                       "parent": last_control, "page": page, "lines": [m.group(5)]}
                continue
            if cur:
                cur["lines"].append(line)
    flush()
    return reqs


def validate_ecc(pages: list[Page], reqs: list[Requirement]) -> list[str]:
    """Independent checks against the source text. Empty list means all checks passed."""
    issues: list[str] = []
    lines = [ln for p in controls_region(pages) for ln in page_content(p)[1]]

    source_ids = [ln for ln in lines if ID3.match(ln)]
    parsed_ids = [r.control_id for r in reqs if r.kind == "control"]
    if source_ids != parsed_ids:
        issues.append(f"control IDs differ from source scan: source={len(source_ids)} parsed={len(parsed_ids)}")
    if len(set(r.control_id for r in reqs)) != len(reqs):
        issues.append("duplicate IDs parsed")

    by_sub: dict[str, list[int]] = {}
    for cid in parsed_ids:
        d, s, c = cid.split("-")
        by_sub.setdefault(f"{d}-{s}", []).append(int(c))
    for sub, nums in by_sub.items():
        if nums != list(range(1, len(nums) + 1)):
            issues.append(f"subdomain {sub}: control numbers not contiguous from 1: {nums}")

    source_subs = [re.sub(r"[.]", "-", ln) for ln in lines if ID2.match(ln)]
    parsed_subs = list(dict.fromkeys(r.subdomain_id for r in reqs))
    if source_subs != parsed_subs:
        issues.append(f"subdomain IDs differ: source={source_subs} parsed={parsed_subs}")
    for sub in parsed_subs:
        if sub not in by_sub:
            issues.append(f"subdomain {sub} has no controls")

    for r in reqs:
        if not r.original_text.strip():
            issues.append(f"{r.control_id}: empty text")

    # Conservation check: every source word is in the output, and nothing extra appears.
    texts = [f"{r.printed_id} {r.original_text}" for r in reqs]
    texts += list(dict.fromkeys(f"{r.subdomain_id} {r.subdomain_title} {r.objective}" for r in reqs))
    n_sub = len(parsed_subs)
    labels = Counter({"objective": n_sub, "controls": n_sub})  # the 'Objective'/'Controls' row labels
    lost, extra = word_diff(lines, texts)
    if lost - labels or labels - lost:
        issues.append(f"source words not accounted for: {dict(list((lost - labels).items())[:25])}; "
                      f"label shortfall: {dict(labels - lost)}")
    if extra:
        issues.append(f"output words not in source: {dict(list(extra.items())[:25])}")
    return issues

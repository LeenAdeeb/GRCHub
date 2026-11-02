"""Tests run against the REAL official PDFs only; no synthetic regulatory text is used.

Register the documents first (see README). Without them these tests are skipped.
Expected values (counts, pages, and SHA-256 digests of the text) were read directly from the official PDFs.
"""
import hashlib
import re
from pathlib import Path

import pytest

from backend.extraction.ecc import extract_ecc, validate_ecc
from backend.extraction.pdf_parser import extract_pages
from backend.extraction.sama import extract_sama, validate_sama

def _h(text: str) -> str:
    """SHA-256 of whitespace-normalized text. The tests compare digests, so no regulatory text is stored in this repository."""
    return hashlib.sha256(re.sub(r"\s+", " ", text.strip()).encode()).hexdigest()


ECC = next(iter(Path("storage/originals/nca-ecc-2-2024-en").glob("*.pdf")), None)
SAMA = next(iter(Path("storage/originals/sama-csf-v1-en").glob("*.pdf")), None)


@pytest.fixture(scope="module")
def ecc():
    pages = extract_pages(ECC)
    digest = hashlib.sha256(ECC.read_bytes()).hexdigest()
    return pages, extract_ecc(pages, "NCA ECC", "ECC-2:2024", digest)


@pytest.fixture(scope="module")
def sama():
    pages = extract_pages(SAMA)
    digest = hashlib.sha256(SAMA.read_bytes()).hexdigest()
    return pages, *extract_sama(pages, "SAMA CSF", "1.0", digest)


@pytest.mark.skipif(ECC is None, reason="real ECC PDF not registered")
def test_ecc_validation_passes(ecc):
    pages, reqs = ecc
    assert validate_ecc(pages, reqs) == []


@pytest.mark.skipif(ECC is None, reason="real ECC PDF not registered")
def test_ecc_traceability_and_review_status(ecc):
    _, reqs = ecc
    for r in reqs:
        assert r.page_index >= 1 and r.original_text
        assert r.validation_status.value == "pending_review"


@pytest.mark.skipif(ECC is None, reason="real ECC PDF not registered")
def test_ecc_known_control(ecc):
    _, reqs = ecc
    r = next(r for r in reqs if r.control_id == "1-1-1")
    assert _h(r.original_text) == "7eb0f663f68b8b087f82b9b49b38f0653b64a0b3080a1879b2a3a89274a70150"
    assert (r.page_index, r.page_label) == (16, "13")
    assert r.domain == "Cybersecurity Governance"


@pytest.mark.skipif(ECC is None, reason="real ECC PDF not registered")
def test_ecc_source_typo_is_flagged(ecc):
    _, reqs = ecc
    r = next(r for r in reqs if r.control_id == "1-7-1")
    assert r.subdomain_id == "1-7" and r.parse_notes


@pytest.mark.skipif(SAMA is None, reason="real SAMA PDF not registered")
def test_sama_validation_passes(sama):
    pages, reqs, ambiguous, skipped = sama
    assert validate_sama(pages, reqs, ambiguous, skipped) == []


@pytest.mark.skipif(SAMA is None, reason="real SAMA PDF not registered")
def test_sama_known_section(sama):
    _, reqs, _, _ = sama
    sec = [r for r in reqs if r.subdomain_id == "3.1.2"]
    assert len(sec) == 3
    assert _h(sec[0].original_text) == "78b6ca04eb9b701dad79fa613734f7513d96854d7e11b5ed1c9780ade19fa26e"
    assert all(r.id_is_derived for r in sec)


# Golden counts: totals confirmed by validation and spot checks against the official PDFs.
# If a parser change alters these, re-check the extraction against the PDF before updating them.
@pytest.mark.skipif(ECC is None, reason="real ECC PDF not registered")
def test_ecc_golden_counts(ecc):
    from collections import Counter
    _, reqs = ecc
    assert Counter(r.kind for r in reqs) == {"control": 108, "sub_control": 91}


@pytest.mark.skipif(SAMA is None, reason="real SAMA PDF not registered")
def test_sama_golden_counts(sama):
    from collections import Counter
    _, reqs, _, _ = sama
    assert Counter(r.kind for r in reqs) == {"control_consideration": 147, "control_reference": 1}


@pytest.fixture(scope="module")
def maturity():
    from backend.extraction.sama_maturity import extract_maturity
    pages = extract_pages(SAMA)
    return pages, *extract_maturity(pages, hashlib.sha256(SAMA.read_bytes()).hexdigest())


@pytest.mark.skipif(SAMA is None, reason="real SAMA PDF not registered")
def test_sama_maturity_validation_passes(maturity):
    from backend.extraction.sama_maturity import validate_maturity
    pages, levels, sections, skipped = maturity
    assert validate_maturity(pages, levels, sections, skipped) == []


@pytest.mark.skipif(SAMA is None, reason="real SAMA PDF not registered")
def test_sama_maturity_levels_match_the_pdf_table(maturity):
    _, levels, sections, _ = maturity
    assert [(l.level, l.name) for l in levels] == [
        (0, "Non-existent"), (1, "Ad-hoc"), (2, "Repeatable but informal"),
        (3, "Structured and formalized"), (4, "Managed and measurable"), (5, "Adaptive")]
    # (criteria bullets, explanation bullets) per level, counted on Table 1 of the PDF
    assert [(len(l.criteria), len(l.explanation)) for l in levels] == [(2, 1), (3, 2), (1, 2), (2, 3), (2, 3), (1, 3)]
    assert [s.section_id for s in sections] == ["2.4", "2.4.1", "2.4.2", "2.4.3"]


@pytest.mark.skipif(SAMA is None, reason="real SAMA PDF not registered")
def test_sama_maturity_stray_bullet_is_merged_and_flagged(maturity):
    _, levels, _, _ = maturity
    assert len(levels[2].criteria) == 1 and _h(levels[2].criteria[0]) == "29e4512164645ee5c7a2426e2b017e9b133a2fcf5a12a28fc028a3b2a8fa4966"
    assert levels[2].parse_notes

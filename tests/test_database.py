"""Database and review tests using the REAL extracted requirements (storage/extracted/*.jsonl).

Needs a scratch PostgreSQL database: set GRCHUB_TEST_DATABASE_URL (its name must contain 'test',
because the schema is dropped and recreated). Skipped otherwise.
"""
import hashlib
import os
from pathlib import Path

import psycopg
import pytest

from backend import review
from backend.acquisition.fetcher import load_sources
from backend.database.loader import init_schema, load_source

URL = os.environ.get("GRCHUB_TEST_DATABASE_URL")
STORAGE = Path("storage")
ECC_JSONL = STORAGE / "extracted" / "nca-ecc-2-2024-en.jsonl"

pytestmark = pytest.mark.skipif(
    not URL or "test" not in URL or not ECC_JSONL.exists(),
    reason="needs GRCHUB_TEST_DATABASE_URL (database name containing 'test') and extracted ECC data",
)


@pytest.fixture()
def conn():
    with psycopg.connect(URL) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        init_schema(c)
        load_source(c, load_sources()["nca-ecc-2-2024-en"], ECC_JSONL, STORAGE)
        yield c


def count(c, sql="SELECT count(*) FROM requirements", *p):
    return c.execute(sql, p).fetchone()[0]


def test_loaded_count_matches_extraction(conn):
    expected = len([l for l in ECC_JSONL.read_text(encoding="utf-8").splitlines() if l.strip()])
    assert count(conn) == expected


def test_reload_is_idempotent_and_keeps_approvals(conn):
    rid = review.list_requirements(conn, limit=1)[0]["id"]
    review.decide(conn, rid, "approved", "tester")
    result = load_source(conn, load_sources()["nca-ecc-2-2024-en"], ECC_JSONL, STORAGE)
    assert result["inserted"] == 0 and result["reset_to_review"] == 0
    assert review.get(conn, rid)["validation_status"] == "approved"


def test_text_drift_sends_requirement_back_to_review(conn):
    rid = review.list_requirements(conn, limit=1)[0]["id"]
    review.decide(conn, rid, "approved", "tester")
    conn.execute("UPDATE requirements SET original_text = original_text || ' (drift)' WHERE id=%s", (rid,))
    result = load_source(conn, load_sources()["nca-ecc-2-2024-en"], ECC_JSONL, STORAGE)
    assert result["reset_to_review"] == 1
    assert review.get(conn, rid)["validation_status"] == "pending_review"


def test_review_decision_is_pinned_to_text_hash_and_audited(conn):
    rid = review.list_requirements(conn, limit=1)[0]["id"]
    review.decide(conn, rid, "approved", "tester", "checked against PDF")
    text = review.get(conn, rid)["original_text"]
    digest = conn.execute("SELECT text_sha256 FROM requirement_reviews WHERE requirement_id=%s", (rid,)).fetchone()[0]
    assert digest == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert count(conn, "SELECT count(*) FROM audit_logs WHERE action='review_approved'") == 1


def test_rejection_requires_note_and_reviewer(conn):
    rid = review.list_requirements(conn, limit=1)[0]["id"]
    with pytest.raises(ValueError):
        review.decide(conn, rid, "rejected", "tester")
    with pytest.raises(ValueError):
        review.decide(conn, rid, "approved", "  ")
    assert review.get(conn, rid)["validation_status"] == "pending_review"

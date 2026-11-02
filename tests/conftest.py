"""Shared fixtures. Regulatory text in these tests is always the REAL extracted text. The organizations, users,
passwords, evidence files and the second framework version below are TEST FIXTURES that exist only inside the
automated tests; nothing here is ever loaded into the real application.

Needs GRCHUB_TEST_DATABASE_URL (database name must contain 'test'; its schema is dropped and recreated)."""
import json
import os
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from backend import review, security, users
from backend.acquisition.fetcher import load_sources
from backend.api.main import app
from backend.database.loader import init_schema, load_source

URL = os.environ.get("GRCHUB_TEST_DATABASE_URL")
STORAGE = Path("storage")
ECC_JSONL = STORAGE / "extracted" / "nca-ecc-2-2024-en.jsonl"
SAMA_JSONL = STORAGE / "extracted" / "sama-csf-v1-en.jsonl"
PASSWORD = "fixture-password-123"   # test data only

needs_db = pytest.mark.skipif(
    not URL or "test" not in URL or not ECC_JSONL.exists(),
    reason="needs GRCHUB_TEST_DATABASE_URL (name containing 'test') and extracted ECC data",
)


class World:
    def __init__(self, conn, client):
        self.conn, self.client = conn, client
        self.org, self.user, self._tokens = {}, {}, {}

    def add_org(self, key, name):
        self.org[key] = users.create_organization(self.conn, name, "fixture")

    def add_user(self, key, role, org=None):
        email = f"{key}@example.test"
        self.user[key] = users.create_user(self.conn, email, PASSWORD, role, self.org[org] if org else None, "fixture")

    def h(self, key):
        """Authorization headers for a fixture user (logs in through the real endpoint)."""
        if key not in self._tokens:
            r = self.client.post("/auth/login", json={"email": f"{key}@example.test", "password": PASSWORD})
            assert r.status_code == 200, r.text
            self._tokens[key] = {"Authorization": f"Bearer {r.json()['access_token']}"}
        return self._tokens[key]

    def req(self, control_id, framework="NCA ECC"):
        return self.conn.execute(
            "SELECT r.id FROM requirements r JOIN framework_versions v ON v.id=r.framework_version_id "
            "JOIN frameworks f ON f.id=v.framework_id WHERE f.name=%s AND r.control_id=%s", (framework, control_id)).fetchone()[0]

    def approve(self, *control_ids, framework="NCA ECC"):
        ids = [self.req(c, framework) for c in control_ids]
        for i in ids:
            review.decide(self.conn, i, "approved", "fixture-reviewer")
        return ids if len(ids) > 1 else ids[0]


@pytest.fixture()
def world(monkeypatch, tmp_path):
    if not (URL and "test" in URL and ECC_JSONL.exists()):
        pytest.skip("needs a test database and extracted ECC data")
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("GRCHUB_JWT_SECRET", "t" * 40)
    monkeypatch.setenv("GRCHUB_EVIDENCE_DIR", str(tmp_path / "evidence"))
    security._failures.clear()
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        init_schema(conn)
        sources = load_sources()
        load_source(conn, sources["nca-ecc-2-2024-en"], ECC_JSONL, STORAGE)
        if SAMA_JSONL.exists():
            load_source(conn, sources["sama-csf-v1-en"], SAMA_JSONL, STORAGE)
        w = World(conn, TestClient(app))
        w.add_org("a", "Test Org A")
        w.add_org("b", "Test Org B")
        w.add_user("padmin", "platform_admin")
        w.add_user("rev1", "reviewer")
        w.add_user("rev2", "reviewer")
        w.add_user("a_admin", "org_admin", "a")
        w.add_user("a_analyst", "analyst", "a")
        w.add_user("a_assessor", "assessor", "a")
        w.add_user("a_assessor2", "assessor", "a")
        w.add_user("a_viewer", "viewer", "a")
        w.add_user("b_admin", "org_admin", "b")
        w.add_user("b_analyst", "analyst", "b")
        w.add_user("b_assessor", "assessor", "b")
        yield w

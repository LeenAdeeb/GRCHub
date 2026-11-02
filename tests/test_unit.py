"""Data-free tests: no regulatory documents, no database. These run anywhere (including CI)."""
import hashlib

import pytest
from fastapi.testclient import TestClient

from backend import evidence, review, security
from backend.acquisition.fetcher import load_sources, register_local, sha256_of
from backend.api.main import app


def test_sha256_matches_hashlib():
    assert sha256_of(b"abc") == hashlib.sha256(b"abc").hexdigest()


def test_source_registry_is_consistent():
    for s in load_sources().values():
        if s["status"] == "verified":
            assert s["download_url"], f"{s['id']} is verified but has no download_url"
        if s["parser"]:
            assert s["identity_check"], f"{s['id']} has a parser but no identity_check"
    assert not any("pdpl" in s["id"].lower() for s in load_sources().values())


def test_registration_rejects_wrong_hash(tmp_path):
    f = tmp_path / "x.pdf"
    f.write_bytes(b"not a regulatory document")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        register_local("nca-ecc-2-2024-en", f, tmp_path, expect_sha256="0" * 64)


def test_review_rules_are_enforced_before_touching_the_database():
    with pytest.raises(ValueError):
        review.decide(None, 1, "maybe", "someone")
    with pytest.raises(ValueError):
        review.decide(None, 1, "approved", "   ")
    with pytest.raises(ValueError):
        review.decide(None, 1, "rejected", "someone")          # rejection needs a note


# ---- passwords and tokens
def test_password_hash_roundtrip_and_salting():
    h1, h2 = security.hash_password("a-long-enough-password"), security.hash_password("a-long-enough-password")
    assert h1 != h2 and "a-long-enough-password" not in h1
    assert security.verify_password("a-long-enough-password", h1)
    assert not security.verify_password("wrong-password-here", h1)
    assert not security.verify_password("x", "garbage")


def test_short_passwords_are_refused():
    with pytest.raises(ValueError):
        security.validate_password("short")


def test_token_needs_a_strong_secret(monkeypatch):
    monkeypatch.setenv("GRCHUB_JWT_SECRET", "too-short")
    with pytest.raises(RuntimeError):
        security.create_token(1, "viewer", 1)


def test_token_roundtrip_and_tampering(monkeypatch):
    monkeypatch.setenv("GRCHUB_JWT_SECRET", "s" * 40)
    token = security.create_token(7, "assessor", 3)
    assert security.decode_token(token)["sub"] == "7"
    monkeypatch.setenv("GRCHUB_JWT_SECRET", "z" * 40)       # a different secret must not validate it
    with pytest.raises(Exception):
        security.decode_token(token)


def test_login_lockout_after_repeated_failures():
    security._failures.clear()
    for _ in range(security.MAX_FAILED_LOGINS):
        security.record_failure("someone@example.test")
    assert security.is_locked_out("SOMEONE@example.test")        # case-insensitive
    security.clear_failures("someone@example.test")
    assert not security.is_locked_out("someone@example.test")


# ---- evidence upload validation
def test_upload_validation():
    assert evidence.validate_upload("policy.pdf", b"%PDF-1.7 ...") == ".pdf"
    assert evidence.validate_upload("notes.txt", "plain text".encode()) == ".txt"
    for bad in [("run.exe", b"MZ"), ("noext", b"abc"), ("a.pdf", b"not a pdf"), ("a.txt", b"bin\x00ary"),
                ("a.pdf", b""), ("a.csv", b"\xff\xfe\xfa")]:
        with pytest.raises(ValueError):
            evidence.validate_upload(*bad)
    with pytest.raises(ValueError):
        evidence.validate_upload("big.txt", b"a" * (evidence.MAX_BYTES + 1))


def test_evidence_url_must_be_http():
    for bad in ("javascript:alert(1)", "file:///etc/passwd", "ftp://x"):
        with pytest.raises(ValueError):
            evidence._check_url(bad)
    assert evidence._check_url(" https://example.test/doc ") == "https://example.test/doc"


# ---- API refuses anonymous access before any database work
def test_api_health_is_public():
    assert TestClient(app).get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize("method,path", [
    ("get", "/requirements"), ("get", "/stats"), ("get", "/dashboard"), ("get", "/evidence"),
    ("get", "/mappings"), ("get", "/maturity"), ("post", "/assessments"), ("get", "/users"), ("get", "/auth/me"),
])
def test_anonymous_requests_are_rejected(method, path):
    assert getattr(TestClient(app), method)(path).status_code == 401


def test_bad_and_malformed_tokens_are_rejected(monkeypatch):
    monkeypatch.setenv("GRCHUB_JWT_SECRET", "s" * 40)
    client = TestClient(app)
    assert client.get("/requirements", headers={"Authorization": "Bearer not-a-token"}).status_code == 401
    assert client.get("/requirements", headers={"Authorization": "Basic abc"}).status_code == 401
    assert client.get("/requirements", headers={"Authorization": "Bearer"}).status_code == 401


def test_no_jwt_secret_means_service_unavailable_not_open(monkeypatch):
    monkeypatch.delenv("GRCHUB_JWT_SECRET", raising=False)
    assert TestClient(app).get("/requirements", headers={"Authorization": "Bearer x.y.z"}).status_code == 503

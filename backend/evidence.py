"""Evidence management: upload/link, status workflow, expiry. Every query is scoped to one organization,
and a record from another organization is indistinguishable from a missing one (KeyError -> 404).

Workflow:  pending -> submitted -> under_review -> accepted | rejected;  accepted -> expired (by date or by hand);
           rejected / expired / pending -> (new attachment) -> submitted.
The person who submitted evidence can never be the one who accepts or rejects it.
'Accepted' means the evidence was judged adequate; it does NOT make a control compliant. That is an assessment.
"""
from __future__ import annotations

import hashlib
import os
from datetime import date
from pathlib import Path

from psycopg.rows import dict_row

from backend.audit import audit

MAX_BYTES = 25 * 1024 * 1024
MAGIC = {
    ".pdf": b"%PDF", ".png": b"\x89PNG\r\n\x1a\n", ".jpg": b"\xff\xd8\xff", ".jpeg": b"\xff\xd8\xff",
    ".docx": b"PK\x03\x04", ".xlsx": b"PK\x03\x04", ".pptx": b"PK\x03\x04", ".txt": None, ".csv": None,
}
EFFECTIVE = ("CASE WHEN e.status = 'accepted' AND e.review_due_date IS NOT NULL AND e.review_due_date < CURRENT_DATE "
             "THEN 'expired' ELSE e.status END")
COLUMNS = ("e.id, e.org_id, e.name, e.description, e.original_filename, e.content_type, e.size_bytes, e.sha256, "
           "e.external_url, e.owner_user_id, e.created_by, e.created_at, e.submitted_by, e.submitted_at, "
           "e.review_due_date, e.status AS stored_status, " + EFFECTIVE + " AS status, e.reviewer_comments")

TRANSITIONS = {  # (from effective status) -> allowed targets
    "submitted": {"under_review"},
    "under_review": {"accepted", "rejected"},
    "accepted": {"expired"},
}


def evidence_dir() -> Path:
    return Path(os.environ.get("GRCHUB_EVIDENCE_DIR", "storage/evidence"))


def validate_upload(filename: str, data: bytes) -> str:
    """Checks extension allow-list, size, and that the content matches the extension. Returns the extension.
    This is NOT malware scanning; run a scanner (for example ClamAV) before using this with real evidence."""
    ext = Path(filename or "").suffix.lower()
    if ext not in MAGIC:
        raise ValueError(f"file type '{ext or 'none'}' is not allowed; allowed: {', '.join(sorted(MAGIC))}")
    if not data:
        raise ValueError("the file is empty")
    if len(data) > MAX_BYTES:
        raise ValueError(f"the file is larger than {MAX_BYTES // (1024 * 1024)} MB")
    magic = MAGIC[ext]
    if magic and not data.startswith(magic):
        raise ValueError(f"the file content does not look like a {ext} file")
    if magic is None:
        if b"\x00" in data:
            raise ValueError("text files must not contain binary data")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("text files must be UTF-8")
    return ext


def _check_url(url: str) -> str:
    url = url.strip()
    if not url.lower().startswith(("https://", "http://")) or len(url) > 2000:
        raise ValueError("external_url must be an http(s) URL of at most 2000 characters")
    return url


def _store(org_id: int, data: bytes) -> tuple[str, str]:
    digest = hashlib.sha256(data).hexdigest()
    target = evidence_dir() / str(org_id) / digest        # stored under its hash; the user's file name is never a path
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(data)
    return digest, str(target)


def _check_requirements(conn, requirement_ids: list[int]) -> list[int]:
    ids = sorted(set(requirement_ids))
    if ids:
        ok = conn.execute("SELECT count(*) FROM requirements WHERE id = ANY(%s) AND validation_status = 'approved'",
                          (ids,)).fetchone()[0]
        if ok != len(ids):
            raise ValueError("every linked requirement must exist and be approved by a reviewer")
    return ids


def _fetch(conn, org_id: int, evidence_id: int, lock: bool = False) -> dict:
    row = conn.cursor(row_factory=dict_row).execute(
        f"SELECT {COLUMNS}, e.storage_path FROM evidence e WHERE e.id=%s AND e.org_id=%s" + (" FOR UPDATE OF e" if lock else ""),
        (evidence_id, org_id)).fetchone()
    if row is None:
        raise KeyError("evidence not found")
    return row


def create(conn, org_id: int, user_id: int, actor: str, name: str, description: str | None, due: date | None,
           owner_user_id: int | None, requirement_ids: list[int], upload: tuple[str, bytes] | None,
           external_url: str | None) -> int:
    if not name.strip():
        raise ValueError("name is required")
    if upload and external_url:
        raise ValueError("provide either a file or a URL, not both")
    ext = validate_upload(*upload) if upload else None
    url = _check_url(external_url) if external_url else None
    with conn.transaction():
        if owner_user_id is not None and conn.execute(
                "SELECT 1 FROM users WHERE id=%s AND org_id=%s AND is_active", (owner_user_id, org_id)).fetchone() is None:
            raise ValueError("owner must be an active user of your organization")
        ids = _check_requirements(conn, requirement_ids)
        digest = path = None
        if upload:
            digest, path = _store(org_id, upload[1])
        attached = bool(upload or url)
        evidence_id = conn.execute(
            "INSERT INTO evidence (org_id, name, description, storage_path, original_filename, content_type, size_bytes, "
            "sha256, external_url, owner_user_id, created_by, submitted_by, submitted_at, review_due_date, status) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (org_id, name.strip(), description, path, os.path.basename(upload[0]) if upload else None, ext,
             len(upload[1]) if upload else None, digest, url, owner_user_id, user_id, user_id if attached else None,
             date.today() if attached else None, due, "submitted" if attached else "pending")).fetchone()[0]
        for rid in ids:
            conn.execute("INSERT INTO evidence_requirements (evidence_id, requirement_id) VALUES (%s,%s)", (evidence_id, rid))
        audit(conn, actor, "evidence_created", "evidence", evidence_id,
              {"status": "submitted" if attached else "pending", "sha256": digest, "requirements": ids}, org_id=org_id)
    return evidence_id


def attach(conn, org_id: int, user_id: int, actor: str, evidence_id: int, upload: tuple[str, bytes] | None,
           external_url: str | None) -> None:
    if bool(upload) == bool(external_url):
        raise ValueError("provide exactly one of: a file, a URL")
    ext = validate_upload(*upload) if upload else None
    url = _check_url(external_url) if external_url else None
    with conn.transaction():
        row = _fetch(conn, org_id, evidence_id, lock=True)
        if row["status"] not in ("pending", "rejected", "expired"):
            raise ValueError(f"evidence in status '{row['status']}' cannot be replaced; it must be rejected or expired first")
        digest = path = None
        if upload:
            digest, path = _store(org_id, upload[1])
        conn.execute(
            "UPDATE evidence SET storage_path=%s, original_filename=%s, content_type=%s, size_bytes=%s, sha256=%s, "
            "external_url=%s, status='submitted', submitted_by=%s, submitted_at=CURRENT_DATE, reviewer_comments=NULL "
            "WHERE id=%s",
            (path, os.path.basename(upload[0]) if upload else None, ext, len(upload[1]) if upload else None, digest,
             url, user_id, evidence_id))
        audit(conn, actor, "evidence_attached", "evidence", evidence_id, {"from": row["status"], "sha256": digest}, org_id=org_id)


def set_requirements(conn, org_id: int, actor: str, evidence_id: int, requirement_ids: list[int]) -> list[int]:
    with conn.transaction():
        _fetch(conn, org_id, evidence_id, lock=True)
        ids = _check_requirements(conn, requirement_ids)
        conn.execute("DELETE FROM evidence_requirements WHERE evidence_id=%s", (evidence_id,))
        for rid in ids:
            conn.execute("INSERT INTO evidence_requirements (evidence_id, requirement_id) VALUES (%s,%s)", (evidence_id, rid))
        audit(conn, actor, "evidence_requirements_set", "evidence", evidence_id, {"requirements": ids}, org_id=org_id)
    return ids


def transition(conn, org_id: int, user_id: int, actor: str, evidence_id: int, to: str, comment: str | None) -> str:
    with conn.transaction():
        row = _fetch(conn, org_id, evidence_id, lock=True)
        current = row["status"]
        if to not in TRANSITIONS.get(current, set()):
            raise ValueError(f"cannot move evidence from '{current}' to '{to}'")
        if to in ("accepted", "rejected"):
            if row["submitted_by"] == user_id:
                raise PermissionError("the person who submitted this evidence cannot accept or reject it")
            if to == "rejected" and not (comment and comment.strip()):
                raise ValueError("a comment is required when rejecting evidence")
        if to == "accepted" and row["review_due_date"] is not None and row["review_due_date"] < date.today():
            raise ValueError("the review due date has already passed; set a new due date before accepting")
        conn.execute("UPDATE evidence SET status=%s, reviewer_comments=COALESCE(%s, reviewer_comments) WHERE id=%s",
                     (to, comment, evidence_id))
        audit(conn, actor, f"evidence_{to}", "evidence", evidence_id, {"from": current, "comment": comment}, org_id=org_id)
    return to


def get(conn, org_id: int, evidence_id: int) -> dict:
    row = _fetch(conn, org_id, evidence_id)
    row.pop("storage_path", None)
    row["requirements"] = conn.cursor(row_factory=dict_row).execute(
        "SELECT r.id, r.control_id, r.framework_version_id FROM evidence_requirements er "
        "JOIN requirements r ON r.id = er.requirement_id WHERE er.evidence_id=%s ORDER BY r.id", (evidence_id,)).fetchall()
    return row


def list_evidence(conn, org_id: int, status: str | None = None, requirement_id: int | None = None,
                  limit: int = 50, offset: int = 0) -> dict:
    where, params = ["e.org_id = %s"], [org_id]
    if requirement_id is not None:
        where.append("EXISTS (SELECT 1 FROM evidence_requirements er WHERE er.evidence_id = e.id AND er.requirement_id = %s)")
        params.append(requirement_id)
    having = f" AND {EFFECTIVE} = %s" if status else ""
    if status:
        params.append(status)
    clause = " WHERE " + " AND ".join(where) + having
    cur = conn.cursor(row_factory=dict_row)
    items = cur.execute(f"SELECT {COLUMNS} FROM evidence e{clause} ORDER BY e.id DESC LIMIT %s OFFSET %s",
                        (*params, limit, offset)).fetchall()
    total = conn.execute(f"SELECT count(*) FROM evidence e{clause}", params).fetchone()[0]
    return {"total": total, "limit": limit, "offset": offset, "items": items}


def file_for_download(conn, org_id: int, evidence_id: int) -> tuple[Path, str]:
    row = _fetch(conn, org_id, evidence_id)
    if not row["storage_path"]:
        raise KeyError("this evidence has no uploaded file")
    return Path(row["storage_path"]), os.path.basename(row["original_filename"] or "evidence")


def expire_due(conn, actor: str = "system") -> int:
    """Persist 'expired' for accepted evidence whose review due date has passed."""
    with conn.transaction():
        rows = conn.execute(
            "UPDATE evidence SET status='expired' WHERE status='accepted' AND review_due_date < CURRENT_DATE "
            "RETURNING id, org_id").fetchall()
        for evidence_id, org_id in rows:
            audit(conn, actor, "evidence_expired", "evidence", evidence_id, {"reason": "review due date passed"}, org_id=org_id)
    return len(rows)

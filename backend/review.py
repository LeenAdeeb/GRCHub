"""Human review of extracted requirements. Nothing is approved automatically or in bulk by default:
a reviewer compares each requirement with the cited page of the official PDF."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

BASE = ("SELECT r.id, f.name AS framework, v.version_label AS version, r.control_id, r.printed_id, r.kind, "
        "r.id_is_derived, r.domain, r.subdomain_id, r.subdomain_title, r.principle, r.objective, r.original_text, r.page_index, r.page_label, "
        "r.extraction_confidence, r.parse_notes, r.validation_status, r.reviewed_by, r.reviewed_at "
        "FROM requirements r JOIN framework_versions v ON v.id = r.framework_version_id "
        "JOIN frameworks f ON f.id = v.framework_id")
FROM = ("FROM requirements r JOIN framework_versions v ON v.id = r.framework_version_id "
        "JOIN frameworks f ON f.id = v.framework_id")


def stats(conn: psycopg.Connection) -> list[dict]:
    return conn.cursor(row_factory=dict_row).execute(
        "SELECT f.name AS framework, v.version_label AS version, r.validation_status AS status, count(*) AS n "
        + FROM + " GROUP BY 1,2,3 ORDER BY 1,2,3").fetchall()


def _where(status, framework, subdomain, only_flagged, text) -> tuple[str, list]:
    where, params = [], []
    if status:
        where.append("r.validation_status = %s"); params.append(status)
    if framework:
        where.append("f.name = %s"); params.append(framework)
    if subdomain:
        where.append("r.subdomain_id = %s"); params.append(subdomain)
    if only_flagged:
        where.append("(r.extraction_confidence < 0.85 OR jsonb_array_length(r.parse_notes) > 0)")
    if text:
        escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append("r.original_text ILIKE %s"); params.append(f"%{escaped}%")
    return (" WHERE " + " AND ".join(where)) if where else "", params


def list_requirements(conn, status: str | None = "pending_review", framework: str | None = None,
                      subdomain: str | None = None, only_flagged: bool = False, limit: int = 50,
                      text: str | None = None, offset: int = 0) -> list[dict]:
    clause, params = _where(status, framework, subdomain, only_flagged, text)
    return conn.cursor(row_factory=dict_row).execute(
        BASE + clause + " ORDER BY r.id LIMIT %s OFFSET %s", (*params, limit, offset)).fetchall()


def count_requirements(conn, status=None, framework=None, subdomain=None, only_flagged=False, text=None) -> int:
    clause, params = _where(status, framework, subdomain, only_flagged, text)
    return conn.execute("SELECT count(*) " + FROM + clause, params).fetchone()[0]


def get(conn, req_id: int) -> dict:
    row = conn.cursor(row_factory=dict_row).execute(BASE + " WHERE r.id = %s", (req_id,)).fetchone()
    if row is None:
        raise KeyError(f"requirement {req_id} not found")
    return row


def history(conn, req_id: int) -> list[dict]:
    return conn.cursor(row_factory=dict_row).execute(
        "SELECT reviewer, decision, note, text_sha256, decided_at FROM requirement_reviews "
        "WHERE requirement_id = %s ORDER BY id", (req_id,)).fetchall()


def decide(conn, req_id: int, decision: str, reviewer: str, note: str | None = None) -> None:
    if decision not in ("approved", "rejected"):
        raise ValueError("decision must be 'approved' or 'rejected'")
    if not reviewer or not reviewer.strip():
        raise ValueError("reviewer name is required")
    if decision == "rejected" and not (note and note.strip()):
        raise ValueError("a note is required when rejecting")
    with conn.transaction():
        row = conn.execute("SELECT original_text FROM requirements WHERE id=%s FOR UPDATE", (req_id,)).fetchone()
        if row is None:
            raise KeyError(f"requirement {req_id} not found")
        digest = hashlib.sha256(row[0].encode("utf-8")).hexdigest()
        conn.execute("INSERT INTO requirement_reviews (requirement_id, reviewer, decision, note, text_sha256) "
                     "VALUES (%s,%s,%s,%s,%s)", (req_id, reviewer, decision, note, digest))
        conn.execute("UPDATE requirements SET validation_status=%s, reviewed_by=%s, reviewed_at=now() WHERE id=%s",
                     (decision, reviewer, req_id))
        conn.execute("INSERT INTO audit_logs (actor, action, entity, entity_id, detail) VALUES (%s,%s,%s,%s,%s)",
                     (reviewer, f"review_{decision}", "requirement", str(req_id),
                      Jsonb({"note": note, "text_sha256": digest})))


def export_csv(conn, out: Path, framework: str | None = None) -> int:
    rows = list_requirements(conn, status=None, framework=framework, limit=100000)
    out.parent.mkdir(parents=True, exist_ok=True)
    fields = ["id", "framework", "version", "control_id", "printed_id", "kind", "domain", "subdomain_id",
              "subdomain_title", "page_index", "page_label", "extraction_confidence", "validation_status",
              "reviewed_by", "original_text"]
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return len(rows)


# ---- maturity levels and narrative sections follow the same review rules as requirements
ITEM_TABLES = {
    "maturity_levels": "name, criteria, explanation",
    "framework_sections": "title, original_text",
}


def decide_item(conn, table: str, item_id: int, decision: str, reviewer: str, note: str | None = None) -> None:
    if table not in ITEM_TABLES:
        raise ValueError("unknown review table")
    if decision not in ("approved", "rejected"):
        raise ValueError("decision must be 'approved' or 'rejected'")
    if not reviewer or not reviewer.strip():
        raise ValueError("reviewer name is required")
    if decision == "rejected" and not (note and note.strip()):
        raise ValueError("a note is required when rejecting")
    with conn.transaction():
        row = conn.execute(f"SELECT {ITEM_TABLES[table]} FROM {table} WHERE id=%s FOR UPDATE", (item_id,)).fetchone()
        if row is None:
            raise KeyError(f"{table} {item_id} not found")
        digest = hashlib.sha256(json.dumps(row, sort_keys=True).encode("utf-8")).hexdigest()
        conn.execute(f"UPDATE {table} SET validation_status=%s, reviewed_by=%s, reviewed_at=now() WHERE id=%s",
                     (decision, reviewer, item_id))
        conn.execute("INSERT INTO audit_logs (actor, action, entity, entity_id, detail) VALUES (%s,%s,%s,%s,%s)",
                     (reviewer, f"review_{decision}", table, str(item_id), Jsonb({"note": note, "content_sha256": digest})))

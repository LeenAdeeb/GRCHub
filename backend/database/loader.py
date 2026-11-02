"""Load extracted requirements into PostgreSQL. Idempotent: re-loading never loses review decisions
unless the original text itself changed, in which case the requirement goes back to review."""
from __future__ import annotations

import json
import os
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

SCHEMA_FILE = Path(__file__).with_name("schema.sql")


def connect(url: str | None = None) -> psycopg.Connection:
    url = url or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("Set DATABASE_URL, e.g. postgresql://grchub:<password>@localhost:5432/grchub")
    return psycopg.connect(url)


def init_schema(conn: psycopg.Connection) -> None:
    """Create or upgrade the schema (idempotent)."""
    from backend.database.migrate import migrate
    migrate(conn)
    conn.commit()


def _audit(conn, actor: str, action: str, entity: str, entity_id, detail: dict) -> None:
    conn.execute(
        "INSERT INTO audit_logs (actor, action, entity, entity_id, detail) VALUES (%s,%s,%s,%s,%s)",
        (actor, action, entity, str(entity_id), Jsonb(detail)),
    )


def _history_record(storage_dir: Path, source_id: str, sha: str) -> dict:
    path = storage_dir / "originals" / source_id / "history.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return next(r for r in reversed(records) if r["sha256"] == sha)


def _load_maturity(conn, doc_id: int, fv_id: int, path: Path, actor: str, stats: dict) -> None:
    """Upsert maturity levels and narrative sections; changed text goes back to review."""
    data = json.loads(path.read_text(encoding="utf-8"))
    for lv in data["levels"]:
        row = conn.execute("SELECT id, name, criteria, explanation FROM maturity_levels "
                           "WHERE framework_version_id=%s AND level=%s", (fv_id, lv["level"])).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO maturity_levels (document_id, framework_version_id, level, name, criteria, explanation, "
                "page_index, page_label, extraction_confidence, parse_notes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (doc_id, fv_id, lv["level"], lv["name"], Jsonb(lv["criteria"]), Jsonb(lv["explanation"]),
                 lv["page_index"], lv["page_label"], lv["extraction_confidence"], Jsonb(lv["parse_notes"])))
            stats["maturity_inserted"] += 1
        elif (row[1], row[2], row[3]) != (lv["name"], lv["criteria"], lv["explanation"]):
            conn.execute(
                "UPDATE maturity_levels SET name=%s, criteria=%s, explanation=%s, parse_notes=%s, "
                "validation_status='pending_review', reviewed_by=NULL, reviewed_at=NULL WHERE id=%s",
                (lv["name"], Jsonb(lv["criteria"]), Jsonb(lv["explanation"]), Jsonb(lv["parse_notes"]), row[0]))
            _audit(conn, actor, "text_changed_reset_to_review", "maturity_level", row[0], {"level": lv["level"]})
            stats["reset_to_review"] += 1
    for sec in data["sections"]:
        row = conn.execute("SELECT id, original_text FROM framework_sections "
                           "WHERE framework_version_id=%s AND section_id=%s", (fv_id, sec["section_id"])).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO framework_sections (document_id, framework_version_id, section_id, title, original_text, "
                "page_index, page_label) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (doc_id, fv_id, sec["section_id"], sec["title"], sec["original_text"], sec["page_index"], sec["page_label"]))
            stats["maturity_inserted"] += 1
        elif row[1] != sec["original_text"]:
            conn.execute("UPDATE framework_sections SET original_text=%s, validation_status='pending_review', "
                         "reviewed_by=NULL, reviewed_at=NULL WHERE id=%s", (sec["original_text"], row[0]))
            _audit(conn, actor, "text_changed_reset_to_review", "framework_section", row[0], {"section": sec["section_id"]})
            stats["reset_to_review"] += 1


def load_source(conn: psycopg.Connection, source: dict, extracted_path: Path, storage_dir: Path,
                actor: str = "system") -> dict:
    rows = [json.loads(line) for line in extracted_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise ValueError(f"{extracted_path} contains no requirements")
    shas = {r["source_document_sha256"] for r in rows}
    if len(shas) != 1:
        raise ValueError("extracted file mixes several source documents")
    sha = shas.pop()
    history = _history_record(storage_dir, source["id"], sha)
    kind = "controls_framework"
    stats = {"inserted": 0, "unchanged": 0, "reset_to_review": 0, "maturity_inserted": 0}

    with conn.transaction():
        fw_id = conn.execute(
            "INSERT INTO frameworks (name, kind) VALUES (%s,%s) "
            "ON CONFLICT (name) DO UPDATE SET kind = EXCLUDED.kind RETURNING id",
            (source["framework"], kind)).fetchone()[0]
        fv_id = conn.execute(
            "INSERT INTO framework_versions (framework_id, version_label) VALUES (%s,%s) "
            "ON CONFLICT (framework_id, version_label) DO UPDATE SET version_label = EXCLUDED.version_label RETURNING id",
            (fw_id, source["version"])).fetchone()[0]
        doc_id = conn.execute(
            "INSERT INTO regulatory_documents (framework_version_id, source_id, source_url, sha256, storage_path, "
            "language, publication_date, retrieved_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (sha256) DO UPDATE SET sha256 = EXCLUDED.sha256 RETURNING id",
            (fv_id, source["id"], history["retrieved_from"], sha, history["stored_path"], source.get("language"),
             source.get("publication_date"), history["retrieved_at"])).fetchone()[0]

        other = conn.execute(
            "SELECT count(*) FROM requirements WHERE framework_version_id=%s AND document_id<>%s",
            (fv_id, doc_id)).fetchone()[0]
        if other:
            raise RuntimeError(
                f"{source['framework']} {source['version']} already has requirements from a different document. "
                "A changed document needs a new version label, not an overwrite.")

        for r in rows:
            existing = conn.execute(
                "SELECT id, original_text FROM requirements WHERE framework_version_id=%s AND control_id=%s",
                (fv_id, r["control_id"])).fetchone()
            cols = (r["printed_id"], r["id_is_derived"], r["kind"], r["level"], r["parent_control_id"], r["domain"],
                    r["subdomain_id"], r["subdomain_title"], r["principle"], r["objective"], r["page_index"],
                    r["page_label"], r["extraction_method"], r["extraction_confidence"], Jsonb(r["parse_notes"]))
            if existing is None:
                conn.execute(
                    "INSERT INTO requirements (document_id, framework_version_id, control_id, original_text, "
                    "printed_id, id_is_derived, kind, level, parent_control_id, domain, subdomain_id, subdomain_title, "
                    "principle, objective, page_index, page_label, extraction_method, extraction_confidence, parse_notes) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (doc_id, fv_id, r["control_id"], r["original_text"], *cols))
                stats["inserted"] += 1
                continue
            req_id, old_text = existing
            conn.execute(
                "UPDATE requirements SET printed_id=%s, id_is_derived=%s, kind=%s, level=%s, parent_control_id=%s, "
                "domain=%s, subdomain_id=%s, subdomain_title=%s, principle=%s, objective=%s, page_index=%s, "
                "page_label=%s, extraction_method=%s, extraction_confidence=%s, parse_notes=%s WHERE id=%s",
                (*cols, req_id))
            if old_text == r["original_text"]:
                stats["unchanged"] += 1
            else:
                conn.execute(
                    "UPDATE requirements SET original_text=%s, validation_status='pending_review', "
                    "reviewed_by=NULL, reviewed_at=NULL WHERE id=%s", (r["original_text"], req_id))
                _audit(conn, actor, "text_changed_reset_to_review", "requirement", req_id, {"control_id": r["control_id"]})
                stats["reset_to_review"] += 1

        maturity_file = extracted_path.parent / f"{source['id']}.maturity.json"
        if maturity_file.exists():
            _load_maturity(conn, doc_id, fv_id, maturity_file, actor, stats)
        _audit(conn, actor, "load_source", "regulatory_document", doc_id, {"source_id": source["id"], **stats})
    return {"document_id": doc_id, "framework_version_id": fv_id, "sha256": sha, **stats}

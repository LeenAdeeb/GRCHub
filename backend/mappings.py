"""Manual cross-framework control mappings with four-eyes approval. Mappings are shared regulatory knowledge
(not per organization). Similar wording is not equivalence: every mapping needs a written justification and a
second person's approval."""
from __future__ import annotations

from psycopg.rows import dict_row

from backend.audit import audit

RELATIONSHIPS = ("equivalent", "partial_overlap", "related", "independent")

_SELECT = (
    "SELECT m.id, m.relationship, m.justification, m.method, m.approval_status, m.created_at, m.decided_at, "
    "m.decision_note, cu.email AS created_by, du.email AS decided_by, "
    "m.source_req_id, sr.control_id AS source_control_id, sf.name AS source_framework, sv.version_label AS source_version, "
    "m.target_req_id, tr.control_id AS target_control_id, tf.name AS target_framework, tv.version_label AS target_version "
    "FROM control_mappings m JOIN users cu ON cu.id = m.created_by LEFT JOIN users du ON du.id = m.decided_by "
    "JOIN requirements sr ON sr.id = m.source_req_id JOIN framework_versions sv ON sv.id = sr.framework_version_id "
    "JOIN frameworks sf ON sf.id = sv.framework_id "
    "JOIN requirements tr ON tr.id = m.target_req_id JOIN framework_versions tv ON tv.id = tr.framework_version_id "
    "JOIN frameworks tf ON tf.id = tv.framework_id"
)


def create(conn, user_id: int, actor: str, source_id: int, target_id: int, relationship: str, justification: str,
           method: str = "manual") -> int:
    if relationship not in RELATIONSHIPS:
        raise ValueError(f"relationship must be one of {', '.join(RELATIONSHIPS)}")
    if source_id == target_id:
        raise ValueError("a requirement cannot be mapped to itself")
    if len(justification.strip()) < 20:
        raise ValueError("a justification of at least 20 characters is required")
    with conn.transaction():
        ok = conn.execute("SELECT count(*) FROM requirements WHERE id = ANY(%s) AND validation_status='approved'",
                          ([source_id, target_id],)).fetchone()[0]
        if ok != 2:
            raise ValueError("both requirements must exist and be approved by a reviewer")
        if conn.execute("SELECT 1 FROM control_mappings WHERE (source_req_id=%s AND target_req_id=%s) "
                        "OR (source_req_id=%s AND target_req_id=%s)", (source_id, target_id, target_id, source_id)).fetchone():
            raise ValueError("these two requirements are already mapped (in either direction)")
        mapping_id = conn.execute(
            "INSERT INTO control_mappings (source_req_id, target_req_id, relationship, justification, method, created_by) "
            "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
            (source_id, target_id, relationship, justification.strip(), method, user_id)).fetchone()[0]
        audit(conn, actor, "mapping_created", "control_mapping", mapping_id,
              {"source": source_id, "target": target_id, "relationship": relationship, "method": method})
    return mapping_id


def decide(conn, user_id: int, actor: str, mapping_id: int, decision: str, note: str | None) -> None:
    if decision not in ("approved", "rejected"):
        raise ValueError("decision must be 'approved' or 'rejected'")
    if decision == "rejected" and not (note and note.strip()):
        raise ValueError("a note is required when rejecting a mapping")
    with conn.transaction():
        row = conn.execute("SELECT created_by, approval_status FROM control_mappings WHERE id=%s FOR UPDATE",
                           (mapping_id,)).fetchone()
        if row is None:
            raise KeyError("mapping not found")
        if row[1] != "pending":
            raise ValueError(f"this mapping was already {row[1]}")
        if row[0] == user_id:
            raise PermissionError("a mapping must be approved or rejected by someone other than its creator")
        conn.execute("UPDATE control_mappings SET approval_status=%s, decided_by=%s, decided_at=now(), decision_note=%s "
                     "WHERE id=%s", (decision, user_id, note, mapping_id))
        audit(conn, actor, f"mapping_{decision}", "control_mapping", mapping_id, {"note": note})


def list_mappings(conn, requirement_id: int | None = None, status: str | None = None,
                  limit: int = 50, offset: int = 0) -> dict:
    where, params = [], []
    if requirement_id is not None:
        where.append("(m.source_req_id = %s OR m.target_req_id = %s)"); params += [requirement_id, requirement_id]
    if status:
        where.append("m.approval_status = %s"); params.append(status)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    items = conn.cursor(row_factory=dict_row).execute(
        _SELECT + clause + " ORDER BY m.id LIMIT %s OFFSET %s", (*params, limit, offset)).fetchall()
    total = conn.execute("SELECT count(*) FROM control_mappings m" + clause, params).fetchone()[0]
    return {"total": total, "limit": limit, "offset": offset, "items": items}

"""Compliance assessments. A result is a human judgment by an assessor, recorded against the exact framework
version; accepted evidence supports it but never decides it. Rows are append-only history; the latest row per
requirement is the current result."""
from __future__ import annotations

from psycopg.rows import dict_row

from backend.audit import audit
from backend.evidence import EFFECTIVE

RESULTS = ("compliant", "partially_compliant", "non_compliant", "not_applicable")
NEEDS_EVIDENCE = ("compliant", "partially_compliant")


def create(conn, org_id: int, user_id: int, actor: str, requirement_id: int, result: str,
           maturity_level: int | None, notes: str | None, evidence_ids: list[int]) -> int:
    if result not in RESULTS:
        raise ValueError(f"result must be one of {', '.join(RESULTS)}")
    ids = sorted(set(evidence_ids))
    with conn.transaction():
        req = conn.execute("SELECT framework_version_id, validation_status FROM requirements WHERE id=%s",
                           (requirement_id,)).fetchone()
        if req is None:
            raise KeyError("requirement not found")
        fv_id, status = req
        if status != "approved":
            raise ValueError("only requirements approved by a reviewer can be assessed")
        if ids:
            usable = conn.execute(
                "SELECT e.id FROM evidence e JOIN evidence_requirements er ON er.evidence_id = e.id AND er.requirement_id = %s "
                f"WHERE e.org_id = %s AND e.id = ANY(%s) AND {EFFECTIVE} = 'accepted'", (requirement_id, org_id, ids)).fetchall()
            if len(usable) != len(ids):
                raise ValueError("every cited evidence item must belong to your organization, be linked to this "
                                 "requirement, and currently be accepted (not expired)")
        if result in NEEDS_EVIDENCE and not ids:
            raise ValueError(f"'{result}' needs at least one accepted evidence item linked to this requirement")
        if result == "not_applicable" and not (notes and notes.strip()):
            raise ValueError("not_applicable needs a justification in the notes")

        levels = {r[0] for r in conn.execute("SELECT level FROM maturity_levels WHERE framework_version_id=%s", (fv_id,))}
        if maturity_level is not None:
            if not levels:
                raise ValueError("this framework defines no maturity model; leave maturity_level empty")
            if maturity_level not in levels:
                raise ValueError(f"maturity_level must be one of {sorted(levels)}")
            if result == "not_applicable":
                raise ValueError("a not_applicable result has no maturity level")

        assessment_id = conn.execute(
            "INSERT INTO assessments (org_id, requirement_id, assessor_id, result, maturity_level, notes, framework_version_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (org_id, requirement_id, user_id, result, maturity_level, notes, fv_id)).fetchone()[0]
        for eid in ids:
            conn.execute("INSERT INTO assessment_evidence (assessment_id, evidence_id) VALUES (%s,%s)", (assessment_id, eid))
        audit(conn, actor, "assessment_created", "assessment", assessment_id,
              {"requirement_id": requirement_id, "result": result, "maturity_level": maturity_level, "evidence": ids},
              org_id=org_id)
    return assessment_id


_SELECT = (
    "SELECT a.id, a.requirement_id, r.control_id, f.name AS framework, v.version_label AS version, a.result, "
    "a.maturity_level, a.notes, a.assessed_at, u.email AS assessor, "
    "COALESCE((SELECT array_agg(ae.evidence_id ORDER BY ae.evidence_id) FROM assessment_evidence ae WHERE ae.assessment_id = a.id), '{}') AS evidence_ids, "
    "NOT EXISTS (SELECT 1 FROM assessment_evidence ae JOIN evidence e ON e.id = ae.evidence_id "
    f"WHERE ae.assessment_id = a.id AND {EFFECTIVE} <> 'accepted') AS evidence_still_valid "
    "FROM assessments a JOIN requirements r ON r.id = a.requirement_id "
    "JOIN framework_versions v ON v.id = a.framework_version_id JOIN frameworks f ON f.id = v.framework_id "
    "JOIN users u ON u.id = a.assessor_id"
)


def current(conn, org_id: int, framework: str | None = None, result: str | None = None,
            limit: int = 50, offset: int = 0) -> dict:
    """Latest assessment per requirement. evidence_still_valid=False means the cited evidence has since
    been rejected or has expired, so the result should be revisited."""
    cur = conn.cursor(row_factory=dict_row)
    base = (f"SELECT * FROM (SELECT DISTINCT ON (a.requirement_id) {_SELECT[len('SELECT '):]} WHERE a.org_id = %s "
            "ORDER BY a.requirement_id, a.assessed_at DESC, a.id DESC) latest WHERE (%s::text IS NULL OR framework = %s) "
            "AND (%s::text IS NULL OR result = %s)")
    params = (org_id, framework, framework, result, result)
    items = cur.execute(base + " ORDER BY requirement_id LIMIT %s OFFSET %s", (*params, limit, offset)).fetchall()
    total = conn.execute(f"SELECT count(*) FROM ({base}) t", params).fetchone()[0]
    return {"total": total, "limit": limit, "offset": offset, "items": items}


def history(conn, org_id: int, requirement_id: int) -> list[dict]:
    return conn.cursor(row_factory=dict_row).execute(
        _SELECT + " WHERE a.org_id = %s AND a.requirement_id = %s ORDER BY a.assessed_at DESC, a.id DESC",
        (org_id, requirement_id)).fetchall()

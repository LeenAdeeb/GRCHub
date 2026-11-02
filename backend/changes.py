"""Regulatory change detection: compare two loaded versions of one framework and, for an organization, show
which assessments and evidence are affected. Requirements are matched by control ID, so a renumbered control
appears as one removed plus one added; a reviewer should read the lists, not trust them blindly."""
from __future__ import annotations

import re

from psycopg.rows import dict_row

from backend.evidence import EFFECTIVE


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _version_id(conn, framework: str, label: str) -> int:
    row = conn.execute("SELECT v.id FROM framework_versions v JOIN frameworks f ON f.id = v.framework_id "
                       "WHERE f.name = %s AND v.version_label = %s", (framework, label)).fetchone()
    if row is None:
        raise KeyError(f"{framework} version '{label}' is not loaded")
    return row[0]


def _requirements(conn, fv_id: int) -> dict[str, dict]:
    rows = conn.cursor(row_factory=dict_row).execute(
        "SELECT id, control_id, original_text, validation_status FROM requirements WHERE framework_version_id=%s", (fv_id,))
    return {r["control_id"]: r for r in rows}


def compare(conn, framework: str, from_version: str, to_version: str, org_id: int | None = None) -> dict:
    if from_version == to_version:
        raise ValueError("choose two different versions")
    old = _requirements(conn, _version_id(conn, framework, from_version))
    new = _requirements(conn, _version_id(conn, framework, to_version))
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    modified = sorted(c for c in set(old) & set(new) if _norm(old[c]["original_text"]) != _norm(new[c]["original_text"]))
    result = {
        "framework": framework, "from_version": from_version, "to_version": to_version,
        "added": [{"control_id": c, "requirement_id": new[c]["id"], "text": new[c]["original_text"]} for c in added],
        "removed": [{"control_id": c, "requirement_id": old[c]["id"], "text": old[c]["original_text"]} for c in removed],
        "modified": [{"control_id": c, "from_requirement_id": old[c]["id"], "to_requirement_id": new[c]["id"],
                      "from_text": old[c]["original_text"], "to_text": new[c]["original_text"]} for c in modified],
        "unchanged": len(set(old) & set(new)) - len(modified),
    }
    if org_id is not None:
        result["impact"] = _impact(conn, org_id, [old[c]["id"] for c in removed + modified],
                                   [new[c]["id"] for c in added])
    return result


def _impact(conn, org_id: int, changed_old_ids: list[int], added_new_ids: list[int]) -> dict:
    cur = conn.cursor(row_factory=dict_row)
    reassess = cur.execute(
        "SELECT r.control_id, a.requirement_id, a.id AS assessment_id, a.result FROM (SELECT DISTINCT ON (requirement_id) * "
        "FROM assessments WHERE org_id = %s AND requirement_id = ANY(%s) ORDER BY requirement_id, assessed_at DESC, id DESC) a "
        "JOIN requirements r ON r.id = a.requirement_id ORDER BY r.control_id", (org_id, changed_old_ids)).fetchall()
    evidence = cur.execute(
        f"SELECT DISTINCT e.id AS evidence_id, e.name, {EFFECTIVE} AS status, r.control_id "
        "FROM evidence e JOIN evidence_requirements er ON er.evidence_id = e.id JOIN requirements r ON r.id = er.requirement_id "
        "WHERE e.org_id = %s AND er.requirement_id = ANY(%s) ORDER BY e.id", (org_id, changed_old_ids)).fetchall()
    return {
        "assessments_to_redo": reassess,
        "evidence_to_revalidate": evidence,
        "new_requirements_to_assess": added_new_ids,
    }

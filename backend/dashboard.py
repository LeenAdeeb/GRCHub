"""Compliance posture for one organization.

What is counted, and how the posture is decided (all rules are explicit and returned with the numbers):

* Scope: requirements a reviewer has approved. Requirements still awaiting review are shown separately and make the
  posture provisional. 'control_reference' items (lists of references in the source) are not assessable controls and
  are left out.
* Applicable = approved requirements minus those the latest assessment marks not_applicable.
* Demonstrated = latest assessment is compliant AND every evidence item it cites is still accepted (not expired,
  not rejected). A compliant result resting on invalid evidence is NOT counted; it is reported as needing revalidation.
* Requirements not yet assessed count AGAINST the rate (compliance must be demonstrated, not assumed).
* Demonstrated rate = demonstrated / applicable. Partially compliant counts as not demonstrated.
* Posture label from the rate: >= 90% Substantially compliant, >= 70% Mostly compliant, >= 40% Partially compliant,
  otherwise Largely non-compliant. If fewer than 50% of applicable requirements are assessed, no label is given
  ("Insufficient assessment"): a rate over a mostly unassessed scope would be misleading.
These thresholds are this tool's convention, not a regulatory standard. Frameworks are never averaged together."""
from __future__ import annotations

from collections import defaultdict

from psycopg.rows import dict_row

from backend.evidence import EFFECTIVE

THRESHOLDS = {"substantially_compliant": 0.90, "mostly_compliant": 0.70, "partially_compliant": 0.40,
              "min_assessed_share": 0.50, "evidence_due_soon_days": 30}

LEVELS = {
    "not_assessed": "Not assessed",
    "insufficient": "Insufficient assessment",
    "substantially_compliant": "Substantially compliant",
    "mostly_compliant": "Mostly compliant",
    "partially_compliant": "Partially compliant",
    "largely_non_compliant": "Largely non-compliant",
}


def classify(applicable: int, assessed: int, demonstrated: int) -> dict:
    """Pure function: counts in, posture out."""
    if applicable == 0:
        return {"level": "not_assessed", "label": LEVELS["not_assessed"], "demonstrated_rate": None, "assessed_share": None}
    rate, share = demonstrated / applicable, assessed / applicable
    if share < THRESHOLDS["min_assessed_share"]:
        level = "insufficient"
    elif rate >= THRESHOLDS["substantially_compliant"]:
        level = "substantially_compliant"
    elif rate >= THRESHOLDS["mostly_compliant"]:
        level = "mostly_compliant"
    elif rate >= THRESHOLDS["partially_compliant"]:
        level = "partially_compliant"
    else:
        level = "largely_non_compliant"
    return {"level": level, "label": LEVELS[level], "demonstrated_rate": round(rate, 3), "assessed_share": round(share, 3)}


def _sum(rows: list[dict]) -> dict:
    keys = ("approved", "awaiting_review", "not_applicable", "demonstrated", "compliant_needs_revalidation", "partial",
            "non_compliant", "not_assessed", "with_accepted_evidence", "missing_evidence")
    return {k: sum(r[k] for r in rows) for k in keys}


def _posture(c: dict) -> dict:
    applicable = c["approved"] - c["not_applicable"]
    assessed = c["demonstrated"] + c["compliant_needs_revalidation"] + c["partial"] + c["non_compliant"]
    out = classify(applicable, assessed, c["demonstrated"])
    out |= {"applicable": applicable, "assessed": assessed, "demonstrated": c["demonstrated"], "partial": c["partial"],
            "non_compliant": c["non_compliant"], "not_assessed": c["not_assessed"],
            "compliant_needs_revalidation": c["compliant_needs_revalidation"], "not_applicable": c["not_applicable"],
            "scope_complete": c["awaiting_review"] == 0,
            "provisional": c["awaiting_review"] > 0 or assessed < applicable}
    return out


def _reasons(c: dict, p: dict) -> list[str]:
    why = []
    if c["awaiting_review"]:
        why.append(f"{c['awaiting_review']} requirement(s) are still awaiting reviewer approval, so this covers only the "
                   f"{c['approved']} approved so far.")
    if p["not_assessed"]:
        why.append(f"{p['not_assessed']} approved requirement(s) have not been assessed; they count against the rate.")
    if p["compliant_needs_revalidation"]:
        why.append(f"{p['compliant_needs_revalidation']} compliant result(s) rely on evidence that has expired or been "
                   "rejected; they are not counted as demonstrated until the evidence is renewed.")
    if p["level"] == "insufficient":
        why.append("Fewer than half of the applicable requirements are assessed, so no posture label is given.")
    return why


def overview(conn, org_id: int) -> dict:
    cur = conn.cursor(row_factory=dict_row)
    rows = cur.execute(f"""
        WITH latest AS (
            SELECT DISTINCT ON (a.requirement_id) a.id, a.requirement_id, a.result, a.maturity_level,
              NOT EXISTS (SELECT 1 FROM assessment_evidence ae JOIN evidence e ON e.id = ae.evidence_id
                          WHERE ae.assessment_id = a.id AND {EFFECTIVE} <> 'accepted') AS evidence_valid
            FROM assessments a WHERE a.org_id = %(org)s
            ORDER BY a.requirement_id, a.assessed_at DESC, a.id DESC),
        accepted AS (
            SELECT DISTINCT er.requirement_id FROM evidence_requirements er JOIN evidence e ON e.id = er.evidence_id
            WHERE e.org_id = %(org)s AND {EFFECTIVE} = 'accepted'),
        any_evidence AS (
            SELECT DISTINCT er.requirement_id FROM evidence_requirements er JOIN evidence e ON e.id = er.evidence_id
            WHERE e.org_id = %(org)s)
        SELECT f.name AS framework, v.version_label AS version, v.id AS framework_version_id, r.domain,
          count(*) FILTER (WHERE r.validation_status = 'approved') AS approved,
          count(*) FILTER (WHERE r.validation_status <> 'approved') AS awaiting_review,
          count(*) FILTER (WHERE r.validation_status = 'approved' AND latest.result = 'not_applicable') AS not_applicable,
          count(*) FILTER (WHERE r.validation_status = 'approved' AND latest.result = 'compliant' AND latest.evidence_valid) AS demonstrated,
          count(*) FILTER (WHERE r.validation_status = 'approved' AND latest.result = 'compliant' AND NOT latest.evidence_valid) AS compliant_needs_revalidation,
          count(*) FILTER (WHERE r.validation_status = 'approved' AND latest.result = 'partially_compliant') AS partial,
          count(*) FILTER (WHERE r.validation_status = 'approved' AND latest.result = 'non_compliant') AS non_compliant,
          count(*) FILTER (WHERE r.validation_status = 'approved' AND latest.requirement_id IS NULL) AS not_assessed,
          count(*) FILTER (WHERE r.validation_status = 'approved' AND accepted.requirement_id IS NOT NULL) AS with_accepted_evidence,
          count(*) FILTER (WHERE r.validation_status = 'approved' AND any_evidence.requirement_id IS NULL) AS missing_evidence
        FROM requirements r
        JOIN framework_versions v ON v.id = r.framework_version_id JOIN frameworks f ON f.id = v.framework_id
        LEFT JOIN latest ON latest.requirement_id = r.id
        LEFT JOIN accepted ON accepted.requirement_id = r.id
        LEFT JOIN any_evidence ON any_evidence.requirement_id = r.id
        WHERE r.kind <> 'control_reference'
        GROUP BY f.name, v.version_label, v.id, r.domain
        ORDER BY f.name, v.version_label, r.domain NULLS LAST""", {"org": org_id}).fetchall()

    by_fw: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        by_fw[r["framework_version_id"]].append(r)

    frameworks = []
    for fv_id, group in by_fw.items():
        c = _sum(group)
        posture = _posture(c)
        posture["reasons"] = _reasons(c, posture)
        maturity = cur.execute(
            "SELECT l.maturity_level, count(*) AS n FROM (SELECT DISTINCT ON (a.requirement_id) a.requirement_id, "
            "a.maturity_level FROM assessments a JOIN requirements r ON r.id = a.requirement_id "
            "WHERE a.org_id = %s AND r.framework_version_id = %s "
            "ORDER BY a.requirement_id, a.assessed_at DESC, a.id DESC) l WHERE l.maturity_level IS NOT NULL "
            "GROUP BY l.maturity_level ORDER BY 1", (org_id, fv_id)).fetchall()
        levels = [m["maturity_level"] for m in maturity for _ in range(m["n"])]
        domains = []
        for d in group:
            dc = _sum([d])
            if dc["approved"] + dc["awaiting_review"] == 0:
                continue
            dp = _posture(dc)
            domains.append({"domain": d["domain"] or "(no domain)", "approved": dc["approved"],
                            "awaiting_review": dc["awaiting_review"], **dp})
        first = group[0]
        frameworks.append({
            "framework": first["framework"], "version": first["version"], "framework_version_id": fv_id,
            "approved_requirements": c["approved"], "awaiting_review": c["awaiting_review"],
            "not_assessed": c["not_assessed"], "compliant": c["demonstrated"] + c["compliant_needs_revalidation"],
            "partially_compliant": c["partial"], "non_compliant": c["non_compliant"], "not_applicable": c["not_applicable"],
            "with_accepted_evidence": c["with_accepted_evidence"], "missing_evidence": c["missing_evidence"],
            "posture": posture, "domains": domains,
            "maturity_distribution": {m["maturity_level"]: m["n"] for m in maturity},
            "median_maturity": levels[len(levels) // 2] if levels else None,
        })

    status_counts = {r["status"]: r["n"] for r in cur.execute(
        f"SELECT {EFFECTIVE} AS status, count(*) AS n FROM evidence e WHERE e.org_id = %s GROUP BY 1", (org_id,))}
    stale = conn.execute(f"""
        SELECT count(*) FROM (SELECT DISTINCT ON (a.requirement_id) a.id FROM assessments a WHERE a.org_id = %s
            ORDER BY a.requirement_id, a.assessed_at DESC, a.id DESC) l
        WHERE EXISTS (SELECT 1 FROM assessment_evidence ae JOIN evidence e ON e.id = ae.evidence_id
                      WHERE ae.assessment_id = l.id AND {EFFECTIVE} <> 'accepted')""", (org_id,)).fetchone()[0]
    days = THRESHOLDS["evidence_due_soon_days"]
    due_soon = cur.execute(
        f"SELECT e.id, e.name, e.review_due_date FROM evidence e WHERE e.org_id = %s AND {EFFECTIVE} = 'accepted' "
        "AND e.review_due_date IS NOT NULL AND e.review_due_date <= CURRENT_DATE + %s ORDER BY e.review_due_date LIMIT 10",
        (org_id, days)).fetchall()
    gaps = cur.execute(
        "SELECT f.name AS framework, r.control_id, a.result FROM (SELECT DISTINCT ON (requirement_id) requirement_id, result "
        "FROM assessments WHERE org_id = %s ORDER BY requirement_id, assessed_at DESC, id DESC) a "
        "JOIN requirements r ON r.id = a.requirement_id JOIN framework_versions v ON v.id = r.framework_version_id "
        "JOIN frameworks f ON f.id = v.framework_id WHERE a.result = 'non_compliant' ORDER BY f.name, r.control_id LIMIT 15",
        (org_id,)).fetchall()
    return {
        "frameworks": frameworks,
        "thresholds": THRESHOLDS,
        "evidence": {
            "by_status": status_counts,
            "needs_attention": {k: status_counts.get(k, 0) for k in ("submitted", "under_review", "rejected", "expired")},
            "assessments_relying_on_invalid_evidence": stale,
            "due_soon": due_soon,
        },
        "top_gaps": gaps,
    }

"""Shared regulatory content: frameworks, requirements, maturity model, and the review of that content.
Reading needs a login; reviewing needs the platform role 'reviewer' or 'platform_admin'."""
from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from backend import review
from backend.api.deps import Principal, current_user, get_conn, require_roles

router = APIRouter()
REVIEWERS = ("reviewer", "platform_admin")


class ReviewIn(BaseModel):
    decision: Literal["approved", "rejected"]
    note: str | None = Field(default=None, max_length=2000)


@router.get("/stats")
def get_stats(_: Principal = Depends(current_user), conn=Depends(get_conn)) -> list[dict]:
    return review.stats(conn)


@router.get("/frameworks")
def frameworks(_: Principal = Depends(current_user), conn=Depends(get_conn)) -> list[dict]:
    return conn.cursor(row_factory=dict_row).execute(
        "SELECT f.name AS framework, f.kind, v.id AS framework_version_id, v.version_label AS version, "
        "d.sha256 AS document_sha256, d.source_url, d.publication_date, count(r.id) AS requirements "
        "FROM frameworks f JOIN framework_versions v ON v.framework_id = f.id "
        "LEFT JOIN regulatory_documents d ON d.framework_version_id = v.id "
        "LEFT JOIN requirements r ON r.framework_version_id = v.id "
        "GROUP BY f.name, f.kind, v.id, v.version_label, d.sha256, d.source_url, d.publication_date ORDER BY 1,4").fetchall()


@router.get("/subdomains")
def subdomains(framework: str | None = None, _: Principal = Depends(current_user), conn=Depends(get_conn)) -> list[dict]:
    rows = conn.cursor(row_factory=dict_row).execute(
        "SELECT DISTINCT f.name AS framework, r.subdomain_id, r.subdomain_title "
        "FROM requirements r JOIN framework_versions v ON v.id = r.framework_version_id "
        "JOIN frameworks f ON f.id = v.framework_id "
        "WHERE r.subdomain_id IS NOT NULL AND (%s::text IS NULL OR f.name = %s)", (framework, framework)).fetchall()
    # numeric order (1-2 before 1-10; 3.2.1 before 3.2.1.1)
    return sorted(rows, key=lambda r: (r["framework"], [int(x) for x in re.split(r"[-.]", r["subdomain_id"])]))


@router.get("/requirements")
def list_requirements(
    framework: str | None = None,
    subdomain: str | None = None,
    status: Literal["pending_review", "approved", "rejected"] | None = None,
    flagged: bool = False,
    q: str | None = Query(default=None, max_length=200, description="substring of the requirement text"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: Principal = Depends(current_user),
    conn=Depends(get_conn),
) -> dict:
    items = review.list_requirements(conn, status, framework, subdomain, flagged, limit, q, offset)
    total = review.count_requirements(conn, status, framework, subdomain, flagged, q)
    return {"total": total, "limit": limit, "offset": offset, "items": items}


@router.get("/requirements/{req_id}")
def get_requirement(req_id: int, _: Principal = Depends(current_user), conn=Depends(get_conn)) -> dict:
    try:
        row = review.get(conn, req_id)
    except KeyError:
        raise HTTPException(404, "requirement not found")
    return {**row, "reviews": review.history(conn, req_id)}


@router.post("/requirements/{req_id}/review")
def post_review(req_id: int, body: ReviewIn, user: Principal = Depends(require_roles(*REVIEWERS)),
                conn=Depends(get_conn)) -> dict:
    try:
        review.decide(conn, req_id, body.decision, user.email, body.note)     # reviewer = the authenticated user
    except KeyError:
        raise HTTPException(404, "requirement not found")
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {**review.get(conn, req_id), "reviews": review.history(conn, req_id)}


@router.get("/maturity")
def maturity(framework: str | None = None, _: Principal = Depends(current_user), conn=Depends(get_conn)) -> dict:
    cur = conn.cursor(row_factory=dict_row)
    levels = cur.execute(
        "SELECT m.id, f.name AS framework, v.version_label AS version, m.level, m.name, m.criteria, m.explanation, "
        "m.page_index, m.page_label, m.parse_notes, m.validation_status, m.reviewed_by "
        "FROM maturity_levels m JOIN framework_versions v ON v.id = m.framework_version_id "
        "JOIN frameworks f ON f.id = v.framework_id WHERE (%s::text IS NULL OR f.name = %s) "
        "ORDER BY f.name, v.version_label, m.level", (framework, framework)).fetchall()
    sections = cur.execute(
        "SELECT s.id, f.name AS framework, v.version_label AS version, s.section_id, s.title, s.original_text, "
        "s.page_index, s.page_label, s.validation_status, s.reviewed_by "
        "FROM framework_sections s JOIN framework_versions v ON v.id = s.framework_version_id "
        "JOIN frameworks f ON f.id = v.framework_id WHERE (%s::text IS NULL OR f.name = %s) "
        "ORDER BY f.name, s.section_id", (framework, framework)).fetchall()
    return {"levels": levels, "sections": sections}


def _review_item(table: str, item_id: int, body: ReviewIn, user: Principal, conn) -> dict:
    try:
        review.decide_item(conn, table, item_id, body.decision, user.email, body.note)
    except KeyError:
        raise HTTPException(404, "item not found")
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"id": item_id, "validation_status": body.decision, "reviewed_by": user.email}


@router.post("/maturity/levels/{item_id}/review")
def review_level(item_id: int, body: ReviewIn, user: Principal = Depends(require_roles(*REVIEWERS)), conn=Depends(get_conn)) -> dict:
    return _review_item("maturity_levels", item_id, body, user, conn)


@router.post("/maturity/sections/{item_id}/review")
def review_section(item_id: int, body: ReviewIn, user: Principal = Depends(require_roles(*REVIEWERS)), conn=Depends(get_conn)) -> dict:
    return _review_item("framework_sections", item_id, body, user, conn)

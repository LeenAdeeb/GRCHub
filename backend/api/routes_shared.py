"""Shared regulatory knowledge: control mappings and version comparison."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend import changes, mappings
from backend.api.deps import Principal, current_user, get_conn, org_scope, require_roles

router = APIRouter()
CURATORS = ("reviewer", "platform_admin")


class MappingIn(BaseModel):
    source_req_id: int
    target_req_id: int
    relationship: Literal["equivalent", "partial_overlap", "related", "independent"]
    justification: str = Field(min_length=1, max_length=4000)


class MappingDecisionIn(BaseModel):
    decision: Literal["approved", "rejected"]
    note: str | None = Field(default=None, max_length=2000)


def _handle(exc: Exception):
    if isinstance(exc, KeyError):
        return HTTPException(404, str(exc.args[0]) if exc.args else "not found")
    if isinstance(exc, PermissionError):
        return HTTPException(403, str(exc))
    return HTTPException(422, str(exc))


@router.post("/mappings", status_code=201)
def create_mapping(body: MappingIn, user: Principal = Depends(require_roles(*CURATORS)), conn=Depends(get_conn)) -> dict:
    try:
        new_id = mappings.create(conn, user.id, user.email, body.source_req_id, body.target_req_id,
                                 body.relationship, body.justification)
    except (ValueError, KeyError, PermissionError) as e:
        raise _handle(e)
    return {"id": new_id, "approval_status": "pending"}


@router.post("/mappings/{mapping_id}/decision")
def decide_mapping(mapping_id: int, body: MappingDecisionIn,
                   user: Principal = Depends(require_roles(*CURATORS)), conn=Depends(get_conn)) -> dict:
    try:
        mappings.decide(conn, user.id, user.email, mapping_id, body.decision, body.note)
    except (ValueError, KeyError, PermissionError) as e:
        raise _handle(e)
    return {"id": mapping_id, "approval_status": body.decision}


@router.get("/mappings")
def list_mappings(
    requirement_id: int | None = None,
    status: Literal["pending", "approved", "rejected"] | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: Principal = Depends(current_user),
    conn=Depends(get_conn),
) -> dict:
    return mappings.list_mappings(conn, requirement_id, status, limit, offset)


@router.get("/changes")
def compare_versions(framework: str, from_version: str, to_version: str, org_id: int | None = None,
                     user: Principal = Depends(current_user), conn=Depends(get_conn)) -> dict:
    """Added/removed/modified requirements between two loaded versions. Organization users also get the
    impact on their own assessments and evidence."""
    scoped = None
    if user.org_id is not None or (user.role == "platform_admin" and org_id is not None):
        scoped = org_scope(user, org_id)
    try:
        return changes.compare(conn, framework, from_version, to_version, scoped)
    except (ValueError, KeyError) as e:
        raise _handle(e)

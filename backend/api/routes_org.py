"""Organization-scoped endpoints: evidence, assessments and the compliance dashboard.
Every handler resolves the organization from the login (org_scope), never from user-supplied ids."""
from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend import assessments, dashboard, evidence
from backend.api.deps import ORG_ROLES, Principal, get_conn, org_scope, require_roles

router = APIRouter()
READERS = (*ORG_ROLES, "platform_admin")
SUBMITTERS = ("org_admin", "analyst")
JUDGES = ("org_admin", "assessor")


class TransitionIn(BaseModel):
    to: Literal["under_review", "accepted", "rejected", "expired"]
    comment: str | None = Field(default=None, max_length=2000)


class RequirementsIn(BaseModel):
    requirement_ids: list[int] = Field(max_length=500)


class AssessmentIn(BaseModel):
    requirement_id: int
    result: Literal["compliant", "partially_compliant", "non_compliant", "not_applicable"]
    maturity_level: int | None = Field(default=None, ge=0, le=9)
    notes: str | None = Field(default=None, max_length=4000)
    evidence_ids: list[int] = Field(default_factory=list, max_length=100)


async def _read_upload(file: UploadFile | None) -> tuple[str, bytes] | None:
    if file is None or not file.filename:
        return None
    data = await file.read(evidence.MAX_BYTES + 1)       # never buffer more than the limit
    return file.filename, data


def _ids(text: str | None) -> list[int]:
    try:
        return [int(x) for x in (text or "").split(",") if x.strip()]
    except ValueError:
        raise HTTPException(422, "requirement_ids must be comma-separated integers")


def _handle(exc: Exception):
    if isinstance(exc, KeyError):
        return HTTPException(404, str(exc.args[0]) if exc.args else "not found")
    if isinstance(exc, PermissionError):
        return HTTPException(403, str(exc))
    return HTTPException(422, str(exc))


# ---------------------------------------------------------------- evidence

@router.post("/evidence", status_code=201)
async def create_evidence(
    name: str = Form(max_length=300),
    description: str | None = Form(default=None, max_length=4000),
    review_due_date: date | None = Form(default=None),
    owner_user_id: int | None = Form(default=None),
    requirement_ids: str | None = Form(default=None, description="comma-separated requirement ids"),
    external_url: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    user: Principal = Depends(require_roles(*SUBMITTERS)),
    conn=Depends(get_conn),
) -> dict:
    upload = await _read_upload(file)
    try:
        new_id = evidence.create(conn, org_scope(user), user.id, user.email, name, description, review_due_date,
                                 owner_user_id, _ids(requirement_ids), upload, external_url)
        return evidence.get(conn, org_scope(user), new_id)
    except (ValueError, KeyError, PermissionError) as e:
        raise _handle(e)


@router.post("/evidence/{evidence_id}/attachment")
async def attach_evidence(
    evidence_id: int,
    external_url: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    user: Principal = Depends(require_roles(*SUBMITTERS)),
    conn=Depends(get_conn),
) -> dict:
    upload = await _read_upload(file)
    try:
        evidence.attach(conn, org_scope(user), user.id, user.email, evidence_id, upload, external_url)
        return evidence.get(conn, org_scope(user), evidence_id)
    except (ValueError, KeyError, PermissionError) as e:
        raise _handle(e)


@router.put("/evidence/{evidence_id}/requirements")
def set_evidence_requirements(evidence_id: int, body: RequirementsIn,
                              user: Principal = Depends(require_roles(*SUBMITTERS)), conn=Depends(get_conn)) -> dict:
    try:
        evidence.set_requirements(conn, org_scope(user), user.email, evidence_id, body.requirement_ids)
        return evidence.get(conn, org_scope(user), evidence_id)
    except (ValueError, KeyError, PermissionError) as e:
        raise _handle(e)


@router.post("/evidence/{evidence_id}/transition")
def transition_evidence(evidence_id: int, body: TransitionIn,
                        user: Principal = Depends(require_roles(*JUDGES)), conn=Depends(get_conn)) -> dict:
    try:
        evidence.transition(conn, org_scope(user), user.id, user.email, evidence_id, body.to, body.comment)
        return evidence.get(conn, org_scope(user), evidence_id)
    except (ValueError, KeyError, PermissionError) as e:
        raise _handle(e)


@router.get("/evidence")
def list_evidence(
    status: Literal["pending", "submitted", "under_review", "accepted", "rejected", "expired"] | None = None,
    requirement_id: int | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    org_id: int | None = None,
    user: Principal = Depends(require_roles(*READERS)),
    conn=Depends(get_conn),
) -> dict:
    return evidence.list_evidence(conn, org_scope(user, org_id), status, requirement_id, limit, offset)


@router.get("/evidence/{evidence_id}")
def get_evidence(evidence_id: int, org_id: int | None = None, user: Principal = Depends(require_roles(*READERS)),
                 conn=Depends(get_conn)) -> dict:
    try:
        return evidence.get(conn, org_scope(user, org_id), evidence_id)
    except KeyError:
        raise HTTPException(404, "evidence not found")


@router.get("/evidence/{evidence_id}/download")
def download_evidence(evidence_id: int, org_id: int | None = None,
                      user: Principal = Depends(require_roles(*READERS)), conn=Depends(get_conn)) -> FileResponse:
    try:
        path, filename = evidence.file_for_download(conn, org_scope(user, org_id), evidence_id)
    except KeyError as e:
        raise HTTPException(404, str(e.args[0]))
    if not path.is_file():
        raise HTTPException(404, "the stored file is missing")
    # Always a download, never rendered in the browser, and never trusted as a content type.
    return FileResponse(path, media_type="application/octet-stream", filename=filename,
                        headers={"X-Content-Type-Options": "nosniff", "Content-Disposition": f'attachment; filename="{filename}"'})


# ---------------------------------------------------------------- assessments

@router.post("/assessments", status_code=201)
def create_assessment(body: AssessmentIn, user: Principal = Depends(require_roles(*JUDGES)), conn=Depends(get_conn)) -> dict:
    org = org_scope(user)
    try:
        new_id = assessments.create(conn, org, user.id, user.email, body.requirement_id, body.result,
                                    body.maturity_level, body.notes, body.evidence_ids)
    except (ValueError, KeyError, PermissionError) as e:
        raise _handle(e)
    return {"id": new_id, "history": assessments.history(conn, org, body.requirement_id)}


@router.get("/assessments")
def list_assessments(
    framework: str | None = None,
    result: Literal["compliant", "partially_compliant", "non_compliant", "not_applicable"] | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    org_id: int | None = None,
    user: Principal = Depends(require_roles(*READERS)),
    conn=Depends(get_conn),
) -> dict:
    return assessments.current(conn, org_scope(user, org_id), framework, result, limit, offset)


@router.get("/assessments/history/{requirement_id}")
def assessment_history(requirement_id: int, org_id: int | None = None,
                       user: Principal = Depends(require_roles(*READERS)), conn=Depends(get_conn)) -> list[dict]:
    return assessments.history(conn, org_scope(user, org_id), requirement_id)


# ---------------------------------------------------------------- dashboard

@router.get("/dashboard")
def get_dashboard(org_id: int | None = None, user: Principal = Depends(require_roles(*READERS)),
                  conn=Depends(get_conn)) -> dict:
    return dashboard.overview(conn, org_scope(user, org_id))

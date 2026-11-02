"""AI-assisted mapping suggestions. Reviewers and platform admins only. Only a platform_admin can spend model
calls. Suggestions never become mappings by themselves (see backend/suggestions.py)."""
from __future__ import annotations

import threading
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend import llm, suggestions
from backend.api.deps import Principal, get_conn, require_roles
from backend.database.loader import connect

router = APIRouter(prefix="/suggestions")
CURATORS = ("reviewer", "platform_admin")
Rel = Literal["equivalent", "partial_overlap", "related", "independent"]

# In-process job table: fine for one server process (see docs/SECURITY.md). Suggestions themselves live in the database.
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


class GenerateIn(BaseModel):
    source_framework: str = Field(max_length=100)
    target_framework: str = Field(max_length=100)
    top_k: int = Field(default=5, ge=1, le=10)
    min_score: float = Field(default=0.15, ge=0.0, le=1.0)
    only_approved: bool = True


class RunIn(BaseModel):
    source_framework: str = Field(max_length=100)
    target_framework: str = Field(max_length=100)
    max_calls: int = Field(default=50, ge=1, le=500)
    provider: Literal["anthropic", "openai", "gemini"] | None = None


class DismissIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class ProposeIn(BaseModel):
    relationship: Rel
    justification: str = Field(min_length=1, max_length=4000)


@router.get("/config")
def config(_: Principal = Depends(require_roles(*CURATORS))) -> dict:
    st = llm.status()
    return {"llm_configured": st["configured"], **st}


@router.post("/generate")
def generate(body: GenerateIn, user: Principal = Depends(require_roles(*CURATORS)), conn=Depends(get_conn)) -> dict:
    try:
        return suggestions.generate(conn, user.email, body.source_framework, body.target_framework, body.top_k,
                                    body.min_score, body.only_approved)
    except KeyError as e:
        raise HTTPException(404, str(e.args[0]))
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.get("")
def list_suggestions(
    status: Literal["suggested", "proposed", "dismissed"] = "suggested",
    source_framework: str | None = None, target_framework: str | None = None,
    relationship: Rel | None = None, hide_independent: bool = True,
    limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0),
    _: Principal = Depends(require_roles(*CURATORS)), conn=Depends(get_conn),
) -> dict:
    return suggestions.list_suggestions(conn, status, source_framework, target_framework, relationship,
                                        hide_independent, limit, offset)


@router.get("/llm/estimate")
def estimate(source_framework: str, target_framework: str, provider: Literal["anthropic", "openai", "gemini"] | None = None,
             _: Principal = Depends(require_roles(*CURATORS)), conn=Depends(get_conn)) -> dict:
    return suggestions.estimate(conn, source_framework, target_framework, provider)


def _run_job(job_id: str, actor: str, body: RunIn) -> None:
    job = _jobs[job_id]
    try:
        client = llm.Client(body.provider)
        try:
            with connect() as conn:
                conn.autocommit = True
                job["result"] = suggestions.refine(
                    conn, actor, body.source_framework, body.target_framework, client, body.max_calls,
                    progress=lambda d, t: job.update(done=d, total=t))
        finally:
            client.close()
        job["state"] = "finished"
    except Exception as e:      # the message never contains the API key
        job["state"], job["error"] = "failed", f"{e.__class__.__name__}: {e}"


@router.post("/llm/run", status_code=202)
def start_run(body: RunIn, user: Principal = Depends(require_roles("platform_admin"))) -> dict:
    if not llm.configured():
        raise HTTPException(503, "No AI key is set on the server (ANTHROPIC_API_KEY, OPENAI_API_KEY or GEMINI_API_KEY), "
                                 "so AI refinement is unavailable")
    chosen = body.provider or llm.default_provider()
    if chosen not in llm.available():
        raise HTTPException(422, f"provider '{chosen}' has no key configured; available: {', '.join(llm.available())}")
    if not llm.model_for(chosen):
        raise HTTPException(422, f"set GRCHUB_{chosen.upper()}_MODEL on the server to name the {chosen} model to use")
    with _jobs_lock:
        if any(j["state"] == "running" for j in _jobs.values()):
            raise HTTPException(409, "an AI refinement run is already in progress")
        job_id = uuid.uuid4().hex[:12]
        _jobs[job_id] = {"state": "running", "done": 0, "total": 0, "result": None, "error": None}
    threading.Thread(target=_run_job, args=(job_id, user.email, body), daemon=True).start()
    return {"job_id": job_id}


@router.get("/llm/jobs/{job_id}")
def job_status(job_id: str, _: Principal = Depends(require_roles(*CURATORS))) -> dict:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found (it may have been lost on a server restart; the saved results are kept)")
    return job


@router.post("/{suggestion_id}/dismiss")
def dismiss(suggestion_id: int, body: DismissIn, user: Principal = Depends(require_roles(*CURATORS)),
            conn=Depends(get_conn)) -> dict:
    try:
        suggestions.dismiss(conn, user.id, user.email, suggestion_id, body.note)
    except KeyError as e:
        raise HTTPException(404, str(e.args[0]))
    return {"id": suggestion_id, "status": "dismissed"}


@router.post("/{suggestion_id}/propose", status_code=201)
def propose(suggestion_id: int, body: ProposeIn, user: Principal = Depends(require_roles(*CURATORS)),
            conn=Depends(get_conn)) -> dict:
    try:
        mapping_id = suggestions.propose(conn, user.id, user.email, suggestion_id, body.relationship, body.justification)
    except KeyError as e:
        raise HTTPException(404, str(e.args[0]))
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"mapping_id": mapping_id, "approval_status": "pending"}

"""Login, current user, organizations and user management."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from psycopg.rows import dict_row

from backend import security, users
from backend.api.deps import Principal, current_user, get_conn, require_roles

router = APIRouter()


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)


class OrgIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class UserIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)
    role: Literal["platform_admin", "reviewer", "org_admin", "analyst", "assessor", "viewer"]
    org_id: int | None = None


class ChangePasswordIn(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


class ResetPasswordIn(BaseModel):
    new_password: str = Field(max_length=256)


class ActiveIn(BaseModel):
    is_active: bool


class SetupIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)


@router.get("/auth/setup")
def setup_status(conn=Depends(get_conn)) -> dict:
    """Public: tells the web page whether to show the first-run 'create the first administrator' form."""
    return {"needed": users.setup_needed(conn)}


@router.post("/auth/setup", status_code=201)
def setup(body: SetupIn, conn=Depends(get_conn)) -> dict:
    """Public but one-shot: only works while the database has no users at all."""
    try:
        new_id = users.create_first_admin(conn, body.email, body.password)
    except PermissionError:
        raise HTTPException(409, "Setup is already complete. Sign in instead.")
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"id": new_id, "role": "platform_admin"}


@router.post("/auth/login")
def login(body: LoginIn, conn=Depends(get_conn)) -> dict:
    if security.is_locked_out(body.email):
        raise HTTPException(429, "Too many failed attempts. Try again later.")
    found = users.authenticate(conn, body.email, body.password)
    if found is None:
        security.record_failure(body.email)
        raise HTTPException(401, "Incorrect email or password")
    security.clear_failures(body.email)
    user_id, role, org_id, email = found
    try:
        token = security.create_token(user_id, role, org_id, users.fingerprint(conn, user_id))
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    return {"access_token": token, "token_type": "bearer", "email": email, "role": role, "org_id": org_id}


@router.get("/auth/me")
def me(user: Principal = Depends(current_user)) -> dict:
    return {"id": user.id, "email": user.email, "role": user.role, "org_id": user.org_id}


@router.post("/auth/change-password")
def change_password(body: ChangePasswordIn, user: Principal = Depends(current_user), conn=Depends(get_conn)) -> dict:
    try:
        users.change_password(conn, user.id, body.current_password, body.new_password, user.email)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    # The old token stops working; the person signs in again with the new password.
    return {"changed": True}


@router.post("/organizations", status_code=201)
def create_org(body: OrgIn, user: Principal = Depends(require_roles("platform_admin")), conn=Depends(get_conn)) -> dict:
    try:
        return {"id": users.create_organization(conn, body.name, user.email), "name": body.name.strip()}
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.get("/organizations")
def list_orgs(user: Principal = Depends(require_roles("platform_admin")), conn=Depends(get_conn)) -> list[dict]:
    return conn.cursor(row_factory=dict_row).execute("SELECT id, name, created_at FROM organizations ORDER BY id").fetchall()


@router.post("/users", status_code=201)
def create_user(body: UserIn, user: Principal = Depends(require_roles("platform_admin", "org_admin")),
                conn=Depends(get_conn)) -> dict:
    org_id = body.org_id
    if user.role == "org_admin":
        # An organization admin can only add organization users to their own organization.
        if body.role in users.PLATFORM_ROLES:
            raise HTTPException(403, "Only a platform_admin can create platform users")
        if org_id not in (None, user.org_id):
            raise HTTPException(403, "You can only create users in your own organization")
        org_id = user.org_id
    try:
        new_id = users.create_user(conn, body.email, body.password, body.role, org_id, user.email)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"id": new_id, "email": body.email.strip().lower(), "role": body.role, "org_id": org_id}


@router.get("/users")
def list_users(user: Principal = Depends(require_roles("platform_admin", "org_admin")), conn=Depends(get_conn)) -> list[dict]:
    sql = ("SELECT u.id, u.org_id, o.name AS org_name, u.email, u.role, u.is_active, u.created_at "
           "FROM users u LEFT JOIN organizations o ON o.id = u.org_id")
    params: tuple = ()
    if user.role == "org_admin":
        sql += " WHERE u.org_id = %s"
        params = (user.org_id,)
    return conn.cursor(row_factory=dict_row).execute(sql + " ORDER BY u.id", params).fetchall()


@router.patch("/users/{user_id}/active")
def set_user_active(user_id: int, body: ActiveIn,
                    user: Principal = Depends(require_roles("platform_admin", "org_admin")), conn=Depends(get_conn)) -> dict:
    target = conn.execute("SELECT org_id FROM users WHERE id=%s", (user_id,)).fetchone()
    if target is None or (user.role == "org_admin" and target[0] != user.org_id):
        raise HTTPException(404, "user not found")       # same answer whether missing or in another organization
    if user_id == user.id and not body.is_active:
        raise HTTPException(422, "You cannot deactivate your own account")
    users.set_active(conn, user_id, body.is_active, user.email)
    return {"id": user_id, "is_active": body.is_active}


@router.put("/users/{user_id}/password")
def reset_user_password(user_id: int, body: ResetPasswordIn,
                        user: Principal = Depends(require_roles("platform_admin", "org_admin")), conn=Depends(get_conn)) -> dict:
    target = conn.execute("SELECT org_id FROM users WHERE id=%s", (user_id,)).fetchone()
    if target is None or (user.role == "org_admin" and target[0] != user.org_id):
        raise HTTPException(404, "user not found")
    if user_id == user.id:
        raise HTTPException(422, "Use 'change password' for your own account")
    try:
        users.reset_password(conn, user_id, body.new_password, user.email)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"id": user_id, "reset": True}

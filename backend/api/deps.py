"""Shared FastAPI dependencies: database connection, authentication and role checks."""
from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Depends, Header, HTTPException

from backend import security
from backend.database.loader import connect

PLATFORM_ROLES = ("platform_admin", "reviewer")
ORG_ROLES = ("org_admin", "analyst", "assessor", "viewer")


@dataclass(frozen=True)
class Principal:
    id: int
    email: str
    role: str
    org_id: int | None


def get_conn():
    conn = connect()
    conn.autocommit = True      # writes use `with conn.transaction()` and commit when that block ends
    try:
        yield conn
    finally:
        conn.close()


def bearer_claims(authorization: str | None = Header(default=None)) -> dict:
    """Validate the token before any database work, so anonymous requests are rejected cheaply."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
    try:
        return security.decode_token(token)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired token", headers={"WWW-Authenticate": "Bearer"})


def current_user(claims: dict = Depends(bearer_claims), conn=Depends(get_conn)) -> Principal:
    # Role and org are re-read from the database, so a deactivated or demoted user loses access at once.
    row = conn.execute("SELECT id, email, role, org_id, is_active, password_hash FROM users WHERE id=%s",
                       (int(claims["sub"]),)).fetchone()
    if row is None or not row[4]:
        raise HTTPException(401, "Account not found or disabled")
    if claims.get("pv") != security.password_fingerprint(row[5]):
        raise HTTPException(401, "Your password changed; please sign in again")
    return Principal(id=row[0], email=row[1], role=row[2], org_id=row[3])


def require_roles(*roles: str):
    def dependency(user: Principal = Depends(current_user)) -> Principal:
        if user.role not in roles:
            raise HTTPException(403, f"This action requires one of the roles: {', '.join(roles)}")
        return user
    return dependency


def org_scope(user: Principal, org_id: int | None = None) -> int:
    """The organization whose data this request may touch. Organization users are locked to their own;
    a platform_admin may name one explicitly. Everyone else is refused."""
    if user.org_id is not None:
        return user.org_id
    if user.role == "platform_admin" and org_id is not None:
        return org_id
    raise HTTPException(403, "This endpoint works on one organization's data; platform users must pass org_id (platform_admin only)")

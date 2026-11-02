"""Organizations and users (business logic; the HTTP layer is in backend/api/routes_auth.py)."""
from __future__ import annotations

import re

import psycopg

from backend import security
from backend.audit import audit

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PLATFORM_ROLES = {"platform_admin", "reviewer"}
ORG_ROLES = {"org_admin", "analyst", "assessor", "viewer"}


def create_organization(conn, name: str, actor: str) -> int:
    name = name.strip()
    if not name:
        raise ValueError("organization name is required")
    try:
        with conn.transaction():
            org_id = conn.execute("INSERT INTO organizations (name) VALUES (%s) RETURNING id", (name,)).fetchone()[0]
            audit(conn, actor, "organization_created", "organization", org_id, {"name": name}, org_id=org_id)
    except psycopg.errors.UniqueViolation:
        raise ValueError("an organization with this name already exists")
    return org_id


def create_user(conn, email: str, password: str, role: str, org_id: int | None, actor: str) -> int:
    email = email.strip().lower()
    if not EMAIL.match(email):
        raise ValueError("invalid email address")
    security.validate_password(password)
    if role in PLATFORM_ROLES and org_id is not None:
        raise ValueError("platform roles do not belong to an organization")
    if role in ORG_ROLES and org_id is None:
        raise ValueError("organization roles need an organization")
    if role not in PLATFORM_ROLES | ORG_ROLES:
        raise ValueError("unknown role")
    try:
        with conn.transaction():
            if org_id is not None and conn.execute("SELECT 1 FROM organizations WHERE id=%s", (org_id,)).fetchone() is None:
                raise ValueError("organization not found")
            user_id = conn.execute(
                "INSERT INTO users (org_id, email, password_hash, role) VALUES (%s,%s,%s,%s) RETURNING id",
                (org_id, email, security.hash_password(password), role)).fetchone()[0]
            audit(conn, actor, "user_created", "user", user_id, {"email": email, "role": role}, org_id=org_id)
    except psycopg.errors.UniqueViolation:
        raise ValueError("this email is already registered")
    return user_id


def authenticate(conn, email: str, password: str):
    """Return (id, role, org_id, email) for valid credentials, else None. Time is similar either way."""
    row = conn.execute("SELECT id, role, org_id, email, password_hash, is_active FROM users WHERE email=%s",
                       (email.strip().lower(),)).fetchone()
    ok = security.verify_password(password, row[4] if row else security.DUMMY_HASH)
    if row and ok and row[5]:
        return row[0], row[1], row[2], row[3]
    return None


def set_active(conn, user_id: int, active: bool, actor: str) -> None:
    with conn.transaction():
        row = conn.execute("UPDATE users SET is_active=%s WHERE id=%s RETURNING org_id", (active, user_id)).fetchone()
        if row is None:
            raise KeyError("user not found")
        audit(conn, actor, "user_activated" if active else "user_deactivated", "user", user_id, org_id=row[0])


def create_first_admin(conn, email: str, password: str) -> int:
    """First-run setup: creates the first platform_admin, and only while no user exists at all.
    An advisory lock makes two simultaneous setup requests safe: exactly one wins."""
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(724001)")
        if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            raise PermissionError("setup is already complete")
    return create_user(conn, email, password, "platform_admin", None, "first-run setup")


def setup_needed(conn) -> bool:
    return conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is None


def fingerprint(conn, user_id: int) -> str:
    return security.password_fingerprint(conn.execute("SELECT password_hash FROM users WHERE id=%s", (user_id,)).fetchone()[0])


def change_password(conn, user_id: int, current: str, new: str, actor: str) -> None:
    row = conn.execute("SELECT password_hash, org_id FROM users WHERE id=%s", (user_id,)).fetchone()
    if row is None or not security.verify_password(current, row[0]):
        raise PermissionError("the current password is not correct")
    security.validate_password(new)
    if security.verify_password(new, row[0]):
        raise ValueError("the new password must be different from the current one")
    with conn.transaction():
        conn.execute("UPDATE users SET password_hash=%s WHERE id=%s", (security.hash_password(new), user_id))
        audit(conn, actor, "password_changed", "user", user_id, org_id=row[1])


def reset_password(conn, user_id: int, new: str, actor: str) -> None:
    """Set a new password for someone else (an administrator action)."""
    security.validate_password(new)
    with conn.transaction():
        row = conn.execute("UPDATE users SET password_hash=%s WHERE id=%s RETURNING org_id",
                           (security.hash_password(new), user_id)).fetchone()
        if row is None:
            raise KeyError("user not found")
        audit(conn, actor, "password_reset", "user", user_id, org_id=row[0])

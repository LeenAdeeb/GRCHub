"""Password hashing (scrypt), JWT tokens and login throttling. No secrets are ever hard-coded:
the JWT secret must be supplied through GRCHUB_JWT_SECRET (at least 32 characters)."""
from __future__ import annotations

import hashlib
import hmac
import os
import time

import jwt

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
MIN_PASSWORD_LENGTH = 12
TOKEN_TTL_SECONDS = 8 * 3600
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 15 * 60

_failures: dict[str, list[float]] = {}


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(candidate, bytes.fromhex(digest))
    except (ValueError, TypeError):
        return False


# Verified when the email is unknown, so response time does not reveal which emails exist.
DUMMY_HASH = hash_password("not-a-real-account-password")


def _secret() -> str:
    secret = os.environ.get("GRCHUB_JWT_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError("GRCHUB_JWT_SECRET must be set to a random string of at least 32 characters")
    return secret


def password_fingerprint(password_hash: str) -> str:
    """Short digest of the stored hash. It is put in the token, so changing a password invalidates old tokens."""
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


def create_token(user_id: int, role: str, org_id: int | None, pv: str | None = None) -> str:
    now = int(time.time())
    return jwt.encode({"sub": str(user_id), "role": role, "org": org_id, "pv": pv, "iat": now,
                       "exp": now + TOKEN_TTL_SECONDS}, _secret(), algorithm="HS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, _secret(), algorithms=["HS256"], options={"require": ["exp", "sub"]})


def is_locked_out(email: str) -> bool:
    now = time.time()
    recent = [t for t in _failures.get(email.lower(), []) if now - t < LOCKOUT_SECONDS]
    _failures[email.lower()] = recent
    return len(recent) >= MAX_FAILED_LOGINS


def record_failure(email: str) -> None:
    _failures.setdefault(email.lower(), []).append(time.time())


def clear_failures(email: str) -> None:
    _failures.pop(email.lower(), None)

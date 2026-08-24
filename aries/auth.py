"""Family authentication: password hashing and browser sessions.

Deliberately lightweight and dependency-free (uses the standard library's
``hashlib.scrypt``). Passwords are salted and hashed; sessions are opaque random
tokens stored server-side and referenced by an HTTP-only cookie. Suitable for a
single-household local tool. When ``ARIES_REQUIRE_AUTH`` is false, the whole
layer is bypassed and the default Principal is used (handy for a quick local
start or automated tests).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import Cookie, HTTPException

from . import repository as repo
from .config import settings

COOKIE_NAME = "aries_session"

# scrypt parameters — a reasonable interactive-login cost.
_N, _R, _P, _DKLEN = 2 ** 14, 8, 1, 32


def hash_password(password: str) -> tuple[str, str]:
    """Return (hash_hex, salt_hex) for a new/updated password."""
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return dk.hex(), salt.hex()


def verify_password(password: str, hash_hex: str, salt_hex: str) -> bool:
    if not hash_hex or not salt_hex:
        return False
    salt = bytes.fromhex(salt_hex)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return hmac.compare_digest(dk.hex(), hash_hex)


def start_session(user_id: int) -> str:
    """Create a session and return its token."""
    repo.prune_sessions()
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=settings.session_ttl_hours)
    repo.create_session(token, user_id, expires.strftime("%Y-%m-%dT%H:%M:%SZ"))
    return token


def end_session(token: str | None) -> None:
    if token:
        repo.delete_session(token)


def user_for_token(token: str | None) -> Optional[dict[str, Any]]:
    if not token:
        return None
    return repo.get_session_user(token)


# --------------------------------------------------------------------------- #
# FastAPI dependency
# --------------------------------------------------------------------------- #
def current_user(aries_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    """Resolve the acting user. Enforces login when auth is required."""
    if not settings.require_auth:
        return repo.ensure_default_user()
    user = user_for_token(aries_session)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return user


def optional_user(aries_session: str | None = Cookie(default=None)) -> Optional[dict[str, Any]]:
    """Like ``current_user`` but never raises — used by public/meta endpoints."""
    if not settings.require_auth:
        return repo.ensure_default_user()
    return user_for_token(aries_session)

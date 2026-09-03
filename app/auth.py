from __future__ import annotations

import hmac
import time
import uuid
from datetime import datetime, timedelta, timezone
from threading import Lock

import bcrypt
import jwt

from .config import settings

JWT_ALGORITHM = "HS256"
JWT_ISSUER = "pactsignal"
JWT_AUDIENCE = "pactsignal-operator"
SESSION_COOKIE = "pactsignal_session"

_attempt_lock = Lock()
_failed_attempts: dict[str, list[float]] = {}


def validate_auth_config() -> None:
    if not settings.pactsignal_auth_enabled:
        return
    if not settings.pactsignal_admin_username.strip():
        raise RuntimeError("PACTSIGNAL_ADMIN_USERNAME is required when authentication is enabled")
    if not settings.pactsignal_admin_password_hash.startswith(("$2a$", "$2b$", "$2y$")):
        raise RuntimeError("PACTSIGNAL_ADMIN_PASSWORD_HASH must be a bcrypt hash")
    if len(settings.pactsignal_jwt_secret) < 32:
        raise RuntimeError("PACTSIGNAL_JWT_SECRET must contain at least 32 characters")
    if settings.pactsignal_jwt_ttl_minutes < 5:
        raise RuntimeError("PACTSIGNAL_JWT_TTL_MINUTES must be at least 5")


def login_rate_limited(key: str, *, now: float | None = None) -> bool:
    current = now if now is not None else time.time()
    cutoff = current - settings.pactsignal_login_window_seconds
    with _attempt_lock:
        recent = [ts for ts in _failed_attempts.get(key, []) if ts >= cutoff]
        _failed_attempts[key] = recent
        return len(recent) >= settings.pactsignal_login_max_failures


def record_login_failure(key: str, *, now: float | None = None) -> None:
    current = now if now is not None else time.time()
    cutoff = current - settings.pactsignal_login_window_seconds
    with _attempt_lock:
        recent = [ts for ts in _failed_attempts.get(key, []) if ts >= cutoff]
        recent.append(current)
        _failed_attempts[key] = recent


def clear_login_failures(key: str) -> None:
    with _attempt_lock:
        _failed_attempts.pop(key, None)


def verify_credentials(username: str, password: str) -> bool:
    expected_username = settings.pactsignal_admin_username.strip()
    username_ok = hmac.compare_digest(username.strip(), expected_username)

    try:
        password_ok = bcrypt.checkpw(
            password.encode("utf-8"),
            settings.pactsignal_admin_password_hash.encode("utf-8"),
        )
    except ValueError:
        password_ok = False

    return username_ok and password_ok


def issue_session_token(username: str, *, now: datetime | None = None) -> str:
    issued_at = now or datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(minutes=settings.pactsignal_jwt_ttl_minutes)
    payload = {
        "sub": username,
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.pactsignal_jwt_secret, algorithm=JWT_ALGORITHM)


def decode_session_token(token: str) -> dict:
    return jwt.decode(
        token,
        settings.pactsignal_jwt_secret,
        algorithms=[JWT_ALGORITHM],
        issuer=JWT_ISSUER,
        audience=JWT_AUDIENCE,
        options={"require": ["sub", "iss", "aud", "iat", "exp", "jti"]},
    )

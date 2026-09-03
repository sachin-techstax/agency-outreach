from __future__ import annotations

import hmac

from .config import settings


def validate_auth_config() -> None:
    if not settings.pactsignal_auth_enabled:
        return
    if len(settings.pactsignal_api_token) < 32:
        raise RuntimeError("PACTSIGNAL_API_TOKEN must contain at least 32 characters")


def bearer_token_from_header(authorization: str | None) -> str:
    if not authorization:
        return ""
    scheme, sep, value = authorization.partition(" ")
    if not sep or scheme.lower() != "bearer":
        return ""
    return value.strip()


def valid_api_token(authorization: str | None) -> bool:
    provided = bearer_token_from_header(authorization)
    expected = settings.pactsignal_api_token
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided, expected)

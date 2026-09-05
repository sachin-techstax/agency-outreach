from __future__ import annotations

import hmac
from functools import lru_cache
from typing import Any

import jwt
from jwt import PyJWKClient

from .config import settings


def cloudflare_access_configured() -> bool:
    return bool(
        settings.nuntago_access_team_domain
        and settings.nuntago_access_aud
    )


def validate_auth_config() -> None:
    if not settings.nuntago_auth_enabled:
        return

    has_token = len(settings.nuntago_api_token) >= 32
    has_team_domain = bool(settings.nuntago_access_team_domain)
    has_aud = bool(settings.nuntago_access_aud)

    if has_team_domain != has_aud:
        raise RuntimeError(
            "NUNTAGO_ACCESS_TEAM_DOMAIN and NUNTAGO_ACCESS_AUD must be configured together"
        )

    if not has_token and not cloudflare_access_configured():
        raise RuntimeError(
            "Nuntago operator auth requires either a 32+ character NUNTAGO_API_TOKEN "
            "or Cloudflare Access configuration"
        )


def bearer_token_from_header(authorization: str | None) -> str:
    if not authorization:
        return ""
    scheme, sep, value = authorization.partition(" ")
    if not sep or scheme.lower() != "bearer":
        return ""
    return value.strip()


def valid_api_token(authorization: str | None) -> bool:
    provided = bearer_token_from_header(authorization)
    expected = settings.nuntago_api_token
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided, expected)


@lru_cache(maxsize=8)
def _jwk_client(certs_url: str) -> PyJWKClient:
    return PyJWKClient(certs_url)


def _verified_cloudflare_claims(assertion: str | None) -> dict[str, Any] | None:
    if not assertion or not cloudflare_access_configured():
        return None

    team_domain = settings.nuntago_access_team_domain
    certs_url = f"{team_domain}/cdn-cgi/access/certs"

    try:
        signing_key = _jwk_client(certs_url).get_signing_key_from_jwt(assertion)
        claims = jwt.decode(
            assertion,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.nuntago_access_aud,
            issuer=team_domain,
            leeway=30,
        )
    except Exception:
        # Authentication failures must fail closed. The API middleware returns
        # a generic 401 and never exposes token/parser/network details.
        return None

    expected_email = settings.nuntago_operator_email
    if expected_email:
        actual_email = str(claims.get("email") or "").strip().lower()
        if not actual_email or not hmac.compare_digest(actual_email, expected_email):
            return None

    return claims


def valid_cloudflare_access(assertion: str | None) -> bool:
    return _verified_cloudflare_claims(assertion) is not None


def valid_operator_request(
    authorization: str | None,
    access_assertion: str | None,
) -> bool:
    # Bearer auth remains a supported non-browser fallback for CLI/local
    # tooling. The production browser console uses Cloudflare Access.
    return valid_api_token(authorization) or valid_cloudflare_access(access_assertion)

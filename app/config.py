from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    serper_api_key: str = os.getenv("SERPER_API_KEY", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
    your_name: str = os.getenv("YOUR_NAME", "Sachin Rajan")
    portfolio_url: str = os.getenv("PORTFOLIO_URL", "")
    calendly_url: str = os.getenv("CALENDLY_URL", "")
    min_score: int = _int("MIN_SCORE", 70)
    discovery_limit: int = _int("DISCOVERY_LIMIT", 15)
    followup_days: int = _int("FOLLOWUP_DAYS", 4)
    db_path: Path = Path(os.getenv("DB_PATH", "agency_outreach.db"))
    gmail_client_secret: Path = Path(os.getenv("GMAIL_CLIENT_SECRET", "client_secret.json"))
    gmail_token_file: Path = Path(os.getenv("GMAIL_TOKEN_FILE", "token.json"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"
    log_file: str = os.getenv("LOG_FILE", "").strip()
    pactsignal_demo_mode: bool = _bool("PACTSIGNAL_DEMO_MODE", False)
    pactsignal_auth_enabled: bool = _bool("PACTSIGNAL_AUTH_ENABLED", False)
    pactsignal_admin_username: str = os.getenv("PACTSIGNAL_ADMIN_USERNAME", "sachin").strip()
    pactsignal_admin_password_hash: str = os.getenv("PACTSIGNAL_ADMIN_PASSWORD_HASH", "").strip()
    pactsignal_jwt_secret: str = os.getenv("PACTSIGNAL_JWT_SECRET", "").strip()
    pactsignal_jwt_ttl_minutes: int = _int("PACTSIGNAL_JWT_TTL_MINUTES", 480)
    pactsignal_cookie_secure: bool = _bool("PACTSIGNAL_COOKIE_SECURE", True)
    pactsignal_login_max_failures: int = _int("PACTSIGNAL_LOGIN_MAX_FAILURES", 5)
    pactsignal_login_window_seconds: int = _int("PACTSIGNAL_LOGIN_WINDOW_SECONDS", 300)


settings = Settings()

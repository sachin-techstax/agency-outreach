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


settings = Settings()

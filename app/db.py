from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterable

from .config import settings
from .logging_config import get_logger

logger = get_logger("db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    domain TEXT NOT NULL UNIQUE,
    website TEXT NOT NULL,
    source_query TEXT,
    source_url TEXT,
    summary TEXT,
    services TEXT,
    team_hint TEXT,
    score INTEGER DEFAULT 0,
    score_reasons TEXT,
    fit_reason TEXT,
    proof_project TEXT,
    outreach_angle TEXT,
    contact_name TEXT,
    contact_role TEXT,
    contact_email TEXT,
    contact_source TEXT,
    subject TEXT,
    draft TEXT,
    status TEXT NOT NULL DEFAULT 'discovered',
    gmail_draft_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_contact_at TEXT,
    followup_due_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_leads_status_score ON leads(status, score DESC);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def conn():
    db = sqlite3.connect(settings.db_path)
    db.row_factory = sqlite3.Row
    try:
        yield db
        db.commit()
    finally:
        db.close()


def init_db() -> None:
    logger.debug("Initializing database at %s", settings.db_path)
    with conn() as db:
        db.executescript(SCHEMA)


def upsert_lead(data: dict) -> int:
    init_db()
    stamp = now_iso()
    with conn() as db:
        existing = db.execute("SELECT id FROM leads WHERE domain=?", (data["domain"],)).fetchone()
        if existing:
            allowed = [k for k in data if k not in {"id", "created_at"}]
            sets = ", ".join(f"{k}=?" for k in allowed)
            values = [data[k] for k in allowed] + [stamp, existing["id"]]
            db.execute(f"UPDATE leads SET {sets}, updated_at=? WHERE id=?", values)
            logger.debug("Updated lead id=%s domain=%s", existing["id"], data["domain"])
            return int(existing["id"])
        cols = list(data.keys()) + ["created_at", "updated_at"]
        vals = [data[c] for c in data] + [stamp, stamp]
        q = ",".join("?" for _ in cols)
        cur = db.execute(f"INSERT INTO leads ({','.join(cols)}) VALUES ({q})", vals)
        lead_id = int(cur.lastrowid)
        logger.debug("Inserted lead id=%s domain=%s", lead_id, data["domain"])
        return lead_id


def update_lead(lead_id: int, **updates) -> None:
    if not updates:
        return
    updates["updated_at"] = now_iso()
    cols = list(updates)
    values = [updates[c] for c in cols] + [lead_id]
    with conn() as db:
        db.execute(f"UPDATE leads SET {', '.join(f'{c}=?' for c in cols)} WHERE id=?", values)
    logger.debug("Updated lead id=%s %s", lead_id, ", ".join(f"{c}={updates[c]}" for c in cols if c != "updated_at"))


def get_lead(lead_id: int):
    with conn() as db:
        return db.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()


def list_leads(status: str | None = None, min_score: int = 0, limit: int = 100):
    sql = "SELECT * FROM leads WHERE score>=?"
    args: list[object] = [min_score]
    if status:
        sql += " AND status=?"
        args.append(status)
    sql += " ORDER BY score DESC, updated_at DESC LIMIT ?"
    args.append(limit)
    with conn() as db:
        return db.execute(sql, args).fetchall()


def due_followups(now: str):
    with conn() as db:
        return db.execute(
            """SELECT * FROM leads
               WHERE status='sent' AND followup_due_at IS NOT NULL AND followup_due_at<=?
               ORDER BY followup_due_at ASC""",
            (now,),
        ).fetchall()

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterable

from .config import settings
from .logging_config import get_logger

logger = get_logger("db")

# ---------------------------------------------------------------------------
# Workflow state protection
# ---------------------------------------------------------------------------
# Once a lead reaches a human-confirmed or sent state, automated discovery
# runs must NOT overwrite its workflow status, outreach draft, Gmail draft ID,
# or follow-up/contact history.  These represent human decisions that the
# pipeline is not allowed to undo.
PROTECTED_STATUSES = frozenset({
    "approved",
    "gmail_drafted",
    "sent",
})

# Fields that must never be overwritten by an automated upsert when the
# existing lead is in a protected status.  This includes both workflow state
# and the contact information used by the human workflow.
_PROTECTED_FIELDS = frozenset({
    "status",
    "subject",
    "draft",
    "gmail_draft_id",
    "last_contact_at",
    "followup_due_at",
    "contact_email",
    "contact_source",
    "contact_name",
    "contact_role",
    "contact_quality",
})

# Fields that represent generated/contact state and should be cleared when a
# non-protected lead is downgraded to rejected-fit (the generated content no
# longer applies).
_STALE_GENERATED_FIELDS = frozenset({
    "subject",
    "draft",
    "gmail_draft_id",
    "contact_email",
    "contact_source",
    "contact_name",
    "contact_role",
    "contact_quality",
})


def is_workflow_state_protected(status: str | None) -> bool:
    """Return True if *status* is a protected workflow state.

    Protected states (approved, gmail_drafted, sent) represent human decisions
    that automated discovery must not overwrite.
    """
    return status in PROTECTED_STATUSES

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
    contact_quality TEXT,
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

# Columns to add if they don't exist (backward-compatible migration).
_MIGRATION_COLUMNS = [
    ("contact_quality", "TEXT"),
]


def _migrate(db: sqlite3.Connection) -> None:
    """Add columns that may be missing in older database files."""
    cols = {row["name"] for row in db.execute("PRAGMA table_info(leads)").fetchall()}
    for col_name, col_type in _MIGRATION_COLUMNS:
        if col_name not in cols:
            db.execute(f"ALTER TABLE leads ADD COLUMN {col_name} {col_type}")
            logger.debug("Added column %s to leads table", col_name)


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
        _migrate(db)


def upsert_lead(data: dict) -> int:
    """Insert or update a lead, respecting workflow-state protection.

    If an existing lead is in a protected status (approved, gmail_drafted,
    sent), the following fields are never overwritten:
    - status, subject, draft, gmail_draft_id, last_contact_at, followup_due_at

    Research metadata (score, score_reasons, summary, etc.) may still be
    refreshed so that human reviewers see the latest qualification data.

    If a non-protected lead is being downgraded to ``rejected-fit``, stale
    generated/contact state (subject, draft, gmail_draft_id, contact_*) is
    cleared so the row does not retain an old outreach draft from a prior
    qualification.
    """
    init_db()
    stamp = now_iso()
    with conn() as db:
        existing = db.execute(
            "SELECT * FROM leads WHERE domain=?", (data["domain"],)
        ).fetchone()
        if existing:
            existing_status = existing["status"]
            new_status = data.get("status", existing_status)

            if is_workflow_state_protected(existing_status):
                # Remove protected fields from the update payload so they
                # are never overwritten by automated discovery.
                data = {
                    k: v for k, v in data.items()
                    if k not in _PROTECTED_FIELDS
                }
                logger.debug(
                    "Preserving protected workflow state for lead id=%s "
                    "domain=%s status=%s",
                    existing["id"], data["domain"], existing_status,
                )
            elif new_status == "rejected-fit":
                # Non-protected lead being downgraded: clear stale generated
                # state so the row doesn't retain an old draft/Gmail ID.
                for field in _STALE_GENERATED_FIELDS:
                    if field not in data:
                        data[field] = ""
                    # If the field IS in data (e.g. contact_email from
                    # discover_contact), it will be set to whatever value was
                    # passed.  For rejected-fit, the pipeline passes empty
                    # strings for all contact fields, so this is consistent.

            allowed = [k for k in data if k not in {"id", "created_at"}]
            sets = ", ".join(f"{k}=?" for k in allowed)
            values = [data[k] for k in allowed] + [stamp, existing["id"]]
            db.execute(f"UPDATE leads SET {sets}, updated_at=? WHERE id=?", values)
            logger.debug(
                "Updated lead id=%s domain=%s fields=%s",
                existing["id"],
                data["domain"],
                ",".join(allowed),
            )
            return int(existing["id"])
        cols = list(data.keys()) + ["created_at", "updated_at"]
        vals = [data[c] for c in data] + [stamp, stamp]
        q = ",".join("?" for _ in cols)
        cur = db.execute(f"INSERT INTO leads ({','.join(cols)}) VALUES ({q})", vals)
        lead_id = int(cur.lastrowid)
        logger.debug(
            "Inserted lead id=%s domain=%s fields=%s",
            lead_id,
            data["domain"],
            ",".join(data.keys()),
        )
        return lead_id


def update_lead(lead_id: int, **updates) -> None:
    """Update specific fields on a lead.

    This is the explicit workflow mutation primitive, used by CLI commands
    (approve, reject, gmail-drafts, mark-sent, etc.).  It does NOT enforce
    workflow-state protection — that is the responsibility of the automated
    discovery path (``upsert_lead``).

    Explicit workflow transitions such as ``drafted -> approved``,
    ``approved -> gmail_drafted``, and ``gmail_drafted -> sent`` must work
    without restriction here.
    """
    if not updates:
        return
    init_db()
    updates["updated_at"] = now_iso()
    cols = list(updates)
    values = [updates[c] for c in cols] + [lead_id]
    with conn() as db:
        db.execute(f"UPDATE leads SET {', '.join(f'{c}=?' for c in cols)} WHERE id=?", values)
    # Log only field names and status (when applicable). Never stringify
    # arbitrary values such as draft bodies, email content, or tokens.
    changed = [c for c in cols if c != "updated_at"]
    status = updates.get("status")
    if status is not None:
        logger.debug(
            "Updated lead id=%s fields=%s status=%s",
            lead_id,
            ",".join(changed),
            status,
        )
    else:
        logger.debug(
            "Updated lead id=%s fields=%s",
            lead_id,
            ",".join(changed),
        )


def get_lead(lead_id: int):
    with conn() as db:
        return db.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()


def get_lead_by_domain(domain: str):
    """Return the existing lead row for *domain*, or None."""
    init_db()
    with conn() as db:
        return db.execute("SELECT * FROM leads WHERE domain=?", (domain,)).fetchone()


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

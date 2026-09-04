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

# ---------------------------------------------------------------------------
# Discovery suppression policy
# ---------------------------------------------------------------------------
# Statuses that cause a domain to be SUPPRESSED from normal discovery runs
# (before crawl).  These leads are already in an outreach workflow and should
# not consume fresh attempt slots.
#
# Suppressed statuses:
#   drafted        - already has outreach prepared; do not regenerate
#   approved       - human approved; do not rediscover/regenerate
#   gmail_drafted  - Gmail draft exists; do not rediscover/regenerate
#   sent           - already contacted; never consume a fresh attempt slot
#   do_not_contact - permanently suppress from normal outreach discovery
#
# NOT suppressed (retryable):
#   rejected-fit   - website/services may change; may become viable later
#   rejected       - manually rejected but may be reconsidered
#   discovered     - non-terminal research state; retryable
#   qualified      - retryable unless there is already a usable draft (which
#                    would mean status=drafted, which IS suppressed)
#
# Note: workflow mutation protection (PROTECTED_STATUSES) and discovery
# suppression (SUPPRESSED_STATUSES) are intentionally separate concepts.
# `drafted` is suppressed from discovery but NOT protected from repository
# mutation, because explicit requalification workflows may need to update it.
SUPPRESSED_STATUSES = frozenset({
    "drafted",
    "approved",
    "gmail_drafted",
    "sent",
    "do_not_contact",
})

# Reverse mapping for allow-contact: which status to restore to when clearing
# a do_not_contact flag.  We restore to "rejected" (a retryable status) so the
# domain can be rediscovered on a future normal run.
_DNC_RESTORE_STATUS = "rejected"

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


def is_suppressed_status(status: str | None) -> bool:
    """Return True if an existing lead with *status* should be suppressed
    from normal discovery runs (before crawl).

    Suppressed statuses are listed in :data:`SUPPRESSED_STATUSES`.  Leads in
    retryable statuses (rejected-fit, rejected, discovered, qualified) are
    NOT suppressed and may be rediscovered.
    """
    return status in SUPPRESSED_STATUSES

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
    draft_stale INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_contact_at TEXT,
    followup_due_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_leads_status_score ON leads(status, score DESC);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_limit INTEGER,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    query_count INTEGER,
    raw_candidate_domains INTEGER,
    candidate_domains INTEGER,
    fresh_retryable_pool INTEGER,
    attempted INTEGER,
    processed INTEGER,
    qualified INTEGER,
    drafted INTEGER,
    below_score INTEGER,
    no_contact INTEGER,
    skipped INTEGER,
    failed_count INTEGER,
    duration_s REAL,
    error_summary TEXT,
    progress TEXT,
    result_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_started_at ON runs(started_at DESC);
"""

# Columns to add if they don't exist (backward-compatible migration).
_MIGRATION_COLUMNS = [
    ("contact_quality", "TEXT"),
    ("draft_stale", "INTEGER DEFAULT 0"),
]

# Columns to add to the ``runs`` table if missing in older database files.
_RUN_MIGRATION_COLUMNS = [
    ("result_json", "TEXT"),
]


def _migrate(db: sqlite3.Connection) -> None:
    """Add columns that may be missing in older database files.

    When the ``draft_stale`` column is first added to an existing production
    database, existing drafts that predate freshness tracking cannot be
    trusted as fresh.  The migration marks existing rows stale where:
      - ``draft`` is non-empty
      - ``status`` is one of ``drafted``, ``rejected``, ``approved``
    (i.e. statuses where regeneration is a meaningful operator action).
    Rows in ``gmail_drafted``, ``sent``, or ``do_not_contact`` are NOT
    marked stale by migration — those represent workflow states where
    regeneration is not the expected next step.
    """
    cols = {row["name"] for row in db.execute("PRAGMA table_info(leads)").fetchall()}
    for col_name, col_type in _MIGRATION_COLUMNS:
        if col_name not in cols:
            db.execute(f"ALTER TABLE leads ADD COLUMN {col_name} {col_type}")
            logger.debug("Added column %s to leads table", col_name)
            # R1-1: When draft_stale is first added, mark existing
            # regeneratable drafts stale.  Existing production drafts
            # predate freshness tracking and cannot be trusted as fresh.
            if col_name == "draft_stale":
                db.execute(
                    "UPDATE leads SET draft_stale = 1 "
                    "WHERE COALESCE(draft, '') <> '' "
                    "AND status IN ('drafted', 'rejected', 'approved')"
                )
                logger.info(
                    "Migration: marked existing regeneratable drafts stale "
                    "(draft_stale=1 for drafted/rejected/approved with non-empty draft)"
                )
    run_cols = {row["name"] for row in db.execute("PRAGMA table_info(runs)").fetchall()}
    for col_name, col_type in _RUN_MIGRATION_COLUMNS:
        if col_name not in run_cols:
            db.execute(f"ALTER TABLE runs ADD COLUMN {col_name} {col_type}")
            logger.debug("Added column %s to runs table", col_name)


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


def replace_stale_draft_if_current(
    lead_id: int,
    *,
    expected_status: str,
    expected_draft: str,
    expected_company: str,
    expected_fit_reason: str,
    expected_proof_project: str,
    expected_outreach_angle: str,
    subject: str,
    draft: str,
) -> bool:
    """R2-1: Optimistic-concurrency conditional update for draft regeneration.

    Atomically replaces the stale draft ONLY if the lead's relevant state
    still matches the snapshot taken before the (slow) ``draft_outreach()``
    call.  This prevents TOCTOU races where a human workflow action or a
    research refresh modifies the lead during the LLM call.

    The UPDATE succeeds only when ALL of the following still hold:
      - ``id`` matches
      - ``status`` matches (drafted/rejected/approved — not mutated to
        do_not_contact/gmail_drafted/sent by a concurrent action)
      - ``draft_stale`` is still 1 (not cleared by a concurrent regeneration)
      - ``gmail_draft_id`` is still empty (no concurrent Gmail draft)
      - ``draft`` body is unchanged (no concurrent regeneration wrote a new one)
      - ``company``, ``fit_reason``, ``proof_project``, ``outreach_angle``
        are unchanged (no concurrent research refresh changed draft-driving
        fields)

    Contact fields, score, summary, and services are intentionally NOT
    checked because they do not affect the generated draft.

    Returns ``True`` if exactly one row was updated, ``False`` on conflict.
    """
    init_db()
    timestamp = now_iso()
    with conn() as db:
        cur = db.execute(
            """
            UPDATE leads
            SET subject = ?,
                draft = ?,
                draft_stale = 0,
                status = 'drafted',
                updated_at = ?
            WHERE id = ?
              AND status = ?
              AND draft_stale = 1
              AND COALESCE(gmail_draft_id, '') = ''
              AND COALESCE(draft, '') = ?
              AND COALESCE(company, '') = ?
              AND COALESCE(fit_reason, '') = ?
              AND COALESCE(proof_project, '') = ?
              AND COALESCE(outreach_angle, '') = ?
            """,
            (
                subject,
                draft,
                timestamp,
                lead_id,
                expected_status,
                expected_draft,
                expected_company,
                expected_fit_reason,
                expected_proof_project,
                expected_outreach_angle,
            ),
        )
        updated = cur.rowcount == 1
    if updated:
        logger.debug(
            "Optimistic draft replacement succeeded for lead id=%s", lead_id
        )
    else:
        logger.info(
            "Optimistic draft replacement conflicted for lead id=%s "
            "(lead changed during regeneration)",
            lead_id,
        )
    return updated


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


# ---------------------------------------------------------------------------
# Bulk discovery suppression lookup
# ---------------------------------------------------------------------------

# SQLite default limit for the number of host parameters in a single statement
# is 999 (or 32766 in newer versions).  Our discovery pools are small (<200),
# so a single IN-clause query is safe.  We chunk defensively anyway in case a
# future pool grows large.
_SQLITE_PARAM_LIMIT = 500


def get_lead_statuses_by_domains(domains: Iterable[str]) -> dict[str, str]:
    """Bulk-lookup the current status of existing leads for *domains*.

    Returns a dict mapping canonical domain -> status for every domain that
    has an existing lead row.  Domains with no existing lead are absent from
    the result.  Uses a single SQL query per chunk (``SELECT domain, status
    FROM leads WHERE domain IN (...)``) so suppression checks do not issue
    one query per candidate.

    Domains are matched exactly as stored (canonical, without ``www.``).
    Callers must normalize domains before passing them in (use
    :func:`app.candidate_filter.normalize_domain`).
    """
    domains = list(domains)
    if not domains:
        return {}
    init_db()
    result: dict[str, str] = {}
    with conn() as db:
        for i in range(0, len(domains), _SQLITE_PARAM_LIMIT):
            chunk = domains[i:i + _SQLITE_PARAM_LIMIT]
            placeholders = ",".join("?" for _ in chunk)
            rows = db.execute(
                f"SELECT domain, status FROM leads WHERE domain IN ({placeholders})",
                chunk,
            ).fetchall()
            for row in rows:
                result[row["domain"]] = row["status"]
    return result


def get_suppressed_domains(domains: Iterable[str]) -> dict[str, str]:
    """Return ``{domain: status}`` for domains whose existing lead status is
    a suppressed status (see :data:`SUPPRESSED_STATUSES`).

    This is the primary bulk lookup used by the pipeline to suppress
    already-handled leads before crawl.  Domains with no existing lead or
    with a retryable status are absent from the result.
    """
    statuses = get_lead_statuses_by_domains(domains)
    return {
        domain: status
        for domain, status in statuses.items()
        if is_suppressed_status(status)
    }


# ---------------------------------------------------------------------------
# Persistent run history
# ---------------------------------------------------------------------------

# Run lifecycle statuses.
RUN_QUEUED = "queued"
RUN_RUNNING = "running"
RUN_COMPLETED = "completed"
RUN_FAILED = "failed"

# Run types.  ``processing`` is the user-facing label for the outreach
# pipeline run; ``discovery`` is the read-only discovery ranking run.
RUN_TYPE_DISCOVERY = "discovery"
RUN_TYPE_PROCESSING = "processing"

# Columns that may be updated after creation.  ``type`` and ``started_at`` are
# immutable once a run row exists.
_RUN_UPDATE_COLUMNS = (
    "status", "completed_at", "query_count", "raw_candidate_domains",
    "candidate_domains", "fresh_retryable_pool", "attempted", "processed",
    "qualified", "drafted", "below_score", "no_contact", "skipped",
    "failed_count", "duration_s", "error_summary", "progress", "result_json",
)


def create_run(run_type: str, requested_limit: int | None = None) -> int:
    """Create a new run row in ``queued`` state and return its id."""
    init_db()
    stamp = now_iso()
    with conn() as db:
        cur = db.execute(
            "INSERT INTO runs (type, status, requested_limit, started_at) "
            "VALUES (?, ?, ?, ?)",
            (run_type, RUN_QUEUED, requested_limit, stamp),
        )
        return int(cur.lastrowid)


def update_run(run_id: int, **updates) -> None:
    """Update specific fields on a run row.

    Only fields in :data:`_RUN_UPDATE_COLUMNS` are written; unknown keys are
    ignored so callers can pass pipeline summary dicts without filtering.
    """
    if not updates:
        return
    init_db()
    allowed = {k: v for k, v in updates.items() if k in _RUN_UPDATE_COLUMNS}
    if not allowed:
        return
    cols = list(allowed)
    values = [allowed[c] for c in cols] + [run_id]
    with conn() as db:
        db.execute(
            f"UPDATE runs SET {', '.join(f'{c}=?' for c in cols)} WHERE id=?",
            values,
        )


def set_run_progress(run_id: int, progress: dict) -> None:
    """Persist a JSON progress snapshot for an active run."""
    import json as _json
    update_run(run_id, progress=_json.dumps(progress, separators=(",", ":")))


def get_run(run_id: int):
    """Return the run row (sqlite3.Row) or None."""
    init_db()
    with conn() as db:
        return db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()


def list_runs(limit: int = 50):
    """Return recent run rows ordered by most-recent first."""
    init_db()
    with conn() as db:
        return db.execute(
            "SELECT * FROM runs ORDER BY started_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()


def list_runs_by_type(run_type: str, limit: int = 50):
    """Return recent run rows of a specific type, most-recent first."""
    init_db()
    with conn() as db:
        return db.execute(
            "SELECT * FROM runs WHERE type=? ORDER BY started_at DESC, id DESC LIMIT ?",
            (run_type, limit),
        ).fetchall()


def list_runs_by_type_and_status(
    run_type: str, status: str, limit: int = 50
):
    """Return recent run rows of a specific type AND status, most-recent first.

    Used by the Discovery page to retrieve the most recent *completed*
    discovery run so a later failed attempt does not hide the last
    successful ranked result (R2-3).
    """
    init_db()
    with conn() as db:
        return db.execute(
            "SELECT * FROM runs WHERE type=? AND status=? "
            "ORDER BY started_at DESC, id DESC LIMIT ?",
            (run_type, status, limit),
        ).fetchall()


def list_runs_by_status(status: str, limit: int = 50):
    """Return recent run rows of a specific status, most-recent first.

    Used by ``GET /api/runs?status=failed`` (R3-2) so a status-only filter
    does not fall through to the unfiltered list.
    """
    init_db()
    with conn() as db:
        return db.execute(
            "SELECT * FROM runs WHERE status=? "
            "ORDER BY started_at DESC, id DESC LIMIT ?",
            (status, limit),
        ).fetchall()


def reconcile_abandoned_runs() -> int:
    """Mark any ``queued`` or ``running`` runs as ``failed``.

    Called on PactSignal startup.  Because background runs execute in daemon
    threads inside this process, any persisted ``queued`` or ``running`` row
    found during a fresh startup cannot still have a live worker from the
    previous process.  Marking them ``failed`` prevents stale runs from
    permanently disabling the operator UI.

    Returns the number of runs reconciled.  No secrets are stored in the
    error summary.
    """
    init_db()
    stamp = now_iso()
    reason = "Interrupted by PactSignal process restart"
    with conn() as db:
        cur = db.execute(
            "UPDATE runs SET status=?, completed_at=?, error_summary=? "
            "WHERE status IN (?, ?)",
            (RUN_FAILED, stamp, reason, RUN_QUEUED, RUN_RUNNING),
        )
        return cur.rowcount

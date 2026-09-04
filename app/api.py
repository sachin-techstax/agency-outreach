from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .auth import validate_auth_config, valid_api_token
from .config import settings
from .logging_config import get_logger
from .db import (
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_QUEUED,
    RUN_RUNNING,
    RUN_TYPE_DISCOVERY,
    RUN_TYPE_PROCESSING,
    SUPPRESSED_STATUSES,
    create_run,
    due_followups,
    get_lead,
    get_run,
    init_db,
    list_leads,
    list_runs,
    list_runs_by_type,
    now_iso,
    reconcile_abandoned_runs,
    set_run_progress,
    update_lead,
    update_run,
)
from .demo_data import DEMO_LEADS, DEMO_LATEST_RUN, demo_dashboard
from .gmail_client import create_draft
from .pipeline import discover_only, refresh_lead_research, run as run_pipeline

logger = get_logger("api")

app = FastAPI(
    title="PactSignal Operator API",
    description="Private operator API for partner intelligence and human-approved outreach.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Single global lock guarding any pipeline/discovery execution.  Held for the
# entire lifetime of a background run so concurrent run requests return 409.
_RUN_LOCK = threading.Lock()
_LATEST_RUN: dict[str, Any] | None = None

_PUBLIC_API_PATHS = {"/api/health"}


@app.on_event("startup")
def _startup_reconcile() -> None:
    """Validate auth config and reconcile abandoned runs from a previous
    process (R1-6).  Because background runs execute in daemon threads inside
    this process, any persisted ``queued`` or ``running`` row found during a
    fresh startup cannot still have a live worker and is marked ``failed``.
    """
    validate_auth_config()
    try:
        reconciled = reconcile_abandoned_runs()
        if reconciled:
            logger.info("Reconciled %d abandoned run(s) on startup", reconciled)
    except Exception as exc:
        logger.warning("Run reconciliation failed on startup: %s", exc)


@app.middleware("http")
async def _operator_auth(request: Request, call_next):
    if not settings.pactsignal_auth_enabled or request.method == "OPTIONS":
        return await call_next(request)

    path = request.url.path
    if not path.startswith("/api/") or path in _PUBLIC_API_PATHS:
        return await call_next(request)

    if not valid_api_token(request.headers.get("Authorization")):
        return JSONResponse(
            status_code=401,
            content={"detail": "Valid PactSignal bearer token required"},
            headers={
                "Cache-Control": "no-store",
                "WWW-Authenticate": "Bearer",
            },
        )

    return await call_next(request)

def _parse_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(v) for v in parsed if str(v).strip()]
        except json.JSONDecodeError:
            pass
        return [part.strip() for part in value.split(",") if part.strip()]
    return [str(value)]


def _serialize_lead(row: Any) -> dict:
    data = dict(row)
    data["services_list"] = _parse_list(data.get("services"))
    data["score_reason_list"] = _parse_list(data.get("score_reasons"))
    return data


def _demo_lead(lead_id: int) -> dict:
    for lead in DEMO_LEADS:
        if lead["id"] == lead_id:
            data = dict(lead)
            data["services_list"] = _parse_list(data.get("services"))
            data["score_reason_list"] = _parse_list(data.get("score_reasons"))
            return data
    raise HTTPException(status_code=404, detail="Lead not found")


def _live_lead(lead_id: int) -> dict:
    init_db()
    row = get_lead(lead_id)
    if not row:
        raise HTTPException(status_code=404, detail="Lead not found")
    return _serialize_lead(row)


def _require_live_mode() -> None:
    if settings.pactsignal_demo_mode:
        raise HTTPException(
            status_code=403,
            detail="PactSignal demo mode is read-only. External and persistent actions are disabled.",
        )


def _run_exclusive(fn):
    if not _RUN_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A PactSignal run is already in progress")
    try:
        return fn()
    finally:
        _RUN_LOCK.release()


def _sanitize_result_for_storage(run_type: str, result: dict) -> dict:
    """Build a sanitized result snapshot safe to persist in ``result_json``.

    Strips any field that could carry secrets (tracebacks, raw exception
    objects, credentials).  For discovery runs, preserves the ranked
    candidate list and pool metrics so the Discovery page can rebuild from
    the DB after a restart or after a later processing run overwrites
    ``_LATEST_RUN``.  For processing runs, preserves the summary metrics.
    """
    if run_type == RUN_TYPE_DISCOVERY:
        return {
            "type": run_type,
            "query_count": result.get("query_count"),
            "search_results_total": result.get("search_results_total"),
            "raw_candidate_domains": result.get("raw_candidate_domains"),
            "rejected_candidate_domains": result.get("rejected_candidate_domains"),
            "candidate_domains": result.get("candidate_domains"),
            "ranked_candidate_domains": result.get("ranked_candidate_domains"),
            "displayed_candidate_domains": result.get("displayed_candidate_domains"),
            "candidate_priority_avg": result.get("candidate_priority_avg"),
            "per_query": result.get("per_query"),
            "ranked": result.get("ranked", []),
        }
    # Processing run: keep summary metrics only (no secrets, no tracebacks).
    return {
        "type": run_type,
        "attempted": result.get("attempted"),
        "processed": result.get("processed"),
        "qualified": result.get("qualified"),
        "drafted": result.get("drafted"),
        "below_score": result.get("below_score"),
        "no_contact": result.get("no_contact"),
        "skipped": result.get("skipped"),
        "failed": result.get("failed"),
        "duration_s": result.get("duration_s"),
    }


def _serialize_run(row: Any) -> dict:
    data = dict(row)
    progress = data.get("progress")
    if progress:
        try:
            data["progress"] = json.loads(progress)
        except (json.JSONDecodeError, TypeError):
            data["progress"] = None
    else:
        data["progress"] = None
    result_json = data.get("result_json")
    if result_json:
        try:
            data["result"] = json.loads(result_json)
        except (json.JSONDecodeError, TypeError):
            data["result"] = None
    else:
        data["result"] = None
    return data


def _start_background_run(
    run_type: str,
    limit: int,
    executor: Callable[[int, Callable[[dict], None]], dict],
) -> dict:
    """Create a run row, claim the run lock, and execute in a background thread.

    The lock is claimed synchronously so a concurrent run returns 409 before
    any work starts.  The lock is held until the background worker finishes.

    R1-7: lock ownership is exception-safe.  If anything fails after the
    lock is acquired but before the background worker successfully owns
    execution (create_run, update_run, or thread.start), the lock is
    released and any already-created run row is marked failed.  Once the
    worker starts, the worker owns the lock and releases it in its
    ``finally``.

    ``executor`` receives ``(limit, progress_cb)`` and returns the pipeline
    summary dict.  The run row is transitioned queued -> running -> completed
    or failed, with progress snapshots persisted along the way.  A sanitized
    result snapshot is persisted in ``result_json`` so the Discovery page
    and Dashboard can rebuild from the DB after a restart or after a later
    run overwrites the in-memory ``_LATEST_RUN``.
    """
    if not _RUN_LOCK.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="A PactSignal run is already in progress",
        )
    run_id: int | None = None
    try:
        run_id = create_run(run_type, requested_limit=limit)
        update_run(run_id, status=RUN_RUNNING)
        global _LATEST_RUN

        def worker() -> None:
            global _LATEST_RUN
            try:
                def progress_cb(snapshot: dict) -> None:
                    set_run_progress(run_id, snapshot)

                result = executor(limit, progress_cb)
                sanitized = _sanitize_result_for_storage(run_type, result)
                summary_fields = {
                    "status": RUN_COMPLETED,
                    "completed_at": now_iso(),
                    "query_count": result.get("query_count"),
                    "raw_candidate_domains": result.get("raw_candidate_domains"),
                    "candidate_domains": result.get("candidate_domains"),
                    "fresh_retryable_pool": result.get("fresh_retryable_pool"),
                    "attempted": result.get("attempted"),
                    "processed": result.get("processed"),
                    "qualified": result.get("qualified"),
                    "drafted": result.get("drafted"),
                    "below_score": result.get("below_score"),
                    "no_contact": result.get("no_contact"),
                    "skipped": result.get("skipped"),
                    "failed_count": result.get("failed"),
                    "duration_s": result.get("duration_s"),
                    "error_summary": None,
                    "result_json": json.dumps(sanitized, separators=(",", ":")),
                }
                update_run(run_id, **summary_fields)
                _LATEST_RUN = {"type": run_type, **result}
            except Exception as exc:
                error_summary = f"{type(exc).__name__}: {exc}"[:500]
                update_run(
                    run_id,
                    status=RUN_FAILED,
                    completed_at=now_iso(),
                    error_summary=error_summary,
                )
                _LATEST_RUN = {
                    "type": run_type,
                    "error": error_summary,
                }
            finally:
                _RUN_LOCK.release()

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
    except Exception:
        # Pre-worker failure: release the lock and mark the run failed so a
        # later run is not permanently locked out (R1-7).
        _RUN_LOCK.release()
        if run_id is not None:
            try:
                update_run(
                    run_id,
                    status=RUN_FAILED,
                    completed_at=now_iso(),
                    error_summary="Run failed to start",
                )
            except Exception:
                pass
        raise
    return _serialize_run(get_run(run_id))


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "product": "PactSignal",
        "demo_mode": settings.pactsignal_demo_mode,
        "auth_enabled": settings.pactsignal_auth_enabled,
    }


@app.get("/api/meta")
def meta() -> dict:
    return {
        "product": "PactSignal",
        "descriptor": "Partner intelligence & outreach",
        "demo_mode": settings.pactsignal_demo_mode,
        "minimum_score": settings.min_score,
        "external_actions_enabled": not settings.pactsignal_demo_mode,
    }


@app.get("/api/dashboard")
def dashboard() -> dict:
    if settings.pactsignal_demo_mode:
        return demo_dashboard()

    init_db()
    rows = [_serialize_lead(r) for r in list_leads(status=None, min_score=0, limit=5000)]
    counts: dict[str, int] = {
        "total": len(rows),
        "drafted": 0,
        "approved": 0,
        "gmail_drafted": 0,
        "sent": 0,
        "do_not_contact": 0,
        "retryable": 0,
    }
    for row in rows:
        status = row["status"]
        if status in counts:
            counts[status] += 1
        if status not in SUPPRESSED_STATUSES:
            counts["retryable"] += 1

    queue_statuses = {"drafted", "approved", "qualified", "discovered"}
    review_queue = [row for row in rows if row["status"] in queue_statuses][:6]

    # Surface the most recent run row so the UI can show real persisted state
    # (status, started_at, type) instead of only the in-memory latest run.
    # R1-8: the persisted row is authoritative.  When ``_LATEST_RUN`` is empty
    # (e.g. after a process restart) but a completed run exists in the DB,
    # rebuild the latest-run payload from the persisted ``result_json`` so the
    # Dashboard still shows the most recent completed run metrics.
    recent_runs = list_runs(limit=1)
    latest_run_row = _serialize_run(recent_runs[0]) if recent_runs else None
    latest_run = _LATEST_RUN
    if latest_run is None and latest_run_row and latest_run_row.get("result"):
        latest_run = latest_run_row["result"]

    return {
        "mode": "private",
        "counts": counts,
        "due_followups": len(due_followups(now_iso())),
        "review_queue": review_queue,
        "latest_run": latest_run,
        "latest_run_row": latest_run_row,
    }


@app.get("/api/leads")
def leads(
    status: str | None = Query(default=None),
    min_score: int = Query(default=0, ge=0, le=100),
    limit: int = Query(default=100, ge=1, le=500),
    q: str = Query(default=""),
) -> dict:
    query = q.strip().lower()

    if settings.pactsignal_demo_mode:
        items = [dict(lead) for lead in DEMO_LEADS]
        if status:
            items = [lead for lead in items if lead["status"] == status]
        items = [lead for lead in items if int(lead["score"]) >= min_score]
        if query:
            items = [
                lead for lead in items
                if query in lead["company"].lower()
                or query in lead["domain"].lower()
                or query in (lead.get("proof_project") or "").lower()
            ]
        total = len(items)
        items = items[:limit]
        return {"items": [_serialize_lead(item) for item in items], "total": total}

    init_db()
    rows = [_serialize_lead(r) for r in list_leads(status=status, min_score=min_score, limit=5000)]
    if query:
        rows = [
            row for row in rows
            if query in row["company"].lower()
            or query in row["domain"].lower()
            or query in (row.get("proof_project") or "").lower()
            or query in (row.get("contact_email") or "").lower()
        ]
    total = len(rows)
    return {"items": rows[:limit], "total": total}


@app.get("/api/leads/{lead_id}")
def lead_detail(lead_id: int) -> dict:
    return _demo_lead(lead_id) if settings.pactsignal_demo_mode else _live_lead(lead_id)


@app.post("/api/leads/{lead_id}/approve")
def approve(lead_id: int) -> dict:
    _require_live_mode()
    lead = _live_lead(lead_id)
    if lead["status"] not in {"drafted", "rejected"} or not lead.get("draft"):
        raise HTTPException(status_code=409, detail="Lead must have a draft before approval")
    update_lead(lead_id, status="approved")
    return _live_lead(lead_id)


@app.post("/api/leads/{lead_id}/reject")
def reject(lead_id: int) -> dict:
    _require_live_mode()
    _live_lead(lead_id)
    update_lead(lead_id, status="rejected")
    return _live_lead(lead_id)


@app.post("/api/leads/{lead_id}/do-not-contact")
def do_not_contact(lead_id: int) -> dict:
    _require_live_mode()
    _live_lead(lead_id)
    update_lead(lead_id, status="do_not_contact")
    return _live_lead(lead_id)


@app.post("/api/leads/{lead_id}/allow-contact")
def allow_contact(lead_id: int) -> dict:
    _require_live_mode()
    lead = _live_lead(lead_id)
    if lead["status"] != "do_not_contact":
        raise HTTPException(status_code=409, detail="Lead is not marked do_not_contact")
    update_lead(lead_id, status="rejected")
    return _live_lead(lead_id)


@app.post("/api/leads/{lead_id}/gmail-draft")
def gmail_draft(lead_id: int) -> dict:
    _require_live_mode()
    lead = _live_lead(lead_id)
    if lead["status"] != "approved":
        raise HTTPException(status_code=409, detail="Lead must be approved first")
    if not lead.get("contact_email"):
        raise HTTPException(status_code=409, detail="Lead has no public contact email")
    if not lead.get("subject") or not lead.get("draft"):
        raise HTTPException(status_code=409, detail="Lead has no outreach draft")
    draft_id = create_draft(lead["contact_email"], lead["subject"], lead["draft"])
    update_lead(lead_id, gmail_draft_id=draft_id, status="gmail_drafted")
    return _live_lead(lead_id)


@app.post("/api/leads/{lead_id}/mark-sent")
def mark_sent(lead_id: int) -> dict:
    _require_live_mode()
    lead = _live_lead(lead_id)
    if lead["status"] not in {"gmail_drafted", "approved"}:
        raise HTTPException(status_code=409, detail="Lead must be approved or have a Gmail draft")
    sent_at = datetime.now(timezone.utc)
    followup = sent_at + timedelta(days=settings.followup_days)
    update_lead(
        lead_id,
        status="sent",
        last_contact_at=sent_at.isoformat(),
        followup_due_at=followup.isoformat(),
    )
    return _live_lead(lead_id)


@app.post("/api/leads/{lead_id}/refresh-research")
def refresh_research(lead_id: int) -> dict:
    """Re-crawl and re-research an existing lead without changing workflow state.

    Protected workflow state (status, draft, gmail_draft_id, follow-up dates)
    is never modified.  Contact fields are updated only when the refresh
    discovers a better contact than the one currently stored.
    """
    _require_live_mode()
    lead = _live_lead(lead_id)
    try:
        result = refresh_lead_research(lead_id)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Research refresh failed: {type(exc).__name__}: {exc}",
        ) from exc
    refreshed = _live_lead(lead_id)
    return {"refresh": result, "lead": refreshed}


@app.post("/api/runs/discovery")
def run_discovery(limit: int = Query(default=20, ge=1, le=100)) -> dict:
    """Start a read-only discovery run in the background and return the run row.

    Returns immediately with ``status`` set to ``running``.  Poll
    ``GET /api/runs/{id}`` to observe progress and completion.  A concurrent
    run request returns ``409``.
    """
    _require_live_mode()

    def execute(lim: int, progress_cb):
        return discover_only(lim, progress_cb=progress_cb)

    return _start_background_run(RUN_TYPE_DISCOVERY, limit, execute)


@app.post("/api/runs/process")
def run_process(limit: int = Query(default=10, ge=1, le=50)) -> dict:
    """Start a 'Process prospects' run (discovery + qualification + drafting).

    This is the user-facing label for the outreach-processing pipeline.  It
    does NOT send email.  Returns immediately with ``status`` set to
    ``running``; poll ``GET /api/runs/{id}`` for progress.  A concurrent run
    request returns ``409``.
    """
    _require_live_mode()

    def execute(lim: int, progress_cb):
        return run_pipeline(lim, progress_cb=progress_cb)

    return _start_background_run(RUN_TYPE_PROCESSING, limit, execute)


@app.post("/api/runs/outreach")
def run_outreach(limit: int = Query(default=10, ge=1, le=50)) -> dict:
    """Backward-compatible alias for ``POST /api/runs/process``.

    The user-facing operator label is 'Process prospects'; this route is kept
    so existing CLI/automation callers continue to work.  It runs the same
    background processing pipeline and persists the run as type
    ``processing``.
    """
    _require_live_mode()

    def execute(lim: int, progress_cb):
        return run_pipeline(lim, progress_cb=progress_cb)

    return _start_background_run(RUN_TYPE_PROCESSING, limit, execute)


@app.get("/api/runs")
def runs(
    limit: int = Query(default=50, ge=1, le=200),
    type: str | None = Query(default=None),
) -> dict:
    """List recent persistent run rows (most recent first).

    Optional ``type`` filter (``discovery`` or ``processing``) restricts the
    list to a single run type.  Used by the Discovery page to retrieve the
    most recent persisted discovery run independent of ``_LATEST_RUN``
    (R1-4).
    """
    if settings.pactsignal_demo_mode:
        return {"items": [], "total": 0}
    init_db()
    if type:
        rows = [_serialize_run(r) for r in list_runs_by_type(type, limit=limit)]
    else:
        rows = [_serialize_run(r) for r in list_runs(limit=limit)]
    return {"items": rows, "total": len(rows)}


@app.get("/api/runs/{run_id}")
def run_detail(run_id: int) -> dict:
    """Return a single persistent run row by id."""
    if settings.pactsignal_demo_mode:
        raise HTTPException(status_code=404, detail="Run not found")
    init_db()
    row = get_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    return _serialize_run(row)


@app.get("/api/followups")
def followups() -> dict:
    """List sent leads whose follow-up date is due."""
    if settings.pactsignal_demo_mode:
        # Surface the demo sent leads as due follow-ups for portfolio display.
        items = [
            {
                "id": lead["id"],
                "company": lead["company"],
                "domain": lead["domain"],
                "contact_email": lead.get("contact_email") or "",
                "status": lead["status"],
                "last_contact_at": lead.get("last_contact_at"),
                "followup_due_at": lead.get("followup_due_at"),
            }
            for lead in DEMO_LEADS
            if lead["status"] == "sent"
        ]
        return {"items": items, "total": len(items)}
    init_db()
    rows = due_followups(now_iso())
    items = [
        {
            "id": row["id"],
            "company": row["company"],
            "domain": row["domain"],
            "contact_email": row["contact_email"] or "",
            "status": row["status"],
            "last_contact_at": row["last_contact_at"],
            "followup_due_at": row["followup_due_at"],
        }
        for row in rows
    ]
    return {"items": items, "total": len(items)}


# Serve the compiled React app when it is present (Docker/web runtime). During
# Vite development, the frontend runs separately and uses the /api proxy.
_DIST_DIR = Path(
    os.getenv(
        "PACTSIGNAL_FRONTEND_DIST",
        str(Path(__file__).resolve().parents[1] / "frontend" / "dist"),
    )
)
if _DIST_DIR.exists():
    assets = _DIST_DIR / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")

        dist_root = _DIST_DIR.resolve()
        candidate = (dist_root / full_path).resolve()
        try:
            candidate.relative_to(dist_root)
        except ValueError:
            raise HTTPException(status_code=404, detail="Not found")

        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(dist_root / "index.html")

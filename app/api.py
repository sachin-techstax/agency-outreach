from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import (
    SUPPRESSED_STATUSES,
    due_followups,
    get_lead,
    init_db,
    list_leads,
    now_iso,
    update_lead,
)
from .demo_data import DEMO_LEADS, DEMO_LATEST_RUN, demo_dashboard
from .gmail_client import create_draft
from .pipeline import discover_only, run as run_pipeline

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

_RUN_LOCK = threading.Lock()
_LATEST_RUN: dict[str, Any] | None = None


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


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "product": "PactSignal",
        "demo_mode": settings.pactsignal_demo_mode,
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
    return {
        "mode": "private",
        "counts": counts,
        "due_followups": len(due_followups(now_iso())),
        "review_queue": review_queue,
        "latest_run": _LATEST_RUN,
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
        items = items[:limit]
        return {"items": [_serialize_lead(item) for item in items], "total": len(items)}

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


@app.post("/api/runs/discovery")
def run_discovery(limit: int = Query(default=20, ge=1, le=100)) -> dict:
    _require_live_mode()

    def execute():
        global _LATEST_RUN
        result = discover_only(limit)
        _LATEST_RUN = {"type": "discovery", **result}
        return _LATEST_RUN

    return _run_exclusive(execute)


@app.post("/api/runs/outreach")
def run_outreach(limit: int = Query(default=10, ge=1, le=50)) -> dict:
    _require_live_mode()

    def execute():
        global _LATEST_RUN
        result = run_pipeline(limit)
        _LATEST_RUN = {"type": "outreach", **result}
        return _LATEST_RUN

    return _run_exclusive(execute)


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
        candidate = _DIST_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_DIST_DIR / "index.html")

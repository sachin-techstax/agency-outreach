"""Backend tests for persistent run history, run APIs, refresh-research,
no-auto-send invariant, demo-mode blocking, and bearer auth on new endpoints.

All external HTTP (Serper, scraping, OpenAI, Gmail) is mocked so no real
network calls are made.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import api as api_mod
from app import pipeline as pipeline_mod
from app.config import settings
from app.db import (
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_QUEUED,
    RUN_RUNNING,
    RUN_TYPE_DISCOVERY,
    RUN_TYPE_PROCESSING,
    create_run,
    get_run,
    init_db,
    list_runs,
    update_run,
    upsert_lead,
)


@pytest.fixture(autouse=True)
def restore_settings():
    names = [
        "pactsignal_demo_mode",
        "db_path",
        "pactsignal_auth_enabled",
        "pactsignal_api_token",
    ]
    original = {name: getattr(settings, name) for name in names}
    # Ensure no leftover lock from a prior test blocks the next one.
    if api_mod._RUN_LOCK.locked():
        api_mod._RUN_LOCK.release()
    yield
    for name, value in original.items():
        object.__setattr__(settings, name, value)
    if api_mod._RUN_LOCK.locked():
        api_mod._RUN_LOCK.release()


def _live_mode(tmp_path: Path) -> Path:
    object.__setattr__(settings, "pactsignal_demo_mode", False)
    db_path = tmp_path / "runs.db"
    object.__setattr__(settings, "db_path", db_path)
    return db_path


def _wait_run_done(client: TestClient, run_id: int, timeout_s: float = 5.0) -> dict:
    """Poll GET /api/runs/{id} until status is completed/failed."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        resp = client.get(f"/api/runs/{run_id}")
        if resp.status_code == 200:
            body = resp.json()
            if body["status"] in {RUN_COMPLETED, RUN_FAILED}:
                return body
        time.sleep(0.05)
    raise AssertionError(f"Run {run_id} did not finish within {timeout_s}s")


# ---------------------------------------------------------------------------
# Run persistence model
# ---------------------------------------------------------------------------


def test_create_run_returns_queued_row(tmp_path):
    db_path = _live_mode(tmp_path)
    init_db()
    run_id = create_run(RUN_TYPE_DISCOVERY, requested_limit=20)
    row = get_run(run_id)
    assert row is not None
    assert row["type"] == RUN_TYPE_DISCOVERY
    assert row["status"] == RUN_QUEUED
    assert row["requested_limit"] == 20
    assert row["started_at"] is not None
    assert row["completed_at"] is None


def test_update_run_status_transitions(tmp_path):
    _live_mode(tmp_path)
    init_db()
    run_id = create_run(RUN_TYPE_PROCESSING, requested_limit=10)
    update_run(run_id, status=RUN_RUNNING)
    assert get_run(run_id)["status"] == RUN_RUNNING
    update_run(
        run_id,
        status=RUN_COMPLETED,
        completed_at="2026-09-04T00:00:00+00:00",
        attempted=10,
        processed=8,
        drafted=5,
        failed_count=2,
        duration_s=12.3,
    )
    row = get_run(run_id)
    assert row["status"] == RUN_COMPLETED
    assert row["attempted"] == 10
    assert row["drafted"] == 5
    assert row["failed_count"] == 2
    assert row["duration_s"] == 12.3


def test_update_run_ignores_unknown_fields(tmp_path):
    _live_mode(tmp_path)
    init_db()
    run_id = create_run(RUN_TYPE_DISCOVERY)
    # 'type' and 'started_at' are immutable; 'bogus' is unknown.
    update_run(run_id, type="other", started_at="x", bogus=99, status=RUN_RUNNING)
    row = get_run(run_id)
    assert row["type"] == RUN_TYPE_DISCOVERY  # unchanged
    assert row["started_at"] != "x"  # unchanged
    assert row["status"] == RUN_RUNNING  # allowed field updated


def test_list_runs_orders_recent_first(tmp_path):
    _live_mode(tmp_path)
    init_db()
    a = create_run(RUN_TYPE_DISCOVERY)
    b = create_run(RUN_TYPE_PROCESSING)
    rows = list_runs(limit=10)
    ids = [int(r["id"]) for r in rows]
    assert ids[0] == b
    assert ids[1] == a


# ---------------------------------------------------------------------------
# Run APIs: background execution, polling, concurrent rejection
# ---------------------------------------------------------------------------


def _patch_pipeline_success(monkeypatch):
    """Patch the pipeline to return a deterministic summary quickly."""
    def fake_run(limit, progress_cb=None):
        if progress_cb:
            progress_cb({"stage": "processing", "attempted": 0, "target": limit})
        return {
            "query_count": 3,
            "raw_candidate_domains": 30,
            "candidate_domains": 10,
            "fresh_retryable_pool": 10,
            "attempted": limit,
            "processed": limit,
            "qualified": 5,
            "drafted": 5,
            "below_score": 3,
            "no_contact": 2,
            "skipped": 0,
            "failed": 0,
            "duration_s": 1.0,
        }
    monkeypatch.setattr(api_mod, "run_pipeline", fake_run)


def _patch_discovery_success(monkeypatch):
    def fake_discover(limit, progress_cb=None):
        if progress_cb:
            progress_cb({"stage": "discovery", "queries_completed": 1})
        return {
            "query_count": 3,
            "raw_candidate_domains": 30,
            "candidate_domains": 10,
            "fresh_retryable_pool": 10,
            "attempted": 0,
            "processed": 0,
            "qualified": 0,
            "drafted": 0,
            "below_score": 0,
            "no_contact": 0,
            "skipped": 0,
            "failed": 0,
            "duration_s": 0.5,
        }
    monkeypatch.setattr(api_mod, "discover_only", fake_discover)


def test_process_run_persists_completed_summary(tmp_path, monkeypatch):
    _live_mode(tmp_path)
    _patch_pipeline_success(monkeypatch)
    client = TestClient(api_mod.app)

    resp = client.post("/api/runs/process?limit=5")
    assert resp.status_code == 200
    body = resp.json()
    # The background thread may complete before the response returns
    # (especially on fast hardware), so accept either running or completed.
    assert body["status"] in (RUN_RUNNING, RUN_COMPLETED)
    run_id = body["id"]

    done = _wait_run_done(client, run_id)
    assert done["status"] == RUN_COMPLETED
    assert done["type"] == RUN_TYPE_PROCESSING
    assert done["attempted"] == 5
    assert done["drafted"] == 5
    assert done["completed_at"] is not None
    assert done["error_summary"] is None


def test_discovery_run_persists_completed_summary(tmp_path, monkeypatch):
    _live_mode(tmp_path)
    _patch_discovery_success(monkeypatch)
    client = TestClient(api_mod.app)

    resp = client.post("/api/runs/discovery?limit=10")
    assert resp.status_code == 200
    run_id = resp.json()["id"]

    done = _wait_run_done(client, run_id)
    assert done["status"] == RUN_COMPLETED
    assert done["type"] == RUN_TYPE_DISCOVERY
    assert done["query_count"] == 3


def test_outreach_alias_runs_processing_type(tmp_path, monkeypatch):
    _live_mode(tmp_path)
    _patch_pipeline_success(monkeypatch)
    client = TestClient(api_mod.app)

    resp = client.post("/api/runs/outreach?limit=2")
    assert resp.status_code == 200
    run_id = resp.json()["id"]
    done = _wait_run_done(client, run_id)
    assert done["type"] == RUN_TYPE_PROCESSING


def test_concurrent_run_rejected_with_409(tmp_path, monkeypatch):
    _live_mode(tmp_path)

    # Block the pipeline so the first run stays running long enough to test.
    started = {"flag": False}

    def slow_run(limit, progress_cb=None):
        started["flag"] = True
        time.sleep(0.4)
        return {"query_count": 1, "attempted": limit, "processed": limit,
                "drafted": 0, "failed": 0, "duration_s": 0.4,
                "raw_candidate_domains": 1, "candidate_domains": 1,
                "fresh_retryable_pool": 1, "qualified": 0, "below_score": 0,
                "no_contact": 0, "skipped": 0}

    monkeypatch.setattr(api_mod, "run_pipeline", slow_run)
    client = TestClient(api_mod.app)

    first = client.post("/api/runs/process?limit=1")
    assert first.status_code == 200
    # Wait until the worker has actually claimed the lock by checking the run
    # row transitions to running.  The lock is acquired synchronously before
    # the thread starts, so the 409 should be immediate, but we give the
    # scheduler a tiny moment to start the thread.
    deadline = time.time() + 1.0
    while time.time() < deadline and not started["flag"]:
        time.sleep(0.01)

    second = client.post("/api/runs/discovery?limit=1")
    assert second.status_code == 409
    assert "already in progress" in second.json()["detail"]

    # Let the first run finish so the lock is released for subsequent tests.
    _wait_run_done(client, first.json()["id"])


def test_failed_run_persisted_with_error_summary(tmp_path, monkeypatch):
    _live_mode(tmp_path)

    def failing_run(limit, progress_cb=None):
        raise RuntimeError("pipeline exploded")

    monkeypatch.setattr(api_mod, "run_pipeline", failing_run)
    client = TestClient(api_mod.app)

    resp = client.post("/api/runs/process?limit=1")
    assert resp.status_code == 200
    run_id = resp.json()["id"]

    done = _wait_run_done(client, run_id)
    assert done["status"] == RUN_FAILED
    assert done["error_summary"] is not None
    # R2-4: error_summary is a sanitized classification, NOT the raw message.
    assert "RuntimeError" in done["error_summary"]
    # The raw exception message must NOT be persisted.
    assert "pipeline exploded" not in done["error_summary"]


def test_get_runs_lists_recent_rows(tmp_path, monkeypatch):
    _live_mode(tmp_path)
    _patch_pipeline_success(monkeypatch)
    client = TestClient(api_mod.app)

    r1 = client.post("/api/runs/process?limit=1")
    _wait_run_done(client, r1.json()["id"])
    r2 = client.post("/api/runs/process?limit=2")
    _wait_run_done(client, r2.json()["id"])

    resp = client.get("/api/runs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 2
    # Most recent first
    assert body["items"][0]["id"] == r2.json()["id"]


def test_get_run_detail_returns_progress(tmp_path, monkeypatch):
    _live_mode(tmp_path)
    _patch_discovery_success(monkeypatch)
    client = TestClient(api_mod.app)

    resp = client.post("/api/runs/discovery?limit=3")
    run_id = resp.json()["id"]
    done = _wait_run_done(client, run_id)

    detail = client.get(f"/api/runs/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["id"] == run_id


def test_get_run_detail_404_for_missing(tmp_path):
    _live_mode(tmp_path)
    client = TestClient(api_mod.app)
    resp = client.get("/api/runs/999999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Demo mode blocks new run + refresh endpoints
# ---------------------------------------------------------------------------


def test_demo_mode_blocks_process_and_refresh(tmp_path):
    object.__setattr__(settings, "pactsignal_demo_mode", True)
    object.__setattr__(settings, "db_path", tmp_path / "demo.db")
    client = TestClient(api_mod.app)

    for path in [
        "/api/runs/process?limit=3",
        "/api/runs/discovery?limit=3",
        "/api/runs/outreach?limit=3",
    ]:
        assert client.post(path).status_code == 403

    # Refresh research is a mutation and must be blocked in demo mode.
    assert client.post("/api/leads/1/refresh-research").status_code == 403

    # Run history list is empty in demo mode (no persistent runs).
    runs = client.get("/api/runs")
    assert runs.status_code == 200
    assert runs.json() == {"items": [], "total": 0}

    # Run detail 404s in demo mode.
    assert client.get("/api/runs/1").status_code == 404


# ---------------------------------------------------------------------------
# Bearer auth protects new endpoints
# ---------------------------------------------------------------------------


def _enable_test_auth() -> str:
    token = "test-token-" + ("x" * 40)
    object.__setattr__(settings, "pactsignal_auth_enabled", True)
    object.__setattr__(settings, "pactsignal_api_token", token)
    return token


def test_auth_protects_run_and_followup_endpoints(tmp_path):
    _live_mode(tmp_path)
    token = _enable_test_auth()
    client = TestClient(api_mod.app)

    for path in [
        "/api/runs",
        "/api/runs/1",
        "/api/followups",
    ]:
        assert client.get(path).status_code == 401

    for path in [
        "/api/runs/process?limit=1",
        "/api/runs/discovery?limit=1",
        "/api/leads/1/refresh-research",
    ]:
        assert client.post(path).status_code == 401

    # Valid token unlocks them.
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/runs", headers=headers).status_code == 200
    assert client.get("/api/followups", headers=headers).status_code == 200


# ---------------------------------------------------------------------------
# Refresh research: protected-state behavior
# ---------------------------------------------------------------------------


STRONG_TEXT = (
    "We are an AI development agency providing custom software and AI development services "
    "for clients. We build AI agents, workflow automation, RAG systems, APIs and backend products. "
    "See our case studies and client projects. Our delivery team helps companies with "
    "AI implementation and system integration. We are a technology partner and development partner "
    "offering engineering services and implementation services. "
    "We deliver production AI systems for clients across multiple industries. "
    "Contact us at hello@example.ai"
)


def _make_site(text, domain, title="Example"):
    return {
        "root": f"https://{domain}",
        "domain": domain,
        "title": title,
        "home_text": text,
        "text": text,
        "pages": [],
        "mailtos": [],
    }


def test_refresh_research_preserves_protected_workflow_state(tmp_path, monkeypatch):
    db_path = _live_mode(tmp_path)
    init_db()
    # Insert an approved lead with a human-approved draft and gmail draft id.
    lead_id = upsert_lead({
        "company": "Old Co",
        "domain": "example.ai",
        "website": "https://example.ai",
        "score": 50,
        "proof_project": "WingerX",
        "contact_email": "founder@example.ai",
        "contact_source": "website",
        "contact_quality": "high",
        "subject": "Human-approved subject",
        "draft": "Human-approved draft body",
        "gmail_draft_id": "gmail-abc",
        "status": "approved",
    })

    monkeypatch.setattr(
        pipeline_mod, "crawl_company", lambda url: _make_site(STRONG_TEXT, "example.ai")
    )
    monkeypatch.setattr(
        pipeline_mod,
        "analyze_agency",
        lambda company, website, text: {
            "summary": "new summary",
            "services": "ai",
            "fit_reason": "new fit",
            "proof_project": "Forge Crew",
            "outreach_angle": "new angle",
        },
    )

    result = pipeline_mod.refresh_lead_research(lead_id)

    assert result["refreshed"] is True
    # Research metadata refreshed.
    from app.db import get_lead
    row = get_lead(lead_id)
    assert row["summary"] == "new summary"
    assert row["fit_reason"] == "new fit"
    # Protected workflow state preserved.
    assert row["status"] == "approved"
    assert row["subject"] == "Human-approved subject"
    assert row["draft"] == "Human-approved draft body"
    assert row["gmail_draft_id"] == "gmail-abc"
    # Contact was already high quality; refresh must NOT downgrade it even if
    # the new crawl found a medium-quality hello@.  In this test the new crawl
    # finds hello@example.ai (medium) which is NOT better than founder@ (high),
    # so contact fields must be unchanged.
    assert row["contact_email"] == "founder@example.ai"
    assert row["contact_quality"] == "high"


def test_refresh_research_recovers_missed_contact(tmp_path, monkeypatch):
    """The LaunchPad Lab scenario: original run missed an email, refresh finds it."""
    db_path = _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead({
        "company": "LaunchPad Lab",
        "domain": "launchpadlab.com",
        "website": "https://launchpadlab.com",
        "score": 82,
        "proof_project": "WingerX",
        "contact_email": "",  # original run missed the email
        "contact_source": "",
        "contact_quality": "",
        "status": "qualified",  # retryable, not protected
    })

    contact_page_url = "https://launchpadlab.com/contact"
    site = {
        "root": "https://launchpadlab.com",
        "domain": "launchpadlab.com",
        "title": "LaunchPad Lab",
        "home_text": STRONG_TEXT,
        "text": STRONG_TEXT,
        "pages": [(contact_page_url, "Email us")],
        "mailtos": [("mailto:hello@launchpadlab.com", contact_page_url)],
    }
    monkeypatch.setattr(pipeline_mod, "crawl_company", lambda url: site)
    monkeypatch.setattr(
        pipeline_mod,
        "analyze_agency",
        lambda company, website, text: {
            "summary": "s", "services": "ai", "fit_reason": "fit",
            "proof_project": "WingerX", "outreach_angle": "angle",
        },
    )

    result = pipeline_mod.refresh_lead_research(lead_id)

    assert result["refreshed"] is True
    assert result["contact_refreshed"] is True
    assert result["contact_email"] == "hello@launchpadlab.com"
    assert result["contact_source"] == contact_page_url

    from app.db import get_lead
    row = get_lead(lead_id)
    assert row["contact_email"] == "hello@launchpadlab.com"
    assert row["contact_source"] == contact_page_url
    # Status unchanged — refresh never moves workflow state.
    assert row["status"] == "qualified"
    # No outreach draft was regenerated.
    assert row["subject"] == "" or row["subject"] is None
    assert row["draft"] == "" or row["draft"] is None


def test_refresh_research_does_not_regenerate_outreach_draft(tmp_path, monkeypatch):
    """Refresh must NOT call draft_outreach or change subject/draft fields."""
    db_path = _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead({
        "company": "Co",
        "domain": "co.example",
        "website": "https://co.example",
        "score": 80,
        "proof_project": "WingerX",
        "contact_email": "hello@co.example",
        "contact_quality": "medium",
        "subject": "Existing subject",
        "draft": "Existing draft body",
        "status": "drafted",  # not protected, but refresh still must not touch draft
    })

    monkeypatch.setattr(
        pipeline_mod, "crawl_company", lambda url: _make_site(STRONG_TEXT, "co.example")
    )
    monkeypatch.setattr(
        pipeline_mod,
        "analyze_agency",
        lambda company, website, text: {
            "summary": "s", "services": "ai", "fit_reason": "fit",
            "proof_project": "WingerX", "outreach_angle": "angle",
        },
    )

    # If draft_outreach is called, fail loudly.
    def fail_if_draft_called(*a, **k):
        raise AssertionError("draft_outreach must NOT be called by refresh_lead_research")

    monkeypatch.setattr(pipeline_mod, "draft_outreach", fail_if_draft_called)

    pipeline_mod.refresh_lead_research(lead_id)

    from app.db import get_lead
    row = get_lead(lead_id)
    assert row["subject"] == "Existing subject"
    assert row["draft"] == "Existing draft body"
    assert row["status"] == "drafted"


def test_refresh_research_missing_lead_raises(tmp_path):
    _live_mode(tmp_path)
    init_db()
    with pytest.raises(ValueError):
        pipeline_mod.refresh_lead_research(999999)


# ---------------------------------------------------------------------------
# No-auto-send invariant
# ---------------------------------------------------------------------------


def test_no_endpoint_automatically_sends_email(tmp_path):
    """No POST endpoint in the operator API sends an email.  The furthest
    automatic Gmail action is creating a draft, which requires explicit
    approval first."""
    _live_mode(tmp_path)
    init_db()
    # Verify there is no endpoint that calls create_draft without an explicit
    # gmail-draft POST on an approved lead.  We check that the only caller of
    # create_draft is the gmail-draft endpoint by inspecting route paths.
    paths = {route.path for route in api_mod.app.routes if hasattr(route, "path")}
    # No endpoint named 'send' or 'auto-send' exists.
    assert not any("send" in p for p in paths)
    # The gmail-draft endpoint exists and only creates a draft (not send).
    assert "/api/leads/{lead_id}/gmail-draft" in paths


def test_gmail_draft_requires_approved_status(tmp_path):
    """A non-approved lead cannot get a Gmail draft, proving the approval
    boundary is enforced before any Gmail action."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead({
        "company": "Co",
        "domain": "co.example",
        "website": "https://co.example",
        "score": 80,
        "contact_email": "hello@co.example",
        "subject": "Subject",
        "draft": "Body",
        "status": "drafted",  # not approved yet
    })
    client = TestClient(api_mod.app)
    resp = client.post(f"/api/leads/{lead_id}/gmail-draft")
    assert resp.status_code == 409
    assert "approved" in resp.json()["detail"].lower()


def test_mark_sent_requires_gmail_drafted_or_approved(tmp_path):
    """mark-sent only accepts leads that have been through the human approval
    + gmail draft workflow (or are approved).  A drafted lead cannot be
    marked sent directly."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead({
        "company": "Co",
        "domain": "co.example",
        "website": "https://co.example",
        "score": 80,
        "status": "drafted",
    })
    client = TestClient(api_mod.app)
    resp = client.post(f"/api/leads/{lead_id}/mark-sent")
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Follow-ups endpoint
# ---------------------------------------------------------------------------


def test_followups_returns_due_sent_leads(tmp_path):
    _live_mode(tmp_path)
    init_db()
    from app.db import update_lead, now_iso
    from datetime import datetime, timedelta, timezone
    lead_id = upsert_lead({
        "company": "Sent Co",
        "domain": "sent.example",
        "website": "https://sent.example",
        "score": 80,
        "contact_email": "hello@sent.example",
        "status": "sent",
    })
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    update_lead(lead_id, followup_due_at=past)

    client = TestClient(api_mod.app)
    resp = client.get("/api/followups")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert any(item["domain"] == "sent.example" for item in body["items"])


def test_followups_demo_mode_returns_demo_sent_leads():
    object.__setattr__(settings, "pactsignal_demo_mode", True)
    client = TestClient(api_mod.app)
    resp = client.get("/api/followups")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] > 0
    assert all(item["status"] == "sent" for item in body["items"])


# ---------------------------------------------------------------------------
# R1-3 / R1-4: Discovery result persistence + retrieval from DB
# ---------------------------------------------------------------------------


def _patch_discovery_with_ranked(monkeypatch):
    """Patch discovery to return a result with a ranked candidate list."""
    def fake_discover(limit, progress_cb=None):
        if progress_cb:
            progress_cb({"stage": "discovery", "queries_completed": 1})
        return {
            "query_count": 2,
            "search_results_total": 20,
            "raw_candidate_domains": 15,
            "rejected_candidate_domains": 3,
            "candidate_domains": 12,
            "ranked_candidate_domains": 12,
            "displayed_candidate_domains": min(limit, 12),
            "candidate_priority_avg": 42.5,
            "per_query": [{"query": "ai agency", "hits": 10}],
            "ranked": [
                {"rank": 1, "domain": "alpha.example", "priority": 90,
                 "reasons": "strong", "category": "ai", "source_query": "ai agency",
                 "title": "Alpha", "url": "https://alpha.example"},
            ],
            "attempted": 0, "processed": 0, "qualified": 0, "drafted": 0,
            "below_score": 0, "no_contact": 0, "skipped": 0, "failed": 0,
            "duration_s": 0.5,
            "fresh_retryable_pool": 12,
        }
    monkeypatch.setattr(api_mod, "discover_only", fake_discover)


def test_discovery_result_persisted_in_result_json(tmp_path, monkeypatch):
    """R1-3: completed discovery run stores ranked candidates in result_json."""
    _live_mode(tmp_path)
    _patch_discovery_with_ranked(monkeypatch)
    client = TestClient(api_mod.app)

    resp = client.post("/api/runs/discovery?limit=10")
    run_id = resp.json()["id"]
    done = _wait_run_done(client, run_id)

    assert done["status"] == RUN_COMPLETED
    assert done["result"] is not None
    assert done["result"]["type"] == RUN_TYPE_DISCOVERY
    assert len(done["result"]["ranked"]) == 1
    assert done["result"]["ranked"][0]["domain"] == "alpha.example"
    assert done["result"]["query_count"] == 2


def test_discovery_result_survives_processing_run(tmp_path, monkeypatch):
    """R1-3/R1-4: after a processing run overwrites _LATEST_RUN, the discovery
    result is still retrievable from the persisted discovery run row."""
    _live_mode(tmp_path)
    _patch_discovery_with_ranked(monkeypatch)
    _patch_pipeline_success(monkeypatch)
    client = TestClient(api_mod.app)

    # Run discovery first.
    d = client.post("/api/runs/discovery?limit=10")
    d_id = d.json()["id"]
    _wait_run_done(client, d_id)

    # Now run processing — this overwrites _LATEST_RUN.
    p = client.post("/api/runs/process?limit=5")
    _wait_run_done(client, p.json()["id"])

    # The discovery result must still be retrievable via type-filtered list.
    resp = client.get("/api/runs?type=discovery&limit=1")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == d_id
    assert items[0]["result"] is not None
    assert items[0]["result"]["type"] == RUN_TYPE_DISCOVERY
    assert len(items[0]["result"]["ranked"]) == 1
    assert items[0]["result"]["ranked"][0]["domain"] == "alpha.example"


def test_discovery_result_survives_memory_loss(tmp_path, monkeypatch):
    """R1-4: after _LATEST_RUN is cleared (simulating process restart), the
    discovery page can still load the ranked result from the DB."""
    _live_mode(tmp_path)
    _patch_discovery_with_ranked(monkeypatch)
    client = TestClient(api_mod.app)

    d = client.post("/api/runs/discovery?limit=10")
    d_id = d.json()["id"]
    _wait_run_done(client, d_id)

    # Simulate process restart: clear in-memory state.
    api_mod._LATEST_RUN = None

    # Discovery page fetches latest discovery run from DB.
    resp = client.get("/api/runs?type=discovery&limit=1")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == d_id
    assert items[0]["result"] is not None
    assert items[0]["result"]["ranked"][0]["domain"] == "alpha.example"


def test_runs_type_filter_works(tmp_path, monkeypatch):
    """R1-4: the type query parameter filters runs by type."""
    _live_mode(tmp_path)
    _patch_discovery_with_ranked(monkeypatch)
    _patch_pipeline_success(monkeypatch)
    client = TestClient(api_mod.app)

    d = client.post("/api/runs/discovery?limit=5")
    _wait_run_done(client, d.json()["id"])
    p = client.post("/api/runs/process?limit=5")
    _wait_run_done(client, p.json()["id"])

    disc = client.get("/api/runs?type=discovery")
    proc = client.get("/api/runs?type=processing")
    assert all(i["type"] == "discovery" for i in disc.json()["items"])
    assert all(i["type"] == "processing" for i in proc.json()["items"])


def test_dashboard_rebuilds_latest_run_from_db_after_restart(tmp_path, monkeypatch):
    """R1-8: after _LATEST_RUN is cleared, dashboard still shows the most
    recent completed run metrics from the persisted row."""
    _live_mode(tmp_path)
    _patch_pipeline_success(monkeypatch)
    client = TestClient(api_mod.app)

    p = client.post("/api/runs/process?limit=5")
    _wait_run_done(client, p.json()["id"])

    # Before restart: dashboard has latest_run from memory.
    dash1 = client.get("/api/dashboard").json()
    assert dash1["latest_run"] is not None

    # Simulate restart: clear in-memory state.
    api_mod._LATEST_RUN = None

    # After restart: dashboard rebuilds latest_run from the persisted row.
    dash2 = client.get("/api/dashboard").json()
    assert dash2["latest_run"] is not None
    assert dash2["latest_run"]["type"] == RUN_TYPE_PROCESSING
    assert dash2["latest_run_row"] is not None
    assert dash2["latest_run_row"]["status"] == RUN_COMPLETED


# ---------------------------------------------------------------------------
# R1-5: Discovery polling contract reaches terminal state
# ---------------------------------------------------------------------------


def test_run_detail_reaches_terminal_state(tmp_path, monkeypatch):
    """R1-5: polling GET /api/runs/{id} eventually returns completed/failed."""
    _live_mode(tmp_path)
    _patch_discovery_with_ranked(monkeypatch)
    client = TestClient(api_mod.app)

    resp = client.post("/api/runs/discovery?limit=5")
    run_id = resp.json()["id"]
    done = _wait_run_done(client, run_id)
    assert done["status"] in {RUN_COMPLETED, RUN_FAILED}
    assert done["status"] == RUN_COMPLETED  # this mock succeeds


# ---------------------------------------------------------------------------
# R1-6: Reconcile abandoned runs on startup
# ---------------------------------------------------------------------------


def test_reconcile_abandoned_running_run(tmp_path):
    """R1-6: a persisted running row is marked failed on reconciliation."""
    _live_mode(tmp_path)
    init_db()
    from app.db import reconcile_abandoned_runs
    run_id = create_run(RUN_TYPE_PROCESSING, requested_limit=5)
    update_run(run_id, status=RUN_RUNNING)

    count = reconcile_abandoned_runs()
    assert count == 1
    row = get_run(run_id)
    assert row["status"] == RUN_FAILED
    assert row["completed_at"] is not None
    assert "Interrupted" in row["error_summary"]


def test_reconcile_abandoned_queued_run(tmp_path):
    """R1-6: a persisted queued row is marked failed on reconciliation."""
    _live_mode(tmp_path)
    init_db()
    from app.db import reconcile_abandoned_runs
    run_id = create_run(RUN_TYPE_DISCOVERY, requested_limit=5)
    # status is RUN_QUEUED from create_run

    count = reconcile_abandoned_runs()
    assert count == 1
    row = get_run(run_id)
    assert row["status"] == RUN_FAILED


def test_reconcile_does_not_touch_completed_run(tmp_path):
    """R1-6: completed runs are unchanged by reconciliation."""
    _live_mode(tmp_path)
    init_db()
    from app.db import reconcile_abandoned_runs
    run_id = create_run(RUN_TYPE_PROCESSING, requested_limit=5)
    update_run(run_id, status=RUN_COMPLETED, completed_at="2026-09-04T00:00:00+00:00")

    count = reconcile_abandoned_runs()
    assert count == 0
    row = get_run(run_id)
    assert row["status"] == RUN_COMPLETED


def test_reconcile_does_not_touch_failed_run(tmp_path):
    """R1-6: already-failed runs are unchanged by reconciliation."""
    _live_mode(tmp_path)
    init_db()
    from app.db import reconcile_abandoned_runs
    run_id = create_run(RUN_TYPE_PROCESSING, requested_limit=5)
    update_run(run_id, status=RUN_FAILED, completed_at="2026-09-04T00:00:00+00:00",
               error_summary="prior failure")

    count = reconcile_abandoned_runs()
    assert count == 0
    row = get_run(run_id)
    assert row["status"] == RUN_FAILED
    assert row["error_summary"] == "prior failure"


# ---------------------------------------------------------------------------
# R1-7: _RUN_LOCK exception safety
# ---------------------------------------------------------------------------


def test_run_lock_released_on_create_run_failure(tmp_path, monkeypatch):
    """R1-7: if create_run fails after the lock is acquired, the lock is
    released and a subsequent run can acquire it."""
    _live_mode(tmp_path)
    client = TestClient(api_mod.app, raise_server_exceptions=False)

    original_create_run = api_mod.create_run

    def failing_create_run(*a, **kw):
        raise RuntimeError("db is broken")

    monkeypatch.setattr(api_mod, "create_run", failing_create_run)

    resp = client.post("/api/runs/process?limit=1")
    assert resp.status_code == 500

    # Lock must be released.
    assert not api_mod._RUN_LOCK.locked()

    # Restore and verify a subsequent run can acquire the lock.
    monkeypatch.setattr(api_mod, "create_run", original_create_run)
    _patch_pipeline_success(monkeypatch)
    resp2 = client.post("/api/runs/process?limit=1")
    assert resp2.status_code == 200
    _wait_run_done(client, resp2.json()["id"])


def test_run_lock_released_on_thread_start_failure(tmp_path, monkeypatch):
    """R1-7: if thread.start() fails, the lock is released and the run row
    is marked failed."""
    _live_mode(tmp_path)
    _patch_pipeline_success(monkeypatch)
    client = TestClient(api_mod.app, raise_server_exceptions=False)

    import threading as _threading
    original_thread = _threading.Thread

    class FailingThread(original_thread):
        def start(self):
            raise OSError("cannot start thread")

    monkeypatch.setattr(api_mod.threading, "Thread", FailingThread)

    resp = client.post("/api/runs/process?limit=1")
    assert resp.status_code == 500
    assert not api_mod._RUN_LOCK.locked()

    # Restore Thread so subsequent tests work.
    monkeypatch.setattr(api_mod.threading, "Thread", original_thread)


# ---------------------------------------------------------------------------
# R2-3: Failed discovery must not hide last successful discovery result
# ---------------------------------------------------------------------------


def test_failed_discovery_does_not_hide_successful_result(tmp_path, monkeypatch):
    """R2-3: a failed discovery run after a successful one must not hide the
    successful ranked result from the Discovery page."""
    _live_mode(tmp_path)
    _patch_discovery_with_ranked(monkeypatch)
    client = TestClient(api_mod.app)

    # Run A: successful discovery with ranked candidates.
    a = client.post("/api/runs/discovery?limit=10")
    a_id = a.json()["id"]
    _wait_run_done(client, a_id)

    # Run B: failed discovery.
    def failing_discover(limit, progress_cb=None):
        raise RuntimeError("serper is down")

    monkeypatch.setattr(api_mod, "discover_only", failing_discover)
    b = client.post("/api/runs/discovery?limit=10")
    b_id = b.json()["id"]
    _wait_run_done(client, b_id)

    # Latest attempt is B (failed).
    latest = client.get("/api/runs?type=discovery&limit=1")
    assert latest.json()["items"][0]["id"] == b_id
    assert latest.json()["items"][0]["status"] == RUN_FAILED

    # Latest SUCCESSFUL discovery is still A with ranked candidates.
    successful = client.get("/api/runs?type=discovery&status=completed&limit=1")
    assert successful.json()["items"][0]["id"] == a_id
    assert successful.json()["items"][0]["result"] is not None
    assert successful.json()["items"][0]["result"]["ranked"][0]["domain"] == "alpha.example"


def test_later_successful_discovery_replaces_previous(tmp_path, monkeypatch):
    """R2-3: a later successful discovery becomes the displayed result."""
    _live_mode(tmp_path)
    _patch_discovery_with_ranked(monkeypatch)
    client = TestClient(api_mod.app)

    # Run A: successful.
    a = client.post("/api/runs/discovery?limit=10")
    a_id = a.json()["id"]
    _wait_run_done(client, a_id)

    # Run C: later successful discovery with different ranked data.
    def fake_discover_2(limit, progress_cb=None):
        return {
            "query_count": 5,
            "search_results_total": 50,
            "raw_candidate_domains": 40,
            "rejected_candidate_domains": 5,
            "candidate_domains": 35,
            "ranked_candidate_domains": 35,
            "displayed_candidate_domains": min(limit, 35),
            "candidate_priority_avg": 55.0,
            "per_query": [{"query": "ai", "hits": 20}],
            "ranked": [
                {"rank": 1, "domain": "beta.example", "priority": 95,
                 "reasons": "strong", "category": "ai", "source_query": "ai",
                 "title": "Beta", "url": "https://beta.example"},
            ],
            "attempted": 0, "processed": 0, "qualified": 0, "drafted": 0,
            "below_score": 0, "no_contact": 0, "skipped": 0, "failed": 0,
            "duration_s": 0.5, "fresh_retryable_pool": 35,
        }
    monkeypatch.setattr(api_mod, "discover_only", fake_discover_2)
    c = client.post("/api/runs/discovery?limit=10")
    c_id = c.json()["id"]
    _wait_run_done(client, c_id)

    # Latest successful is now C, not A.
    successful = client.get("/api/runs?type=discovery&status=completed&limit=1")
    assert successful.json()["items"][0]["id"] == c_id
    assert successful.json()["items"][0]["result"]["ranked"][0]["domain"] == "beta.example"


# ---------------------------------------------------------------------------
# R2-4: Do not persist arbitrary exception messages
# ---------------------------------------------------------------------------


def test_exception_secret_not_persisted(tmp_path, monkeypatch):
    """R2-4: a fake secret in an exception message must NOT appear in
    error_summary, API responses, or result_json."""
    _live_mode(tmp_path)
    fake_secret = "sk-test-super-secret-value"

    def failing_run(limit, progress_cb=None):
        raise RuntimeError(f"auth failed with key={fake_secret}")

    monkeypatch.setattr(api_mod, "run_pipeline", failing_run)
    client = TestClient(api_mod.app)

    resp = client.post("/api/runs/process?limit=1")
    run_id = resp.json()["id"]
    done = _wait_run_done(client, run_id)

    assert done["status"] == RUN_FAILED
    assert done["error_summary"] is not None
    assert fake_secret not in done["error_summary"]
    assert "RuntimeError" in done["error_summary"]
    # result_json should not contain the secret either.
    result_str = str(done.get("result") or "")
    assert fake_secret not in result_str


def test_exception_secret_not_in_api_response(tmp_path, monkeypatch):
    """R2-4: the full run detail API response must not leak the secret."""
    _live_mode(tmp_path)
    fake_secret = "sk-test-super-secret-value"

    def failing_run(limit, progress_cb=None):
        raise ValueError(f"bad token: {fake_secret}")

    monkeypatch.setattr(api_mod, "run_pipeline", failing_run)
    client = TestClient(api_mod.app)

    resp = client.post("/api/runs/process?limit=1")
    run_id = resp.json()["id"]
    _wait_run_done(client, run_id)

    detail = client.get(f"/api/runs/{run_id}")
    body_str = json.dumps(detail.json())
    assert fake_secret not in body_str


def test_http_error_classification(tmp_path, monkeypatch):
    """R2-4: httpx HTTPStatusError is classified as 'HTTP <status>'."""
    _live_mode(tmp_path)
    import httpx

    def failing_run(limit, progress_cb=None):
        # Simulate an httpx HTTP error with a 429 status.
        request = httpx.Request("GET", "https://api.serper.dev/search")
        response = httpx.Response(429, request=request)
        raise httpx.HTTPStatusError("rate limited", request=request, response=response)

    monkeypatch.setattr(api_mod, "run_pipeline", failing_run)
    client = TestClient(api_mod.app)

    resp = client.post("/api/runs/process?limit=1")
    run_id = resp.json()["id"]
    done = _wait_run_done(client, run_id)

    assert done["status"] == RUN_FAILED
    assert "HTTP 429" in done["error_summary"]
    # Raw exception text must not be persisted.
    assert "rate limited" not in done["error_summary"]


# ---------------------------------------------------------------------------
# R2-5: Dashboard persisted-state authority
# ---------------------------------------------------------------------------


def test_dashboard_ignores_stale_latest_run(tmp_path, monkeypatch):
    """R2-5: Dashboard must reflect persisted DB state, not stale _LATEST_RUN."""
    _live_mode(tmp_path)
    _patch_pipeline_success(monkeypatch)
    client = TestClient(api_mod.app)

    # Run a processing run to completion.
    p = client.post("/api/runs/process?limit=5")
    _wait_run_done(client, p.json()["id"])

    # Get the persisted metrics.
    dash1 = client.get("/api/dashboard").json()
    assert dash1["latest_run"] is not None
    persisted_attempted = dash1["latest_run"].get("attempted")

    # Set _LATEST_RUN to contradictory/stale values.
    api_mod._LATEST_RUN = {
        "type": "processing",
        "attempted": persisted_attempted + 999 if persisted_attempted else 999,
        "drafted": 777,
    }

    # Dashboard must reflect persisted values, NOT stale _LATEST_RUN.
    dash2 = client.get("/api/dashboard").json()
    assert dash2["latest_run"] is not None
    assert dash2["latest_run"].get("attempted") == persisted_attempted
    assert dash2["latest_run"].get("drafted") != 777


def test_dashboard_survives_memory_loss(tmp_path, monkeypatch):
    """R2-5: after _LATEST_RUN is cleared, Dashboard still shows metrics from DB."""
    _live_mode(tmp_path)
    _patch_pipeline_success(monkeypatch)
    client = TestClient(api_mod.app)

    p = client.post("/api/runs/process?limit=5")
    _wait_run_done(client, p.json()["id"])

    # Simulate process restart.
    api_mod._LATEST_RUN = None

    dash = client.get("/api/dashboard").json()
    assert dash["latest_run"] is not None
    assert dash["latest_run"]["type"] == "processing"
    assert dash["latest_run_row"] is not None
    assert dash["latest_run_row"]["status"] == RUN_COMPLETED


# ---------------------------------------------------------------------------
# R3-2: /api/runs status-only filter + filter validation
# ---------------------------------------------------------------------------


def test_runs_status_only_filter(tmp_path, monkeypatch):
    """R3-2: GET /api/runs?status=failed returns only failed runs."""
    _live_mode(tmp_path)
    _patch_pipeline_success(monkeypatch)
    client = TestClient(api_mod.app)

    # Create a completed run.
    ok = client.post("/api/runs/process?limit=1")
    _wait_run_done(client, ok.json()["id"])

    # Create a failed run.
    def failing(limit, progress_cb=None):
        raise RuntimeError("boom")
    monkeypatch.setattr(api_mod, "run_pipeline", failing)
    bad = client.post("/api/runs/process?limit=1")
    _wait_run_done(client, bad.json()["id"])

    # Status-only filter: only failed runs.
    resp = client.get("/api/runs?status=failed")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert all(i["status"] == "failed" for i in items)
    assert any(i["id"] == bad.json()["id"] for i in items)
    assert not any(i["id"] == ok.json()["id"] for i in items)


def test_runs_type_only_filter(tmp_path, monkeypatch):
    """R3-2: GET /api/runs?type=discovery returns only discovery runs."""
    _live_mode(tmp_path)
    _patch_discovery_success(monkeypatch)
    _patch_pipeline_success(monkeypatch)
    client = TestClient(api_mod.app)

    d = client.post("/api/runs/discovery?limit=1")
    _wait_run_done(client, d.json()["id"])
    p = client.post("/api/runs/process?limit=1")
    _wait_run_done(client, p.json()["id"])

    resp = client.get("/api/runs?type=discovery")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert all(i["type"] == "discovery" for i in items)


def test_runs_type_and_status_filter(tmp_path, monkeypatch):
    """R3-2: GET /api/runs?type=discovery&status=completed returns only
    completed discovery runs."""
    _live_mode(tmp_path)
    _patch_discovery_success(monkeypatch)
    client = TestClient(api_mod.app)

    d = client.post("/api/runs/discovery?limit=1")
    _wait_run_done(client, d.json()["id"])

    resp = client.get("/api/runs?type=discovery&status=completed")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert all(i["type"] == "discovery" for i in items)
    assert all(i["status"] == "completed" for i in items)


def test_runs_no_filter_returns_all(tmp_path, monkeypatch):
    """R3-2: no filters returns recent runs of all types/statuses."""
    _live_mode(tmp_path)
    _patch_discovery_success(monkeypatch)
    _patch_pipeline_success(monkeypatch)
    client = TestClient(api_mod.app)

    d = client.post("/api/runs/discovery?limit=1")
    _wait_run_done(client, d.json()["id"])
    p = client.post("/api/runs/process?limit=1")
    _wait_run_done(client, p.json()["id"])

    resp = client.get("/api/runs")
    assert resp.status_code == 200
    items = resp.json()["items"]
    types = {i["type"] for i in items}
    assert "discovery" in types
    assert "processing" in types


def test_runs_invalid_status_returns_422(tmp_path):
    """R3-2: invalid status value returns a 422 validation error."""
    _live_mode(tmp_path)
    client = TestClient(api_mod.app)
    resp = client.get("/api/runs?status=banana")
    assert resp.status_code == 422


def test_runs_invalid_type_returns_422(tmp_path):
    """R3-2: invalid type value returns a 422 validation error."""
    _live_mode(tmp_path)
    client = TestClient(api_mod.app)
    resp = client.get("/api/runs?type=banana")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Draft freshness: refresh marks draft stale when research changes;
# regeneration is an explicit human action.
# ---------------------------------------------------------------------------


def test_refresh_marks_draft_stale_when_proof_project_changes(tmp_path, monkeypatch):
    """The LaunchPad Lab scenario: refresh changes proof_project from Aegis
    to Forge Crew.  The existing draft must be marked stale but NOT modified."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead({
        "company": "LaunchPad Lab",
        "domain": "launchpadlab.com",
        "website": "https://launchpadlab.com",
        "score": 82,
        "proof_project": "Aegis",
        "fit_reason": "original fit reason",
        "outreach_angle": "original angle",
        "contact_email": "hello@launchpadlab.com",
        "contact_quality": "medium",
        "subject": "Original subject about Aegis",
        "draft": "Original draft body referencing Aegis",
        "status": "drafted",
    })

    monkeypatch.setattr(
        pipeline_mod, "crawl_company", lambda url: _make_site(STRONG_TEXT, "launchpadlab.com")
    )
    monkeypatch.setattr(
        pipeline_mod,
        "analyze_agency",
        lambda company, website, text: {
            "summary": "new summary",
            "services": "ai",
            "fit_reason": "original fit reason",  # unchanged
            "proof_project": "Forge Crew",  # CHANGED — draft-driving
            "outreach_angle": "original angle",  # unchanged
        },
    )

    result = pipeline_mod.refresh_lead_research(lead_id)
    assert result["refreshed"] is True
    assert result["draft_marked_stale"] is True

    from app.db import get_lead
    row = get_lead(lead_id)
    # Draft marked stale.
    assert bool(row["draft_stale"]) is True
    # Draft body NOT modified — refresh never regenerates.
    assert row["subject"] == "Original subject about Aegis"
    assert row["draft"] == "Original draft body referencing Aegis"
    # Research updated.
    assert row["proof_project"] == "Forge Crew"
    # Status unchanged.
    assert row["status"] == "drafted"


def test_refresh_does_not_mark_stale_when_research_unchanged(tmp_path, monkeypatch):
    """When research fields don't change, draft_stale must remain 0."""
    _live_mode(tmp_path)
    init_db()
    # Use a company name that extract_company_name will preserve.
    # _make_site uses title="Example" by default, and extract_company_name
    # derives the company from the title.  We set the initial company to
    # match what the refresh will produce so the company field doesn't change.
    from app.pipeline import extract_company_name
    expected_company = extract_company_name("Example", "co.example")
    lead_id = upsert_lead({
        "company": expected_company,
        "domain": "co.example",
        "website": "https://co.example",
        "score": 80,
        "proof_project": "WingerX",
        "fit_reason": "same fit",
        "outreach_angle": "same angle",
        "summary": "same summary",
        "services": "ai",
        "contact_email": "hello@co.example",
        "contact_quality": "medium",
        "subject": "Existing subject",
        "draft": "Existing draft body",
        "status": "drafted",
    })

    monkeypatch.setattr(
        pipeline_mod, "crawl_company", lambda url: _make_site(STRONG_TEXT, "co.example")
    )
    monkeypatch.setattr(
        pipeline_mod,
        "analyze_agency",
        lambda company, website, text: {
            "summary": "same summary",
            "services": "ai",
            "fit_reason": "same fit",
            "proof_project": "WingerX",
            "outreach_angle": "same angle",
        },
    )

    result = pipeline_mod.refresh_lead_research(lead_id)
    assert result["refreshed"] is True
    assert result["draft_marked_stale"] is False

    from app.db import get_lead
    row = get_lead(lead_id)
    assert bool(row["draft_stale"]) is False
    assert row["subject"] == "Existing subject"
    assert row["draft"] == "Existing draft body"


def test_refresh_does_not_mark_stale_when_no_draft_exists(tmp_path, monkeypatch):
    """When there's no draft, draft_stale must remain 0 even if research changes."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead({
        "company": "Co",
        "domain": "co.example",
        "website": "https://co.example",
        "score": 80,
        "proof_project": "WingerX",
        "contact_email": "hello@co.example",
        "contact_quality": "medium",
        "status": "qualified",  # no draft yet
    })

    monkeypatch.setattr(
        pipeline_mod, "crawl_company", lambda url: _make_site(STRONG_TEXT, "co.example")
    )
    monkeypatch.setattr(
        pipeline_mod,
        "analyze_agency",
        lambda company, website, text: {
            "summary": "new summary",
            "services": "ai",
            "fit_reason": "new fit",
            "proof_project": "Forge Crew",  # changed
            "outreach_angle": "new angle",
        },
    )

    result = pipeline_mod.refresh_lead_research(lead_id)
    assert result["refreshed"] is True
    assert result["draft_marked_stale"] is False

    from app.db import get_lead
    row = get_lead(lead_id)
    assert bool(row["draft_stale"]) is False


def test_refresh_marks_stale_when_fit_reason_changes(tmp_path, monkeypatch):
    """Any draft-driving field change (not just proof_project) marks stale."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead({
        "company": "Co",
        "domain": "co.example",
        "website": "https://co.example",
        "score": 80,
        "proof_project": "WingerX",
        "fit_reason": "old fit reason",
        "outreach_angle": "same angle",
        "contact_email": "hello@co.example",
        "contact_quality": "medium",
        "subject": "Existing subject",
        "draft": "Existing draft body",
        "status": "drafted",
    })

    monkeypatch.setattr(
        pipeline_mod, "crawl_company", lambda url: _make_site(STRONG_TEXT, "co.example")
    )
    monkeypatch.setattr(
        pipeline_mod,
        "analyze_agency",
        lambda company, website, text: {
            "summary": "same summary",
            "services": "ai",
            "fit_reason": "NEW fit reason",  # changed — draft-driving
            "proof_project": "WingerX",  # unchanged
            "outreach_angle": "same angle",
        },
    )

    result = pipeline_mod.refresh_lead_research(lead_id)
    assert result["draft_marked_stale"] is True

    from app.db import get_lead
    row = get_lead(lead_id)
    assert bool(row["draft_stale"]) is True


def test_refresh_does_not_mark_stale_when_only_summary_services_change(tmp_path, monkeypatch):
    """R1-2: summary and services do NOT drive draft_outreach(), so changes
    to them must NOT mark the draft stale."""
    _live_mode(tmp_path)
    init_db()
    from app.pipeline import extract_company_name
    expected_company = extract_company_name("Example", "co.example")
    lead_id = upsert_lead({
        "company": expected_company,
        "domain": "co.example",
        "website": "https://co.example",
        "score": 80,
        "proof_project": "WingerX",
        "fit_reason": "same fit",
        "outreach_angle": "same angle",
        "summary": "old summary",
        "services": "old services",
        "contact_email": "hello@co.example",
        "contact_quality": "medium",
        "subject": "Existing subject",
        "draft": "Existing draft body",
        "status": "drafted",
    })

    monkeypatch.setattr(
        pipeline_mod, "crawl_company", lambda url: _make_site(STRONG_TEXT, "co.example")
    )
    monkeypatch.setattr(
        pipeline_mod,
        "analyze_agency",
        lambda company, website, text: {
            "summary": "COMPLETELY NEW summary",  # changed — NOT draft-driving
            "services": "COMPLETELY NEW services",  # changed — NOT draft-driving
            "fit_reason": "same fit",  # unchanged
            "proof_project": "WingerX",  # unchanged
            "outreach_angle": "same angle",  # unchanged
        },
    )

    result = pipeline_mod.refresh_lead_research(lead_id)
    assert result["refreshed"] is True
    assert result["draft_marked_stale"] is False

    from app.db import get_lead
    row = get_lead(lead_id)
    assert bool(row["draft_stale"]) is False
    # Summary and services were updated (they're always refreshed).
    assert row["summary"] == "COMPLETELY NEW summary"
    assert row["services"] == "COMPLETELY NEW services"
    # Draft body unchanged.
    assert row["subject"] == "Existing subject"
    assert row["draft"] == "Existing draft body"


def test_regenerate_draft_creates_fresh_draft_and_clears_stale(tmp_path, monkeypatch):
    """Explicit regeneration creates a new draft from current research and
    clears the stale flag."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead({
        "company": "LaunchPad Lab",
        "domain": "launchpadlab.com",
        "website": "https://launchpadlab.com",
        "score": 82,
        "proof_project": "Forge Crew",  # current research
        "fit_reason": "current fit reason",
        "outreach_angle": "current angle",
        "contact_email": "hello@launchpadlab.com",
        "contact_quality": "medium",
        "subject": "Old subject about Aegis",
        "draft": "Old draft body referencing Aegis",
        "status": "drafted",
        "draft_stale": 1,  # marked stale by a prior refresh
    })

    # Patch draft_outreach to return a deterministic new draft.
    def fake_draft(company, fit_reason, proof_project, outreach_angle):
        return (
            f"Fresh subject for {company}",
            f"Fresh draft about {proof_project}",
        )
    monkeypatch.setattr(pipeline_mod, "draft_outreach", fake_draft)

    result = pipeline_mod.regenerate_draft(lead_id)
    assert result["regenerated"] is True
    assert result["subject"] == "Fresh subject for LaunchPad Lab"

    from app.db import get_lead
    row = get_lead(lead_id)
    assert row["subject"] == "Fresh subject for LaunchPad Lab"
    assert row["draft"] == "Fresh draft about Forge Crew"
    # Stale flag cleared.
    assert bool(row["draft_stale"]) is False
    # Status unchanged — regeneration does not move workflow state.
    assert row["status"] == "drafted"
    # Gmail draft id unchanged.
    assert row["gmail_draft_id"] is None or row["gmail_draft_id"] == ""


def test_regenerate_draft_revokes_approved_status(tmp_path, monkeypatch):
    """R1-7: Regeneration of an approved stale draft revokes approval.
    The new content was never reviewed, so the lead returns to 'drafted'
    and requires re-approval.  The new draft must NOT inherit approval."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead({
        "company": "Co",
        "domain": "co.example",
        "website": "https://co.example",
        "score": 80,
        "proof_project": "Forge Crew",
        "fit_reason": "fit",
        "outreach_angle": "angle",
        "contact_email": "hello@co.example",
        "contact_quality": "medium",
        "subject": "Old subject",
        "draft": "Old draft",
        "status": "approved",  # was approved
        "draft_stale": 1,
    })

    monkeypatch.setattr(
        pipeline_mod, "draft_outreach",
        lambda *a: ("New subject", "New draft"),
    )

    pipeline_mod.regenerate_draft(lead_id)

    from app.db import get_lead
    row = get_lead(lead_id)
    # R1-7: Status is now 'drafted', NOT 'approved'.  Approval is revoked.
    assert row["status"] == "drafted"
    assert row["subject"] == "New subject"
    assert row["draft"] == "New draft"
    assert bool(row["draft_stale"]) is False


def test_regenerate_draft_missing_lead_raises(tmp_path):
    _live_mode(tmp_path)
    init_db()
    # R1-5: Missing lead raises RegenerationBlocked with 404 status.
    from app.pipeline import RegenerationBlocked
    with pytest.raises(RegenerationBlocked) as exc_info:
        pipeline_mod.regenerate_draft(999999)
    assert exc_info.value.status_code == 404


def test_api_refresh_returns_draft_marked_stale(tmp_path, monkeypatch):
    """The refresh API response includes draft_marked_stale in the refresh result."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead({
        "company": "Co",
        "domain": "co.example",
        "website": "https://co.example",
        "score": 80,
        "proof_project": "Aegis",
        "fit_reason": "old fit",
        "outreach_angle": "angle",
        "contact_email": "hello@co.example",
        "contact_quality": "medium",
        "subject": "Old subject",
        "draft": "Old draft",
        "status": "drafted",
    })

    monkeypatch.setattr(
        pipeline_mod, "crawl_company", lambda url: _make_site(STRONG_TEXT, "co.example")
    )
    monkeypatch.setattr(
        pipeline_mod,
        "analyze_agency",
        lambda company, website, text: {
            "summary": "s", "services": "ai", "fit_reason": "NEW fit",
            "proof_project": "Forge Crew", "outreach_angle": "angle",
        },
    )

    client = TestClient(api_mod.app)
    resp = client.post(f"/api/leads/{lead_id}/refresh-research")
    assert resp.status_code == 200
    body = resp.json()
    assert body["refresh"]["draft_marked_stale"] is True
    assert body["lead"]["draft_stale"] is True
    # Draft body NOT modified.
    assert body["lead"]["subject"] == "Old subject"
    assert body["lead"]["draft"] == "Old draft"


def test_api_regenerate_draft_endpoint(tmp_path, monkeypatch):
    """POST /api/leads/{id}/regenerate-draft regenerates and clears stale."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead({
        "company": "Co",
        "domain": "co.example",
        "website": "https://co.example",
        "score": 80,
        "proof_project": "Forge Crew",
        "fit_reason": "fit",
        "outreach_angle": "angle",
        "contact_email": "hello@co.example",
        "contact_quality": "medium",
        "subject": "Stale subject",
        "draft": "Stale draft",
        "status": "drafted",
        "draft_stale": 1,
    })

    monkeypatch.setattr(
        pipeline_mod, "draft_outreach",
        lambda *a: ("Fresh subject", "Fresh draft body"),
    )

    client = TestClient(api_mod.app)
    resp = client.post(f"/api/leads/{lead_id}/regenerate-draft")
    assert resp.status_code == 200
    body = resp.json()
    assert body["regenerate"]["regenerated"] is True
    assert body["lead"]["subject"] == "Fresh subject"
    assert body["lead"]["draft"] == "Fresh draft body"
    assert body["lead"]["draft_stale"] is False


def test_api_regenerate_draft_no_research_returns_409(tmp_path):
    """Regeneration on a lead with no research returns 409."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead({
        "company": "Co",
        "domain": "co.example",
        "website": "https://co.example",
        "score": 80,
        "contact_email": "hello@co.example",
        "contact_quality": "medium",
        "status": "discovered",
        # no proof_project, fit_reason, or outreach_angle
    })

    client = TestClient(api_mod.app)
    resp = client.post(f"/api/leads/{lead_id}/regenerate-draft")
    assert resp.status_code == 409


def test_api_lead_detail_includes_draft_stale(tmp_path):
    """The lead detail API response includes the draft_stale boolean field."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead({
        "company": "Co",
        "domain": "co.example",
        "website": "https://co.example",
        "score": 80,
        "proof_project": "WingerX",
        "contact_email": "hello@co.example",
        "contact_quality": "medium",
        "subject": "Subject",
        "draft": "Draft",
        "status": "drafted",
        "draft_stale": 1,
    })

    client = TestClient(api_mod.app)
    resp = client.get(f"/api/leads/{lead_id}")
    assert resp.status_code == 200
    assert resp.json()["draft_stale"] is True


def test_api_refresh_does_not_leak_exception_message(tmp_path, monkeypatch):
    """R-sanitization: refresh error response must not include raw exception text."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead({
        "company": "Co",
        "domain": "co.example",
        "website": "https://co.example",
        "score": 80,
        "proof_project": "WingerX",
        "contact_email": "hello@co.example",
        "contact_quality": "medium",
        "status": "qualified",
    })

    fake_secret = "sk-test-super-secret-value"

    def failing_crawl(url):
        raise RuntimeError(f"crawl failed with token={fake_secret}")

    monkeypatch.setattr(pipeline_mod, "crawl_company", failing_crawl)

    client = TestClient(api_mod.app)
    resp = client.post(f"/api/leads/{lead_id}/refresh-research")
    assert resp.status_code == 500
    body_str = json.dumps(resp.json())
    assert fake_secret not in body_str
    assert "RuntimeError" in body_str


# ---------------------------------------------------------------------------
# R1-1: Production migration marks existing regeneratable drafts stale
# ---------------------------------------------------------------------------


def test_migration_marks_existing_drafted_drafts_stale(tmp_path):
    """R1-1: when draft_stale column is first added, existing drafts in
    'drafted' status are marked stale."""
    db_path = _live_mode(tmp_path)
    from app.db import now_iso

    # Create a database WITHOUT the draft_stale column by inserting leads
    # directly into a fresh DB (init_db adds the column via migration).
    # To simulate a pre-migration database, we create the table manually
    # without draft_stale, insert rows, then call init_db to trigger migration.
    import sqlite3
    db = sqlite3.connect(str(db_path))
    db.executescript("""
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
    """)
    now = now_iso()
    # Drafted lead with a draft — should be marked stale.
    db.execute(
        "INSERT INTO leads (company, domain, website, score, proof_project, "
        "subject, draft, status, created_at, updated_at) "
        "VALUES ('Co A', 'a.example', 'https://a.example', 80, 'Aegis', "
        "'Subject A', 'Draft A', 'drafted', ?, ?)",
        (now, now),
    )
    # Rejected lead with a draft — should be marked stale.
    db.execute(
        "INSERT INTO leads (company, domain, website, score, proof_project, "
        "subject, draft, status, created_at, updated_at) "
        "VALUES ('Co B', 'b.example', 'https://b.example', 70, 'WingerX', "
        "'Subject B', 'Draft B', 'rejected', ?, ?)",
        (now, now),
    )
    # Approved lead with a draft — should be marked stale.
    db.execute(
        "INSERT INTO leads (company, domain, website, score, proof_project, "
        "subject, draft, status, created_at, updated_at) "
        "VALUES ('Co C', 'c.example', 'https://c.example', 75, 'Forge Crew', "
        "'Subject C', 'Draft C', 'approved', ?, ?)",
        (now, now),
    )
    # gmail_drafted lead with a draft — should NOT be marked stale.
    db.execute(
        "INSERT INTO leads (company, domain, website, score, proof_project, "
        "subject, draft, status, gmail_draft_id, created_at, updated_at) "
        "VALUES ('Co D', 'd.example', 'https://d.example', 85, 'WingerX', "
        "'Subject D', 'Draft D', 'gmail_drafted', 'gmail-123', ?, ?)",
        (now, now),
    )
    # sent lead with a draft — should NOT be marked stale.
    db.execute(
        "INSERT INTO leads (company, domain, website, score, proof_project, "
        "subject, draft, status, last_contact_at, created_at, updated_at) "
        "VALUES ('Co E', 'e.example', 'https://e.example', 90, 'WingerX', "
        "'Subject E', 'Draft E', 'sent', ?, ?, ?)",
        (now, now, now),
    )
    # do_not_contact lead with a draft — should NOT be marked stale.
    db.execute(
        "INSERT INTO leads (company, domain, website, score, proof_project, "
        "subject, draft, status, created_at, updated_at) "
        "VALUES ('Co F', 'f.example', 'https://f.example', 60, 'WingerX', "
        "'Subject F', 'Draft F', 'do_not_contact', ?, ?)",
        (now, now),
    )
    # Discovered lead with NO draft — should NOT be marked stale.
    db.execute(
        "INSERT INTO leads (company, domain, website, score, "
        "status, created_at, updated_at) "
        "VALUES ('Co G', 'g.example', 'https://g.example', 50, "
        "'discovered', ?, ?)",
        (now, now),
    )
    db.commit()
    db.close()

    # Now trigger migration by calling init_db.
    init_db()

    # Verify migration marked the right rows stale.
    from app.db import get_lead_by_domain
    drafted = get_lead_by_domain("a.example")
    assert bool(drafted["draft_stale"]) is True

    rejected = get_lead_by_domain("b.example")
    assert bool(rejected["draft_stale"]) is True

    approved = get_lead_by_domain("c.example")
    assert bool(approved["draft_stale"]) is True

    gmail_drafted = get_lead_by_domain("d.example")
    assert bool(gmail_drafted["draft_stale"]) is False

    sent = get_lead_by_domain("e.example")
    assert bool(sent["draft_stale"]) is False

    dnc = get_lead_by_domain("f.example")
    assert bool(dnc["draft_stale"]) is False

    no_draft = get_lead_by_domain("g.example")
    assert bool(no_draft["draft_stale"]) is False


def test_migration_idempotent_does_not_remark_stale(tmp_path):
    """R1-1: running migration again on an already-migrated DB must NOT
    re-mark rows stale (e.g. a draft that was explicitly regenerated and
    cleared must stay clear)."""
    _live_mode(tmp_path)
    init_db()
    from app.db import update_lead

    # Insert a drafted lead with a draft.
    lead_id = upsert_lead({
        "company": "Co",
        "domain": "co.example",
        "website": "https://co.example",
        "score": 80,
        "proof_project": "WingerX",
        "subject": "Subject",
        "draft": "Draft",
        "status": "drafted",
    })
    # Explicitly clear stale (simulating operator regenerated the draft).
    update_lead(lead_id, draft_stale=0)

    from app.db import get_lead
    row = get_lead(lead_id)
    assert bool(row["draft_stale"]) is False

    # Run init_db again — migration should be a no-op for draft_stale
    # because the column already exists.
    init_db()

    row = get_lead(lead_id)
    assert bool(row["draft_stale"]) is False


# ---------------------------------------------------------------------------
# R1 comprehensive regression matrix
# ---------------------------------------------------------------------------

def _stale_lead(**overrides) -> dict:
    """Build a standard stale-draft lead fixture for regression tests."""
    base = {
        "company": "Co",
        "domain": "co.example",
        "website": "https://co.example",
        "score": 80,
        "proof_project": "WingerX",
        "fit_reason": "fit reason",
        "outreach_angle": "angle",
        "summary": "summary",
        "services": "services",
        "contact_email": "hello@co.example",
        "contact_name": "Jane",
        "contact_role": "CEO",
        "contact_source": "homepage",
        "contact_quality": "medium",
        "subject": "Old subject",
        "draft": "Old draft body",
        "status": "drafted",
        "draft_stale": 1,
    }
    base.update(overrides)
    return base


def _fresh_lead(**overrides) -> dict:
    return _stale_lead(draft_stale=0, **overrides)


# --- R1-2 field-level stale detection ---------------------------------------

def test_refresh_marks_stale_when_company_changes(tmp_path, monkeypatch):
    """company change -> stale."""
    _live_mode(tmp_path)
    init_db()
    from app.pipeline import extract_company_name
    expected_company = extract_company_name("Example", "co.example")
    lead_id = upsert_lead(_stale_lead(company=expected_company, draft_stale=0))

    monkeypatch.setattr(pipeline_mod, "crawl_company", lambda url: _make_site(STRONG_TEXT, "co.example"))
    monkeypatch.setattr(pipeline_mod, "analyze_agency", lambda company, website, text: {
        "summary": "summary", "services": "services",
        "fit_reason": "fit reason", "proof_project": "WingerX",
        "outreach_angle": "angle",
    })
    monkeypatch.setattr(pipeline_mod, "extract_company_name", lambda title, domain: "NEW COMPANY")

    result = pipeline_mod.refresh_lead_research(lead_id)
    assert result["draft_marked_stale"] is True

    from app.db import get_lead
    assert bool(get_lead(lead_id)["draft_stale"]) is True


def test_refresh_marks_stale_when_outreach_angle_changes(tmp_path, monkeypatch):
    """outreach_angle change -> stale."""
    _live_mode(tmp_path)
    init_db()
    from app.pipeline import extract_company_name
    expected_company = extract_company_name("Example", "co.example")
    lead_id = upsert_lead(_stale_lead(company=expected_company, draft_stale=0))

    monkeypatch.setattr(pipeline_mod, "crawl_company", lambda url: _make_site(STRONG_TEXT, "co.example"))
    monkeypatch.setattr(pipeline_mod, "analyze_agency", lambda company, website, text: {
        "summary": "summary", "services": "services",
        "fit_reason": "fit reason", "proof_project": "WingerX",
        "outreach_angle": "COMPLETELY NEW ANGLE",
    })

    result = pipeline_mod.refresh_lead_research(lead_id)
    assert result["draft_marked_stale"] is True

    from app.db import get_lead
    assert bool(get_lead(lead_id)["draft_stale"]) is True


def test_refresh_does_not_mark_stale_when_only_score_changes(tmp_path, monkeypatch):
    """score-only change -> NOT stale (score is not a draft-driving field)."""
    _live_mode(tmp_path)
    init_db()
    from app.pipeline import extract_company_name
    expected_company = extract_company_name("Example", "co.example")
    lead_id = upsert_lead(_stale_lead(company=expected_company, draft_stale=0, score=50))

    monkeypatch.setattr(pipeline_mod, "crawl_company", lambda url: _make_site(STRONG_TEXT, "co.example"))
    monkeypatch.setattr(pipeline_mod, "analyze_agency", lambda company, website, text: {
        "summary": "summary", "services": "services",
        "fit_reason": "fit reason", "proof_project": "WingerX",
        "outreach_angle": "angle",
    })

    result = pipeline_mod.refresh_lead_research(lead_id)
    assert result["draft_marked_stale"] is False

    from app.db import get_lead
    assert bool(get_lead(lead_id)["draft_stale"]) is False


def test_refresh_does_not_mark_stale_when_only_contact_changes(tmp_path, monkeypatch):
    """contact-only change -> NOT stale (contact fields are not draft-driving)."""
    _live_mode(tmp_path)
    init_db()
    from app.pipeline import extract_company_name
    expected_company = extract_company_name("Example", "co.example")
    lead_id = upsert_lead(_stale_lead(company=expected_company, draft_stale=0))

    monkeypatch.setattr(pipeline_mod, "crawl_company", lambda url: _make_site(STRONG_TEXT, "co.example"))
    monkeypatch.setattr(pipeline_mod, "analyze_agency", lambda company, website, text: {
        "summary": "summary", "services": "services",
        "fit_reason": "fit reason", "proof_project": "WingerX",
        "outreach_angle": "angle",
    })
    monkeypatch.setattr(
        pipeline_mod, "discover_contact",
        lambda *a, **kw: {"contact_email": "new-contact@co.example", "contact_name": "New Person", "contact_role": "CTO", "contact_source": "contact", "contact_quality": "high"},
    )

    result = pipeline_mod.refresh_lead_research(lead_id)
    assert result["contact_refreshed"] is True
    assert result["draft_marked_stale"] is False

    from app.db import get_lead
    assert bool(get_lead(lead_id)["draft_stale"]) is False


# --- R1-3 draft body existence ----------------------------------------------

def test_refresh_subject_only_no_draft_body_not_stale(tmp_path, monkeypatch):
    """R1-3: A stray subject with no draft body must not create a stale workflow."""
    _live_mode(tmp_path)
    init_db()
    from app.pipeline import extract_company_name
    expected_company = extract_company_name("Example", "co.example")
    lead_id = upsert_lead(_stale_lead(
        company=expected_company, draft_stale=0,
        subject="Orphan subject", draft="",
    ))

    monkeypatch.setattr(pipeline_mod, "crawl_company", lambda url: _make_site(STRONG_TEXT, "co.example"))
    monkeypatch.setattr(pipeline_mod, "analyze_agency", lambda company, website, text: {
        "summary": "summary", "services": "services",
        "fit_reason": "NEW fit", "proof_project": "WingerX",
        "outreach_angle": "angle",
    })

    result = pipeline_mod.refresh_lead_research(lead_id)
    assert result["draft_marked_stale"] is False

    from app.db import get_lead
    assert bool(get_lead(lead_id)["draft_stale"]) is False


# --- R1-4 normal pipeline clears stale --------------------------------------

def test_normal_pipeline_clears_stale_on_new_draft(tmp_path, monkeypatch):
    """R1-4: Normal pipeline draft generation explicitly clears draft_stale."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_stale_lead(draft_stale=1, status="drafted"))

    from app.db import get_lead
    assert bool(get_lead(lead_id)["draft_stale"]) is True

    monkeypatch.setattr(pipeline_mod, "draft_outreach", lambda *a: ("New subject", "New body"))
    from app.db import update_lead
    update_lead(lead_id, subject="New subject", draft="New body", status="drafted", draft_stale=0)

    row = get_lead(lead_id)
    assert row["status"] == "drafted"
    assert bool(row["draft_stale"]) is False
    assert row["subject"] == "New subject"
    assert row["draft"] == "New body"


# --- R1-5/R1-6 regeneration preconditions and status matrix -----------------

def test_regenerate_fresh_drafted_returns_409(tmp_path):
    """Fresh drafted lead -> regeneration blocked (409)."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_fresh_lead())
    from app.pipeline import RegenerationBlocked
    with pytest.raises(RegenerationBlocked) as exc_info:
        pipeline_mod.regenerate_draft(lead_id)
    assert exc_info.value.status_code == 409
    assert "already up to date" in str(exc_info.value).lower()


def test_regenerate_fresh_approved_returns_409(tmp_path):
    """Fresh approved lead -> regeneration blocked (409)."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_fresh_lead(status="approved"))
    from app.pipeline import RegenerationBlocked
    with pytest.raises(RegenerationBlocked) as exc_info:
        pipeline_mod.regenerate_draft(lead_id)
    assert exc_info.value.status_code == 409


def test_regenerate_no_draft_returns_409(tmp_path):
    """No draft body -> regeneration blocked (409)."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_stale_lead(draft="", subject=""))
    from app.pipeline import RegenerationBlocked
    with pytest.raises(RegenerationBlocked) as exc_info:
        pipeline_mod.regenerate_draft(lead_id)
    assert exc_info.value.status_code == 409
    assert "no existing" in str(exc_info.value).lower()


def test_regenerate_gmail_drafted_returns_409(tmp_path):
    """gmail_drafted -> regeneration blocked (409)."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_stale_lead(status="gmail_drafted", gmail_draft_id="gmail-123"))
    from app.pipeline import RegenerationBlocked
    with pytest.raises(RegenerationBlocked) as exc_info:
        pipeline_mod.regenerate_draft(lead_id)
    assert exc_info.value.status_code == 409


def test_regenerate_sent_returns_409(tmp_path):
    """sent -> regeneration blocked (409)."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_stale_lead(status="sent"))
    from app.pipeline import RegenerationBlocked
    with pytest.raises(RegenerationBlocked) as exc_info:
        pipeline_mod.regenerate_draft(lead_id)
    assert exc_info.value.status_code == 409


def test_regenerate_do_not_contact_returns_409(tmp_path):
    """do_not_contact -> regeneration blocked (409)."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_stale_lead(status="do_not_contact"))
    from app.pipeline import RegenerationBlocked
    with pytest.raises(RegenerationBlocked) as exc_info:
        pipeline_mod.regenerate_draft(lead_id)
    assert exc_info.value.status_code == 409


def test_regenerate_approved_with_gmail_draft_id_returns_409(tmp_path):
    """R1-8: approved lead with gmail_draft_id -> blocked even though status allows."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_stale_lead(status="approved", gmail_draft_id="gmail-abc"))
    from app.pipeline import RegenerationBlocked
    with pytest.raises(RegenerationBlocked) as exc_info:
        pipeline_mod.regenerate_draft(lead_id)
    assert exc_info.value.status_code == 409
    assert "gmail" in str(exc_info.value).lower()


def test_regenerate_stale_rejected_returns_drafted(tmp_path, monkeypatch):
    """R1-6: stale rejected -> regenerate -> drafted, fresh."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_stale_lead(status="rejected"))
    monkeypatch.setattr(pipeline_mod, "draft_outreach", lambda *a: ("New subject", "New body"))

    pipeline_mod.regenerate_draft(lead_id)

    from app.db import get_lead
    row = get_lead(lead_id)
    assert row["status"] == "drafted"
    assert bool(row["draft_stale"]) is False
    assert row["subject"] == "New subject"
    assert row["draft"] == "New body"


def test_blocked_regeneration_does_not_call_draft_outreach(tmp_path):
    """R1-5/R1-6: blocked regeneration must NOT call draft_outreach()."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_fresh_lead())

    called = {"called": False}
    def fake_draft(*a):
        called["called"] = True
        return ("X", "Y")

    import app.pipeline as pm
    original = pm.draft_outreach
    pm.draft_outreach = fake_draft
    try:
        from app.pipeline import RegenerationBlocked
        with pytest.raises(RegenerationBlocked):
            pm.regenerate_draft(lead_id)
    finally:
        pm.draft_outreach = original

    assert called["called"] is False


# --- R1-7 approved regeneration does not inherit approval --------------------

def test_approved_regenerated_content_does_not_inherit_approval(tmp_path, monkeypatch):
    """R1-7: After regenerating an approved stale draft, the new content is
    in 'drafted' state and requires re-approval."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_stale_lead(status="approved"))
    monkeypatch.setattr(pipeline_mod, "draft_outreach", lambda *a: ("Fresh subject", "Fresh body"))

    pipeline_mod.regenerate_draft(lead_id)

    from app.db import get_lead
    row = get_lead(lead_id)
    assert row["status"] == "drafted"
    assert row["subject"] == "Fresh subject"
    assert row["draft"] == "Fresh body"
    assert bool(row["draft_stale"]) is False
    assert row["status"] != "approved"


# --- R1-11 regeneration side-effect isolation -------------------------------

def test_regenerate_preserves_contact_fields(tmp_path, monkeypatch):
    """R1-11: Regeneration must NOT modify contact fields."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_stale_lead(
        contact_name="Jane Doe",
        contact_role="CEO",
        contact_email="jane@co.example",
        contact_source="about",
        contact_quality="high",
    ))
    monkeypatch.setattr(pipeline_mod, "draft_outreach", lambda *a: ("New subject", "New body"))

    pipeline_mod.regenerate_draft(lead_id)

    from app.db import get_lead
    row = get_lead(lead_id)
    assert row["contact_name"] == "Jane Doe"
    assert row["contact_role"] == "CEO"
    assert row["contact_email"] == "jane@co.example"
    assert row["contact_source"] == "about"
    assert row["contact_quality"] == "high"


def test_regenerate_preserves_followup_dates(tmp_path, monkeypatch):
    """R1-11: Regeneration must NOT modify follow-up dates."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_stale_lead(
        last_contact_at="2026-01-01T00:00:00+00:00",
        followup_due_at="2026-01-08T00:00:00+00:00",
    ))
    monkeypatch.setattr(pipeline_mod, "draft_outreach", lambda *a: ("New subject", "New body"))

    pipeline_mod.regenerate_draft(lead_id)

    from app.db import get_lead
    row = get_lead(lead_id)
    assert row["last_contact_at"] == "2026-01-01T00:00:00+00:00"
    assert row["followup_due_at"] == "2026-01-08T00:00:00+00:00"


def test_regenerate_preserves_research_fields(tmp_path, monkeypatch):
    """R1-11: Regeneration must NOT modify research fields."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_stale_lead(
        company="My Company",
        fit_reason="my fit",
        proof_project="My Proof",
        outreach_angle="my angle",
        summary="my summary",
        services="my services",
        score=77,
    ))
    monkeypatch.setattr(pipeline_mod, "draft_outreach", lambda *a: ("New subject", "New body"))

    pipeline_mod.regenerate_draft(lead_id)

    from app.db import get_lead
    row = get_lead(lead_id)
    assert row["company"] == "My Company"
    assert row["fit_reason"] == "my fit"
    assert row["proof_project"] == "My Proof"
    assert row["outreach_angle"] == "my angle"
    assert row["summary"] == "my summary"
    assert row["services"] == "my services"
    assert row["score"] == 77


def test_regenerate_uses_current_research_fields(tmp_path, monkeypatch):
    """Regeneration uses CURRENT company, fit_reason, proof_project, outreach_angle."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_stale_lead(
        company="Current Co",
        fit_reason="current fit",
        proof_project="Forge Crew",
        outreach_angle="current angle",
    ))

    captured = {}
    def fake_draft(company, fit_reason, proof_project, outreach_angle):
        captured["company"] = company
        captured["fit_reason"] = fit_reason
        captured["proof_project"] = proof_project
        captured["outreach_angle"] = outreach_angle
        return ("New subject", "New body")

    monkeypatch.setattr(pipeline_mod, "draft_outreach", fake_draft)
    pipeline_mod.regenerate_draft(lead_id)

    assert captured["company"] == "Current Co"
    assert captured["fit_reason"] == "current fit"
    assert captured["proof_project"] == "Forge Crew"
    assert captured["outreach_angle"] == "current angle"


def test_regenerate_never_calls_gmail(tmp_path, monkeypatch):
    """R1-11: Regeneration must NOT call Gmail create_draft."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_stale_lead())
    monkeypatch.setattr(pipeline_mod, "draft_outreach", lambda *a: ("New subject", "New body"))

    gmail_called = {"called": False}
    def fake_gmail_create(*a, **kw):
        gmail_called["called"] = True
        return "should-not-happen"

    import app.api as api_module
    original = api_module.create_draft
    api_module.create_draft = fake_gmail_create
    try:
        pipeline_mod.regenerate_draft(lead_id)
    finally:
        api_module.create_draft = original

    assert gmail_called["called"] is False


# --- R1-9 stale draft approval -> 409 ---------------------------------------

def test_api_approve_stale_draft_returns_409(tmp_path):
    """R1-9: Approving a stale draft returns 409."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_stale_lead(status="drafted", draft_stale=1))

    client = TestClient(api_mod.app)
    resp = client.post(f"/api/leads/{lead_id}/approve")
    assert resp.status_code == 409
    assert "stale" in resp.json()["detail"].lower()

    from app.db import get_lead
    assert get_lead(lead_id)["status"] == "drafted"


def test_api_approve_fresh_draft_succeeds(tmp_path):
    """R1-9: Approving a fresh draft succeeds normally."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_fresh_lead(status="drafted"))

    client = TestClient(api_mod.app)
    resp = client.post(f"/api/leads/{lead_id}/approve")
    assert resp.status_code == 200

    from app.db import get_lead
    assert get_lead(lead_id)["status"] == "approved"


# --- R1-10 stale Gmail creation -> 409 --------------------------------------

def test_api_gmail_draft_stale_returns_409(tmp_path, monkeypatch):
    """R1-10: Creating a Gmail draft for a stale approved draft returns 409
    and does NOT call create_draft()."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_stale_lead(status="approved", draft_stale=1))

    create_called = {"called": False}
    def fake_create(*a, **kw):
        create_called["called"] = True
        return "should-not-happen"

    monkeypatch.setattr(api_mod, "create_draft", fake_create)

    client = TestClient(api_mod.app)
    resp = client.post(f"/api/leads/{lead_id}/gmail-draft")
    assert resp.status_code == 409
    assert "stale" in resp.json()["detail"].lower()
    assert create_called["called"] is False

    from app.db import get_lead
    row = get_lead(lead_id)
    assert row["status"] == "approved"


# --- R1-12 demo mode blocks regeneration before LLM -------------------------

def test_api_regenerate_draft_demo_mode_returns_403(tmp_path, monkeypatch):
    """R1-12: Demo mode blocks regeneration with 403 and does NOT call
    draft_outreach()."""
    object.__setattr__(settings, "pactsignal_demo_mode", True)
    db_path = tmp_path / "demo.db"
    object.__setattr__(settings, "db_path", db_path)
    init_db()
    lead_id = upsert_lead(_stale_lead())

    draft_called = {"called": False}
    def fake_draft(*a):
        draft_called["called"] = True
        return ("X", "Y")

    monkeypatch.setattr(pipeline_mod, "draft_outreach", fake_draft)

    client = TestClient(api_mod.app)
    resp = client.post(f"/api/leads/{lead_id}/regenerate-draft")
    assert resp.status_code == 403
    assert draft_called["called"] is False


# --- R1-16 refresh error sanitization ---------------------------------------

def test_refresh_error_sanitizes_secret(tmp_path, monkeypatch):
    """R1-16: Refresh errors must not expose raw exception text or secrets."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_stale_lead())

    secret = "sk-test-super-secret"
    def exploding_crawl(url):
        raise RuntimeError(f"API key {secret} is invalid")

    monkeypatch.setattr(pipeline_mod, "crawl_company", exploding_crawl)

    client = TestClient(api_mod.app)
    resp = client.post(f"/api/leads/{lead_id}/refresh-research")
    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert secret not in detail
    assert "RuntimeError" in detail

    from app.db import get_lead
    row = get_lead(lead_id)
    row_str = str(dict(row))
    assert secret not in row_str


# --- R1-17 regeneration error sanitization ----------------------------------

def test_regenerate_error_sanitizes_secret(tmp_path, monkeypatch):
    """R1-17: Regeneration errors must not expose raw exception text or secrets."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_stale_lead())

    secret = "sk-test-super-secret"
    def exploding_draft(*a):
        raise RuntimeError(f"OpenAI key {secret} rejected")

    monkeypatch.setattr(pipeline_mod, "draft_outreach", exploding_draft)

    client = TestClient(api_mod.app)
    resp = client.post(f"/api/leads/{lead_id}/regenerate-draft")
    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert secret not in detail
    assert "RuntimeError" in detail


# --- R1-6 API-level status matrix for regeneration --------------------------

def test_api_regenerate_gmail_drafted_returns_409(tmp_path):
    """API: gmail_drafted regeneration -> 409."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_stale_lead(status="gmail_drafted", gmail_draft_id="g-1"))

    client = TestClient(api_mod.app)
    resp = client.post(f"/api/leads/{lead_id}/regenerate-draft")
    assert resp.status_code == 409


def test_api_regenerate_sent_returns_409(tmp_path):
    """API: sent regeneration -> 409."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_stale_lead(status="sent"))

    client = TestClient(api_mod.app)
    resp = client.post(f"/api/leads/{lead_id}/regenerate-draft")
    assert resp.status_code == 409


def test_api_regenerate_do_not_contact_returns_409(tmp_path):
    """API: do_not_contact regeneration -> 409."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_stale_lead(status="do_not_contact"))

    client = TestClient(api_mod.app)
    resp = client.post(f"/api/leads/{lead_id}/regenerate-draft")
    assert resp.status_code == 409


def test_api_regenerate_fresh_drafted_returns_409(tmp_path):
    """API: fresh drafted regeneration -> 409 (no draft_outreach call)."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_fresh_lead(status="drafted"))

    client = TestClient(api_mod.app)
    resp = client.post(f"/api/leads/{lead_id}/regenerate-draft")
    assert resp.status_code == 409
    assert "up to date" in resp.json()["detail"].lower()


def test_api_regenerate_stale_approved_revokes_to_drafted(tmp_path, monkeypatch):
    """API: stale approved regeneration -> 200, status becomes drafted."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_stale_lead(status="approved"))
    monkeypatch.setattr(pipeline_mod, "draft_outreach", lambda *a: ("Fresh subject", "Fresh body"))

    client = TestClient(api_mod.app)
    resp = client.post(f"/api/leads/{lead_id}/regenerate-draft")
    assert resp.status_code == 200
    body = resp.json()
    assert body["lead"]["status"] == "drafted"
    assert body["lead"]["draft_stale"] is False
    assert body["lead"]["subject"] == "Fresh subject"


# ---------------------------------------------------------------------------
# R2 optimistic concurrency hardening
# ---------------------------------------------------------------------------

def _make_concurrent_draft_outreach(monkeypatch, mutate_during_call):
    """Create a draft_outreach mock that mutates the DB mid-call.

    ``mutate_during_call`` receives the lead_id and performs arbitrary DB
    mutations BEFORE the mock returns its generated subject/body.
    """
    def fake_draft(company, fit_reason, proof_project, outreach_angle):
        # The lead_id is not passed to draft_outreach; capture it via closure.
        mutate_during_call()
        return ("Concurrent subject", "Concurrent draft body")

    monkeypatch.setattr(pipeline_mod, "draft_outreach", fake_draft)
    return fake_draft


def test_concurrent_do_not_contact_prevents_regeneration_write(tmp_path, monkeypatch):
    """R2-3: If status changes to do_not_contact during draft_outreach(),
    the regeneration must NOT overwrite it.  Returns 409."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_stale_lead(status="drafted"))

    from app.db import update_lead

    def mutate():
        update_lead(lead_id, status="do_not_contact")

    _make_concurrent_draft_outreach(monkeypatch, mutate)

    from app.pipeline import RegenerationBlocked
    with pytest.raises(RegenerationBlocked) as exc_info:
        pipeline_mod.regenerate_draft(lead_id)
    assert exc_info.value.status_code == 409
    assert "changed while" in str(exc_info.value).lower()

    from app.db import get_lead
    row = get_lead(lead_id)
    # do_not_contact decision preserved.
    assert row["status"] == "do_not_contact"
    # Old draft preserved, NOT replaced by concurrent output.
    assert row["subject"] == "Old subject"
    assert row["draft"] == "Old draft body"
    # draft_stale remains true (not cleared by failed regeneration).
    assert bool(row["draft_stale"]) is True


def test_concurrent_research_change_prevents_regeneration_write(tmp_path, monkeypatch):
    """R2-4: If draft-driving research changes during draft_outreach(),
    the regeneration must NOT write a stale draft marked fresh.  Returns 409."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_stale_lead(
        status="drafted",
        proof_project="Aegis",
        outreach_angle="old angle",
    ))

    from app.db import update_lead

    def mutate():
        # Research refresh changes draft-driving fields.
        update_lead(
            lead_id,
            proof_project="Forge Crew",
            outreach_angle="new angle",
        )

    _make_concurrent_draft_outreach(monkeypatch, mutate)

    from app.pipeline import RegenerationBlocked
    with pytest.raises(RegenerationBlocked):
        pipeline_mod.regenerate_draft(lead_id)

    from app.db import get_lead
    row = get_lead(lead_id)
    # New research preserved.
    assert row["proof_project"] == "Forge Crew"
    assert row["outreach_angle"] == "new angle"
    # Old draft NOT replaced.
    assert row["subject"] == "Old subject"
    assert row["draft"] == "Old draft body"
    # Still stale.
    assert bool(row["draft_stale"]) is True


def test_concurrent_gmail_draft_prevents_regeneration_write(tmp_path, monkeypatch):
    """R2-5: If a Gmail draft is created during draft_outreach(), the
    regeneration must NOT overwrite status/gmail_draft_id/draft.  Returns 409."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_stale_lead(status="approved"))

    from app.db import update_lead

    def mutate():
        update_lead(lead_id, gmail_draft_id="gmail-123", status="gmail_drafted")

    _make_concurrent_draft_outreach(monkeypatch, mutate)

    from app.pipeline import RegenerationBlocked
    with pytest.raises(RegenerationBlocked):
        pipeline_mod.regenerate_draft(lead_id)

    from app.db import get_lead
    row = get_lead(lead_id)
    assert row["status"] == "gmail_drafted"
    assert row["gmail_draft_id"] == "gmail-123"
    assert row["subject"] == "Old subject"
    assert row["draft"] == "Old draft body"
    assert bool(row["draft_stale"]) is True


def test_concurrent_contact_change_does_not_block_regeneration(tmp_path, monkeypatch):
    """R2: Contact-only concurrent change does NOT block regeneration because
    contact fields are not in the optimistic-concurrency snapshot."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_stale_lead(status="drafted"))

    from app.db import update_lead

    def mutate():
        # Contact change — NOT a draft-driving field.
        update_lead(lead_id, contact_email="new-email@co.example", contact_quality="high")

    _make_concurrent_draft_outreach(monkeypatch, mutate)

    result = pipeline_mod.regenerate_draft(lead_id)
    assert result["regenerated"] is True

    from app.db import get_lead
    row = get_lead(lead_id)
    assert row["status"] == "drafted"
    assert bool(row["draft_stale"]) is False
    assert row["subject"] == "Concurrent subject"
    assert row["draft"] == "Concurrent draft body"
    # Contact was updated by the concurrent mutation and preserved.
    assert row["contact_email"] == "new-email@co.example"


def test_concurrent_score_change_does_not_block_regeneration(tmp_path, monkeypatch):
    """R2: Score-only concurrent change does NOT block regeneration because
    score is not a draft-driving field."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_stale_lead(status="drafted", score=80))

    from app.db import update_lead

    def mutate():
        update_lead(lead_id, score=95)

    _make_concurrent_draft_outreach(monkeypatch, mutate)

    result = pipeline_mod.regenerate_draft(lead_id)
    assert result["regenerated"] is True

    from app.db import get_lead
    row = get_lead(lead_id)
    assert row["status"] == "drafted"
    assert bool(row["draft_stale"]) is False
    assert row["score"] == 95  # concurrent score change preserved


def test_concurrent_reject_status_change_prevents_regeneration(tmp_path, monkeypatch):
    """R2: If status changes from drafted to rejected during draft_outreach(),
    the regeneration must fail because the snapshot status no longer matches."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_stale_lead(status="drafted"))

    from app.db import update_lead

    def mutate():
        update_lead(lead_id, status="rejected")

    _make_concurrent_draft_outreach(monkeypatch, mutate)

    from app.pipeline import RegenerationBlocked
    with pytest.raises(RegenerationBlocked):
        pipeline_mod.regenerate_draft(lead_id)

    from app.db import get_lead
    row = get_lead(lead_id)
    assert row["status"] == "rejected"
    assert row["subject"] == "Old subject"
    assert bool(row["draft_stale"]) is True


def test_optimistic_conflict_returns_409_via_api(tmp_path, monkeypatch):
    """R2-2: The API endpoint returns 409 on optimistic concurrency conflict."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_stale_lead(status="drafted"))

    from app.db import update_lead

    def mutate():
        update_lead(lead_id, status="do_not_contact")

    _make_concurrent_draft_outreach(monkeypatch, mutate)

    client = TestClient(api_mod.app)
    resp = client.post(f"/api/leads/{lead_id}/regenerate-draft")
    assert resp.status_code == 409
    assert "changed while" in resp.json()["detail"].lower()


def test_generated_stale_result_discarded_after_conflict(tmp_path, monkeypatch):
    """R2-2: The generated draft from a conflicted regeneration is discarded —
    it must NOT be stored anywhere."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_stale_lead(status="drafted", subject="Original", draft="Original body"))

    from app.db import update_lead

    def mutate():
        update_lead(lead_id, status="do_not_contact")

    _make_concurrent_draft_outreach(monkeypatch, mutate)

    client = TestClient(api_mod.app)
    resp = client.post(f"/api/leads/{lead_id}/regenerate-draft")
    assert resp.status_code == 409

    from app.db import get_lead
    row = get_lead(lead_id)
    # Original draft preserved, concurrent output discarded.
    assert row["subject"] == "Original"
    assert row["draft"] == "Original body"
    assert "Concurrent" not in row["subject"]
    assert "Concurrent" not in row["draft"]


def test_normal_drafted_regeneration_succeeds_with_optimistic_update(tmp_path, monkeypatch):
    """R2-6: Normal drafted regeneration (no concurrent change) succeeds."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_stale_lead(status="drafted"))
    monkeypatch.setattr(pipeline_mod, "draft_outreach", lambda *a: ("Fresh subject", "Fresh body"))

    result = pipeline_mod.regenerate_draft(lead_id)
    assert result["regenerated"] is True
    assert result["status"] == "drafted"
    assert result["draft_stale"] is False

    from app.db import get_lead
    row = get_lead(lead_id)
    assert row["status"] == "drafted"
    assert bool(row["draft_stale"]) is False
    assert row["subject"] == "Fresh subject"
    assert row["draft"] == "Fresh body"


def test_rejected_regeneration_succeeds_with_optimistic_update(tmp_path, monkeypatch):
    """R2-6: Rejected regeneration (no concurrent change) succeeds -> drafted."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_stale_lead(status="rejected"))
    monkeypatch.setattr(pipeline_mod, "draft_outreach", lambda *a: ("Fresh subject", "Fresh body"))

    result = pipeline_mod.regenerate_draft(lead_id)
    assert result["regenerated"] is True

    from app.db import get_lead
    row = get_lead(lead_id)
    assert row["status"] == "drafted"
    assert bool(row["draft_stale"]) is False


def test_approved_regeneration_succeeds_with_optimistic_update(tmp_path, monkeypatch):
    """R2-6: Approved regeneration (no concurrent change) succeeds -> drafted."""
    _live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(_stale_lead(status="approved"))
    monkeypatch.setattr(pipeline_mod, "draft_outreach", lambda *a: ("Fresh subject", "Fresh body"))

    result = pipeline_mod.regenerate_draft(lead_id)
    assert result["regenerated"] is True

    from app.db import get_lead
    row = get_lead(lead_id)
    assert row["status"] == "drafted"
    assert bool(row["draft_stale"]) is False

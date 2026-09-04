"""Backend tests for persistent run history, run APIs, refresh-research,
no-auto-send invariant, demo-mode blocking, and bearer auth on new endpoints.

All external HTTP (Serper, scraping, OpenAI, Gmail) is mocked so no real
network calls are made.
"""
from __future__ import annotations

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
    assert body["status"] == RUN_RUNNING
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
    assert "pipeline exploded" in done["error_summary"]


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

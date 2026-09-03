from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import api as api_mod
from app.config import settings
from app.db import get_lead, upsert_lead, update_lead


@pytest.fixture(autouse=True)
def restore_settings():
    original_demo = settings.pactsignal_demo_mode
    original_db = settings.db_path
    yield
    object.__setattr__(settings, "pactsignal_demo_mode", original_demo)
    object.__setattr__(settings, "db_path", original_db)


def test_demo_dashboard_and_leads_are_safe():
    object.__setattr__(settings, "pactsignal_demo_mode", True)
    client = TestClient(api_mod.app)

    dashboard = client.get("/api/dashboard")
    assert dashboard.status_code == 200
    body = dashboard.json()
    assert body["mode"] == "demo"
    assert body["counts"]["drafted"] == 13
    assert body["latest_run"]["fresh_retryable_pool"] == 47

    leads = client.get("/api/leads?min_score=80")
    assert leads.status_code == 200
    assert all(row["score"] >= 80 for row in leads.json()["items"])


def test_demo_mode_blocks_mutations_and_external_actions():
    object.__setattr__(settings, "pactsignal_demo_mode", True)
    client = TestClient(api_mod.app)

    for path in [
        "/api/leads/1/approve",
        "/api/leads/1/do-not-contact",
        "/api/leads/1/gmail-draft",
        "/api/runs/discovery?limit=3",
        "/api/runs/outreach?limit=3",
    ]:
        response = client.post(path)
        assert response.status_code == 403


def test_live_lead_approve_and_reject(tmp_path: Path):
    object.__setattr__(settings, "pactsignal_demo_mode", False)
    object.__setattr__(settings, "db_path", tmp_path / "operator-api.db")

    lead_id = upsert_lead({
        "company": "Test Agency",
        "domain": "test-agency.example",
        "website": "https://test-agency.example",
        "score": 82,
        "proof_project": "WingerX",
        "contact_email": "hello@test-agency.example",
        "subject": "Subject",
        "draft": "Three sentence draft.",
        "status": "drafted",
    })

    client = TestClient(api_mod.app)
    approved = client.post(f"/api/leads/{lead_id}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    rejected = client.post(f"/api/leads/{lead_id}/reject")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"


def test_lead_search_filters_domain_and_proof(tmp_path: Path):
    object.__setattr__(settings, "pactsignal_demo_mode", False)
    object.__setattr__(settings, "db_path", tmp_path / "search.db")

    upsert_lead({
        "company": "Alpha Systems",
        "domain": "alpha.example",
        "website": "https://alpha.example",
        "score": 81,
        "proof_project": "Forge Crew",
        "status": "qualified",
    })
    upsert_lead({
        "company": "Beta Systems",
        "domain": "beta.example",
        "website": "https://beta.example",
        "score": 76,
        "proof_project": "WingerX",
        "status": "drafted",
    })

    client = TestClient(api_mod.app)
    response = client.get("/api/leads?q=forge")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["domain"] == "alpha.example"


def test_health_reports_product_name():
    client = TestClient(api_mod.app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["product"] == "PactSignal"


def test_private_leads_initializes_fresh_database(tmp_path: Path):
    object.__setattr__(settings, "pactsignal_demo_mode", False)
    object.__setattr__(settings, "db_path", tmp_path / "fresh.db")

    client = TestClient(api_mod.app)
    response = client.get("/api/leads")

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0}


def test_demo_leads_total_is_before_limit():
    object.__setattr__(settings, "pactsignal_demo_mode", True)
    client = TestClient(api_mod.app)

    response = client.get("/api/leads?limit=2")

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 2
    assert body["total"] == len(api_mod.DEMO_LEADS)

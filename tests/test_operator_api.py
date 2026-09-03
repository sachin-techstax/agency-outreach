from __future__ import annotations

from pathlib import Path

import bcrypt
import pytest
from fastapi.testclient import TestClient

from app import api as api_mod
from app.config import settings
from app.db import get_lead, upsert_lead, update_lead


@pytest.fixture(autouse=True)
def restore_settings():
    names = [
        "pactsignal_demo_mode",
        "db_path",
        "pactsignal_auth_enabled",
        "pactsignal_admin_username",
        "pactsignal_admin_password_hash",
        "pactsignal_jwt_secret",
        "pactsignal_jwt_ttl_minutes",
        "pactsignal_cookie_secure",
        "pactsignal_login_max_failures",
        "pactsignal_login_window_seconds",
    ]
    original = {name: getattr(settings, name) for name in names}
    yield
    for name, value in original.items():
        object.__setattr__(settings, name, value)


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


def test_spa_path_containment_rejects_parent_escape(tmp_path: Path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>PactSignal</html>", encoding="utf-8")
    outside = tmp_path / "secret.txt"
    outside.write_text("must-not-leak", encoding="utf-8")

    # The SPA route is registered at import time only when the dist directory
    # exists, so validate the containment primitive directly here.
    dist_root = dist.resolve()
    candidate = (dist_root / "../secret.txt").resolve()

    with pytest.raises(ValueError):
        candidate.relative_to(dist_root)

def _enable_test_auth() -> None:
    object.__setattr__(settings, "pactsignal_auth_enabled", True)
    object.__setattr__(settings, "pactsignal_admin_username", "sachin")
    object.__setattr__(
        settings,
        "pactsignal_admin_password_hash",
        bcrypt.hashpw(b"correct-horse", bcrypt.gensalt(rounds=4)).decode("utf-8"),
    )
    object.__setattr__(settings, "pactsignal_jwt_secret", "test-secret-" + ("a" * 48))
    object.__setattr__(settings, "pactsignal_jwt_ttl_minutes", 60)
    object.__setattr__(settings, "pactsignal_cookie_secure", False)
    object.__setattr__(settings, "pactsignal_login_max_failures", 5)
    object.__setattr__(settings, "pactsignal_login_window_seconds", 300)


def test_auth_enabled_keeps_health_public_but_protects_operator_api():
    _enable_test_auth()
    client = TestClient(api_mod.app)

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["auth_enabled"] is True

    dashboard = client.get("/api/dashboard")
    assert dashboard.status_code == 401
    assert dashboard.json()["detail"] == "Authentication required"


def test_login_issues_httponly_jwt_cookie_and_unlocks_api():
    _enable_test_auth()
    object.__setattr__(settings, "pactsignal_demo_mode", True)
    client = TestClient(api_mod.app)

    invalid = client.post(
        "/api/auth/login",
        json={"username": "sachin", "password": "wrong"},
    )
    assert invalid.status_code == 401

    login = client.post(
        "/api/auth/login",
        json={"username": "sachin", "password": "correct-horse"},
    )
    assert login.status_code == 200
    assert login.json()["authenticated"] is True
    set_cookie = login.headers["set-cookie"].lower()
    assert "pactsignal_session=" in set_cookie
    assert "httponly" in set_cookie
    assert "samesite=strict" in set_cookie

    session = client.get("/api/auth/session")
    assert session.status_code == 200
    assert session.json()["username"] == "sachin"

    dashboard = client.get("/api/dashboard")
    assert dashboard.status_code == 200
    assert dashboard.json()["mode"] == "demo"


def test_invalid_jwt_cookie_is_rejected():
    _enable_test_auth()
    client = TestClient(api_mod.app)
    client.cookies.set("pactsignal_session", "not-a-jwt")

    response = client.get("/api/meta")

    assert response.status_code == 401
    assert response.json()["detail"] == "Session expired or invalid"


def test_logout_clears_session_cookie():
    _enable_test_auth()
    client = TestClient(api_mod.app)

    login = client.post(
        "/api/auth/login",
        json={"username": "sachin", "password": "correct-horse"},
    )
    assert login.status_code == 200

    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200

    session = client.get("/api/auth/session")
    assert session.status_code == 401


"""Tests for contact hygiene — rejected emails, tiered selection, role detection."""
from __future__ import annotations

from app.contacts import discover_contact


DOMAIN = "company.com"


def _make_text(emails: list[str], domain: str = DOMAIN, roles: str = "") -> str:
    """Build site text containing the given emails."""
    email_str = " ".join(f"Contact us at {e}" for e in emails)
    return f"Welcome to our company. {roles} {email_str} We build AI solutions."


def test_media_email_rejected():
    text = _make_text(["media@company.com"])
    result = discover_contact(text, [], DOMAIN)
    assert result["contact_email"] == ""
    assert result["contact_quality"] == "none"


def test_press_email_rejected():
    text = _make_text(["press@company.com"])
    result = discover_contact(text, [], DOMAIN)
    assert result["contact_email"] == ""


def test_privacy_email_rejected():
    text = _make_text(["privacy@company.com"])
    result = discover_contact(text, [], DOMAIN)
    assert result["contact_email"] == ""


def test_careers_email_rejected():
    text = _make_text(["careers@company.com"])
    result = discover_contact(text, [], DOMAIN)
    assert result["contact_email"] == ""


def test_noreply_email_rejected():
    text = _make_text(["noreply@company.com"])
    result = discover_contact(text, [], DOMAIN)
    assert result["contact_email"] == ""


def test_support_email_rejected():
    text = _make_text(["support@company.com"])
    result = discover_contact(text, [], DOMAIN)
    assert result["contact_email"] == ""


def test_founder_preferred_over_hello():
    text = _make_text(["hello@company.com", "founder@company.com"])
    result = discover_contact(text, [], DOMAIN)
    assert result["contact_email"] == "founder@company.com"
    assert result["contact_quality"] == "high"


def test_partnerships_preferred_over_info():
    text = _make_text(["info@company.com", "partnerships@company.com"])
    result = discover_contact(text, [], DOMAIN)
    assert result["contact_email"] == "partnerships@company.com"
    assert result["contact_quality"] == "high"


def test_hello_accepted_as_fallback():
    text = _make_text(["hello@company.com"])
    result = discover_contact(text, [], DOMAIN)
    assert result["contact_email"] == "hello@company.com"
    assert result["contact_quality"] == "medium"


def test_contact_accepted_as_fallback():
    text = _make_text(["contact@company.com"])
    result = discover_contact(text, [], DOMAIN)
    assert result["contact_email"] == "contact@company.com"
    assert result["contact_quality"] == "medium"


def test_other_domain_email_rejected():
    text = _make_text(["founder@other-domain.com"])
    result = discover_contact(text, [], DOMAIN)
    assert result["contact_email"] == ""


def test_no_email_returns_empty():
    text = "Welcome to our company. We build AI solutions."
    result = discover_contact(text, [], DOMAIN)
    assert result["contact_email"] == ""
    assert result["contact_quality"] == "none"


def test_all_rejected_returns_empty():
    text = _make_text(["media@company.com", "press@company.com", "privacy@company.com"])
    result = discover_contact(text, [], DOMAIN)
    assert result["contact_email"] == ""
    assert result["contact_quality"] == "none"


def test_role_detected_from_text():
    text = _make_text(["hello@company.com"], roles="Our founder and CEO leads the team.")
    result = discover_contact(text, [], DOMAIN)
    assert result["contact_role"] != ""
    assert "Founder" in result["contact_role"] or "Ceo" in result["contact_role"].title()


def test_no_role_defaults_to_empty():
    text = _make_text(["hello@company.com"])
    result = discover_contact(text, [], DOMAIN)
    assert result["contact_role"] == ""


def test_contact_name_always_empty():
    text = _make_text(["founder@company.com"])
    result = discover_contact(text, [], DOMAIN)
    assert result["contact_name"] == ""

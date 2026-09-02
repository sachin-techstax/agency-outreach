"""Tests for deterministic company identity extraction."""
from __future__ import annotations

from app.company_identity import extract_company_name


def test_seo_title_falls_back_to_domain():
    """ayautomate.com with title 'Best AI Transformation Partners' should
    fall back to domain-derived brand."""
    name = extract_company_name("Best AI Transformation Partners", "ayautomate.com")
    # Domain-derived brand: "ayautomate" -> "Ayautomate" (no hyphens to split on)
    assert name.lower() == "ayautomate"
    assert name != "Best AI Transformation Partners"


def test_pipe_separator_extracts_brand():
    name = extract_company_name("Acme AI | Generative AI Development Agency", "acme.ai")
    assert name == "Acme AI"


def test_dash_separator_extracts_brand():
    name = extract_company_name("Acme AI - Custom Software & AI Automation", "acme.ai")
    assert name == "Acme AI"


def test_empty_title_falls_back_to_domain():
    name = extract_company_name("", "example.io")
    assert name == "Example"


def test_marketing_phrase_rejected():
    name = extract_company_name("Top Generative AI Companies", "tech-blog.com")
    assert name == "Tech Blog"


def test_leading_prefix_rejected():
    name = extract_company_name("Leading AI Consulting Firm", "somecompany.com")
    assert name == "Somecompany"


def test_short_brand_with_best_not_rejected():
    """A short compound brand like 'BestAI' should not be rejected."""
    name = extract_company_name("BestAI", "bestai.io")
    assert name == "BestAI"


def test_hyphenated_domain_to_brand():
    name = extract_company_name("", "my-cool-agency.com")
    assert name == "My Cool Agency"


def test_long_seo_title_rejected():
    name = extract_company_name(
        "AI Automation Services - Custom AI Solutions for Enterprise Clients",
        "fakeagency.com",
    )
    # "AI Automation Services" looks like a category, should fall back
    assert name == "Fakeagency"

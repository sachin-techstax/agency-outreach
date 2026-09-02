"""Tests for commercial fit qualification scoring and categorization.

All tests use synthetic site text — no network or LLM calls.
"""
from __future__ import annotations

from app.commercial_fit import score_commercial_fit


# ---------------------------------------------------------------------------
# A. AI agency — strong commercial fit
# ---------------------------------------------------------------------------

AGENCY_TEXT = (
    "We are an AI development agency providing custom software and AI development services "
    "for clients. We build AI agents, workflow automation, RAG systems, and LLM applications. "
    "See our case studies and client projects. Our delivery team helps companies with "
    "AI implementation and system integration. We are your technology partner and "
    "development partner for custom development and engineering services. "
    "We also do machine learning, data engineering, and backend API development "
    "using Python and FastAPI. We help companies build AI products."
)


def test_ai_agency_scores_above_threshold():
    fit = score_commercial_fit(AGENCY_TEXT)
    assert fit.score >= 70
    assert fit.category in ("agency", "consultancy")


def test_ai_agency_has_transparent_reasons():
    fit = score_commercial_fit(AGENCY_TEXT)
    assert len(fit.reasons) > 0
    # Every reason should start with +N or -N
    for reason in fit.reasons:
        assert reason[0] in "+-"


# ---------------------------------------------------------------------------
# B. AI SaaS platform — low commercial fit
# ---------------------------------------------------------------------------

SAAS_TEXT = (
    "AI agent platform for building conversational AI. Start free today. "
    "Pricing plans for every team. Our SaaS platform offers self-service "
    "deployment. Subscribe to our developer platform today. API documentation and "
    "developer tools available. Product documentation and SDK access. "
    "Sign up free and start building your first AI agent in minutes."
)


def test_saas_platform_scores_below_threshold():
    fit = score_commercial_fit(SAAS_TEXT)
    assert fit.score < 70
    assert fit.category in ("platform", "product-company")


def test_saas_platform_has_product_penalty():
    fit = score_commercial_fit(SAAS_TEXT)
    penalty_reasons = [r for r in fit.reasons if r.startswith("-")]
    assert len(penalty_reasons) > 0


# ---------------------------------------------------------------------------
# C. Community platform — low commercial fit
# ---------------------------------------------------------------------------

COMMUNITY_TEXT = (
    "Join our community of creators. Membership includes access to courses, "
    "academy content, and cohort-based bootcamp programs. Our community platform "
    "offers certification programs. Join the community and learn together. "
    "Become a member today."
)


def test_community_scores_below_threshold():
    fit = score_commercial_fit(COMMUNITY_TEXT)
    assert fit.score < 70
    assert fit.category in ("community", "training")


# ---------------------------------------------------------------------------
# D. Enterprise consultancy — relevant but lower score than small agency
# ---------------------------------------------------------------------------

ENTERPRISE_TEXT = (
    "Global consulting firm providing enterprise transformation and AI implementation. "
    "We are a multinational consultancy with thousands of employees and global offices. "
    "Fortune 500 clients trust our digital transformation consulting and technology consulting. "
    "We offer system integration and implementation services for large enterprise clients."
)


def test_enterprise_consultancy_below_small_agency():
    agency_fit = score_commercial_fit(AGENCY_TEXT)
    enterprise_fit = score_commercial_fit(ENTERPRISE_TEXT)
    # Enterprise should be commercially relevant but lower than a small/mid agency
    assert enterprise_fit.score < agency_fit.score
    assert enterprise_fit.category in ("consultancy", "enterprise-consultancy")


def test_enterprise_has_enterprise_penalty():
    fit = score_commercial_fit(ENTERPRISE_TEXT)
    enterprise_penalties = [r for r in fit.reasons if "enterprise scale" in r]
    assert len(enterprise_penalties) > 0


# ---------------------------------------------------------------------------
# Score explanation transparency
# ---------------------------------------------------------------------------


def test_score_explanation_is_transparent():
    fit = score_commercial_fit(AGENCY_TEXT)
    # Should have both positive and identifiable reasons
    assert any("client-services" in r or "custom development" in r or "client delivery" in r for r in fit.reasons)


def test_scores_clamped_to_valid_range():
    # Very short text with no signals
    fit = score_commercial_fit("hello world")
    assert 0 <= fit.score <= 100
    # Text with tons of signals
    huge = (AGENCY_TEXT * 10)
    fit2 = score_commercial_fit(huge)
    assert 0 <= fit2.score <= 100


def test_sub_scores_exposed():
    fit = score_commercial_fit(AGENCY_TEXT)
    assert fit.technical_score >= 0
    assert fit.commercial_score >= 0
    assert 0 <= fit.technical_score <= 100
    assert 0 <= fit.commercial_score <= 100

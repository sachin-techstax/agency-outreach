"""Tests for discovery priority scoring of search hits.

Covers the four scenarios from the spec:
  A. Real-looking AI agency -> high priority
  B. SaaS platform -> significantly below A
  C. Enterprise consultancy -> lower than a boutique/custom dev agency
  D. Generic AI article -> low priority / candidate_filter rejection

Also covers commercial-intent-dominates-topical-terms and URL path hints.
"""
from __future__ import annotations

from app.discovery_priority import DiscoveryPriority, rank_hits, score_discovery_priority
from app.search import SearchHit


def _hit(title: str, snippet: str, url: str = "https://example.com/services") -> SearchHit:
    return SearchHit(title=title, url=url, snippet=snippet, query="q")


# ---------------------------------------------------------------------------
# Scenario A: real-looking AI agency
# ---------------------------------------------------------------------------


def test_real_agency_high_priority():
    hit = _hit(
        "Acme AI - AI Agent Development Agency",
        "We build custom AI agents and automation systems for clients.",
    )
    p = score_discovery_priority(hit)
    assert p.score >= 60
    assert "agency" in p.reasons
    assert "custom-development" in p.reasons or "client-delivery" in p.reasons


# ---------------------------------------------------------------------------
# Scenario B: SaaS platform
# ---------------------------------------------------------------------------


def test_saas_platform_below_agency():
    platform = _hit(
        "AI Agent Platform",
        "Start free. Pricing plans. Developer platform.",
        url="https://platform.example.com/",
    )
    agency = _hit(
        "Acme AI - AI Agent Development Agency",
        "We build custom AI agents and automation systems for clients.",
    )
    pp = score_discovery_priority(platform)
    pa = score_discovery_priority(agency)
    assert pp.score < pa.score
    # significantly below
    assert pa.score - pp.score >= 30
    assert "-platform" in pp.reasons or "-self-service" in pp.reasons


# ---------------------------------------------------------------------------
# Scenario C: enterprise consultancy
# ---------------------------------------------------------------------------


def test_enterprise_consultancy_below_boutique():
    enterprise = _hit(
        "Global AI Consulting and Advisory",
        "Public multinational consultancy serving Fortune 500 organizations.",
    )
    boutique = _hit(
        "Boutique AI Development Studio",
        "We develop custom AI solutions for client projects. Case studies.",
    )
    pe = score_discovery_priority(enterprise)
    pb = score_discovery_priority(boutique)
    assert pb.score > pe.score
    # enterprise should be penalized
    assert "-enterprise-scale" in pe.reasons


# ---------------------------------------------------------------------------
# Scenario D: generic AI article (no commercial signal)
# ---------------------------------------------------------------------------


def test_generic_ai_article_low_priority():
    article = _hit(
        "What is RAG? A guide to retrieval augmented generation",
        "An introduction to RAG and LLMs for developers.",
        url="https://blog.example.com/what-is-rag",
    )
    p = score_discovery_priority(article)
    # No delivery signal -> topical bonuses stripped -> low score
    assert p.score <= 5
    # editorial penalty applied
    assert "-editorial" in p.reasons


# ---------------------------------------------------------------------------
# Commercial intent dominates topical AI terms
# ---------------------------------------------------------------------------


def test_commercial_intent_dominates_topical_terms():
    platform = _hit(
        "Enterprise AI Platform | Start Free",
        "AI platform with LLM, RAG, automation, machine learning, AI agents.",
        url="https://platform.example.com/",
    )
    agency = _hit(
        "Acme AI Development Agency | Custom AI Solutions for Clients",
        "We build AI agents.",
        url="https://acme.ai/services",
    )
    pp = score_discovery_priority(platform)
    pa = score_discovery_priority(agency)
    assert pa.score > pp.score, (
        f"agency {pa.score} should outrank platform {pp.score}"
    )


def test_topical_terms_alone_do_not_help_without_delivery():
    # Lots of AI keywords, zero delivery signal
    hit = _hit(
        "AI LLM RAG automation machine learning",
        "Generative AI agents and LLMs.",
        url="https://example.com/",
    )
    p = score_discovery_priority(hit)
    # topical bonuses should be stripped
    assert p.score <= 5
    assert not any(r.endswith("-topic") for r in p.reasons)


# ---------------------------------------------------------------------------
# Size proxies only when explicit
# ---------------------------------------------------------------------------


def test_boutique_bonus_when_explicit():
    with_size = _hit(
        "Boutique AI Development Agency",
        "We build custom AI for clients.",
    )
    without_size = _hit(
        "AI Development Agency",
        "We build custom AI for clients.",
    )
    pw = score_discovery_priority(with_size)
    po = score_discovery_priority(without_size)
    assert pw.score > po.score
    assert "boutique" in pw.reasons


# ---------------------------------------------------------------------------
# URL path hints
# ---------------------------------------------------------------------------


def test_delivery_path_bonus():
    services = _hit("AI Agency", "We build AI for clients.", url="https://x.ai/services")
    blog = _hit("AI Agency", "We build AI for clients.", url="https://x.ai/blog/post")
    ps = score_discovery_priority(services)
    pb = score_discovery_priority(blog)
    assert ps.score > pb.score
    assert "delivery-path" in ps.reasons
    assert "-content-path" in pb.reasons


def test_file_extension_zero():
    hit = _hit("AI Agency", "We build AI for clients.", url="https://x.ai/brochure.pdf")
    p = score_discovery_priority(hit)
    assert p.score == 0
    assert "-file" in p.reasons


# ---------------------------------------------------------------------------
# Determinism + rank_hits
# ---------------------------------------------------------------------------


def test_score_deterministic():
    hit = _hit("Acme AI Agency", "We build AI for clients.")
    assert score_discovery_priority(hit) == score_discovery_priority(hit)


def test_rank_hits_descending_priority():
    agency = _hit("Acme AI Agency", "We build custom AI for clients.", url="https://a.ai/services")
    platform = _hit("AI Platform", "Start free. Developer platform.", url="https://p.com/")
    article = _hit("What is RAG", "A guide to RAG and LLMs.", url="https://b.example.com/what-is-rag")
    ranked = rank_hits([platform, article, agency])
    assert ranked[0] is agency
    assert ranked[-1] is article or ranked[-1] is platform


def test_rank_hits_tiebreak_query_then_result_rank():
    h1 = _hit("AI Agency", "We build AI for clients.", url="https://a.ai/services")
    h1.query_rank = 1
    h1.result_rank = 0
    h2 = _hit("AI Agency", "We build AI for clients.", url="https://b.ai/services")
    h2.query_rank = 0
    h2.result_rank = 5
    # same priority -> earlier query_rank wins
    ranked = rank_hits([h1, h2])
    assert ranked[0] is h2


def test_discovery_priority_is_frozen():
    p = DiscoveryPriority(score=50, reasons=["agency"])
    import dataclasses
    assert dataclasses.is_dataclass(p)
    # frozen dataclass
    try:
        p.score = 10  # type: ignore[misc]
        raise AssertionError("DiscoveryPriority should be frozen")
    except dataclasses.FrozenInstanceError:
        pass

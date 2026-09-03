"""Tests for discovery pool construction: best-hit-per-domain selection,
ranked attempt order, and query-level observability.

These exercise the pipeline discovery layer with mocked Serper calls.
"""
from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from app import pipeline as pipeline_mod
from app.config import settings
from app.pipeline import discover_only, run as run_pipeline
from app.search import SearchHit


def _set_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    object.__setattr__(settings, "db_path", db_path)
    return db_path


def _make_site(text: str, domain: str, title: str = "Example") -> dict:
    return {
        "root": f"https://{domain}",
        "domain": domain,
        "title": title,
        "text": text,
        "pages": [],
    }


STRONG_TEXT = (
    "We are an AI development agency providing custom software and AI development services "
    "for clients. We build AI agents, workflow automation, RAG systems, APIs and backend products. "
    "See our case studies and client projects. Our delivery team helps companies with "
    "AI implementation and system integration. We are a technology partner and development partner "
    "offering engineering services and implementation services. "
    "We deliver production AI systems for clients across multiple industries. "
    "Our team specializes in LLM development, retrieval augmented generation, "
    "machine learning, data engineering, and end-to-end AI product engineering. "
    "Contact us at hello@example.ai"
)


# ---------------------------------------------------------------------------
# Best hit per domain
# ---------------------------------------------------------------------------


def test_best_hit_per_domain_keeps_services_over_blog(tmp_path, monkeypatch):
    """Same domain appears twice: a generic blog hit first, then a strong
    services hit.  The services hit should be retained as the candidate."""
    _set_db(tmp_path)
    from app.search import DEFAULT_QUERY_SPECS

    # Two queries: first returns the blog hit, second returns the services hit.
    blog_hit = SearchHit(
        title="Agency Blog - AI Trends",
        url="https://agency.com/blog/ai-trends",
        snippet="AI trends and news. AI LLM automation.",
        query="q1",
    )
    services_hit = SearchHit(
        title="Agency - AI Agent Development Services",
        url="https://agency.com/services/ai-agent-development",
        snippet="We build custom AI solutions for clients. Case studies.",
        query="q2",
    )

    call = {"n": 0}

    def fake_search(query, num=10):
        call["n"] += 1
        if call["n"] == 1:
            return [blog_hit]
        return [services_hit]

    monkeypatch.setattr(pipeline_mod, "search_serper", fake_search)
    monkeypatch.setattr(
        pipeline_mod, "crawl_company", lambda url: _make_site(STRONG_TEXT, "agency.com")
    )
    monkeypatch.setattr(
        pipeline_mod, "analyze_agency",
        lambda *a, **k: {"summary": "s", "services": "ai", "fit_reason": "f",
                         "proof_project": "WingerX", "outreach_angle": "a"},
    )
    monkeypatch.setattr(
        pipeline_mod, "draft_outreach", lambda *a, **k: ("Subject", "Body")
    )

    result = run_pipeline(limit=1)

    assert result["raw_candidate_domains"] == 1
    assert result["candidate_domains"] == 1
    assert result["attempted"] == 1
    assert result["processed"] == 1
    # The services hit has higher priority than the blog hit; verify via
    # discover_only which exposes the chosen source_url.
    call["n"] = 0
    disc = discover_only(limit=5)
    assert disc["candidate_domains"] == 1
    row = disc["ranked"][0]
    assert row["domain"] == "agency.com"
    assert row["url"] == services_hit.url
    assert row["source_query"] == services_hit.query
    assert row["priority"] > 0


def test_best_hit_priority_ordering_in_pool(tmp_path, monkeypatch):
    """A higher-priority later hit replaces a lower-priority earlier hit."""
    _set_db(tmp_path)

    weak_hit = SearchHit(
        title="AI Company",
        url="https://co.ai/",
        snippet="AI LLM RAG automation machine learning.",
        query="q1",
        result_rank=0,
    )
    strong_hit = SearchHit(
        title="AI Development Agency",
        url="https://co.ai/services",
        snippet="We build custom AI solutions for clients. Case studies.",
        query="q2",
        result_rank=0,
    )

    call = {"n": 0}

    def fake_search(query, num=10):
        call["n"] += 1
        return [weak_hit] if call["n"] == 1 else [strong_hit]

    monkeypatch.setattr(pipeline_mod, "search_serper", fake_search)

    disc = discover_only(limit=5)
    assert disc["candidate_domains"] == 1
    row = disc["ranked"][0]
    assert row["url"] == strong_hit.url


# ---------------------------------------------------------------------------
# Ranked attempt order
# ---------------------------------------------------------------------------


def test_ranked_attempt_order_crawls_highest_priority_first(tmp_path, monkeypatch):
    """Mock candidates with different priorities; --limit 2 should attempt
    the two highest-priority domains first, not the lowest."""
    _set_db(tmp_path)

    # Four domains with distinct priority profiles.
    hits_by_query = {
        0: [SearchHit(  # enterprise.example - low priority
            title="Global AI Consulting and Advisory",
            url="https://enterprise.example/",
            snippet="Public multinational consultancy serving Fortune 500 organizations.",
            query="q0", result_rank=0,
        )],
        1: [SearchHit(  # agency-b.example - high priority
            title="Agency B - AI Development Agency",
            url="https://agency-b.example/services",
            snippet="We build custom AI solutions for clients. Case studies.",
            query="q1", result_rank=0,
        )],
        2: [SearchHit(  # agency-a.example - highest priority
            title="Agency A - AI Agent Development Agency | Boutique",
            url="https://agency-a.example/services",
            snippet="We build custom AI agents for clients. Case studies.",
            query="q2", result_rank=0,
        )],
        3: [SearchHit(  # platform.example - low priority
            title="AI Platform",
            url="https://platform.example/",
            snippet="Start free. Pricing plans. Developer platform.",
            query="q3", result_rank=0,
        )],
    }
    # Remaining queries return empty so only these four domains surface.
    from app.search import DEFAULT_QUERY_SPECS
    n_specs = len(DEFAULT_QUERY_SPECS)

    call = {"n": 0}

    def fake_search(query, num=10):
        call["n"] += 1
        idx = call["n"] - 1
        if idx in hits_by_query:
            return hits_by_query[idx]
        return []

    monkeypatch.setattr(pipeline_mod, "search_serper", fake_search)

    crawled: list[str] = []

    def fake_crawl(url):
        from app.scrape import domain_of
        d = domain_of(url)
        crawled.append(d)
        return _make_site(STRONG_TEXT, d, title=d)

    monkeypatch.setattr(pipeline_mod, "crawl_company", fake_crawl)
    monkeypatch.setattr(
        pipeline_mod, "analyze_agency",
        lambda *a, **k: {"summary": "s", "services": "ai", "fit_reason": "f",
                         "proof_project": "WingerX", "outreach_angle": "a"},
    )
    monkeypatch.setattr(
        pipeline_mod, "draft_outreach", lambda *a, **k: ("Subject", "Body")
    )

    result = run_pipeline(limit=2)

    assert result["attempted"] == 2
    # The two highest-priority domains should be attempted first.
    assert crawled[0] == "agency-a.example"
    assert crawled[1] == "agency-b.example"
    # enterprise and platform must NOT consume an attempt
    assert "enterprise.example" not in crawled
    assert "platform.example" not in crawled
    # Invariant
    assert result["attempted"] == result["processed"] + result["skipped"] + result["failed"]


def test_ranked_attempt_order_invariant_holds(tmp_path, monkeypatch):
    """attempted == processed + skipped + failed after ranked selection."""
    _set_db(tmp_path)

    hits = [
        SearchHit(
            title="Agency A - AI Development Agency",
            url="https://agency-a.example/services",
            snippet="We build custom AI for clients.",
            query="q", result_rank=0,
        ),
        SearchHit(
            title="Agency B - AI Development Agency",
            url="https://agency-b.example/services",
            snippet="We build custom AI for clients.",
            query="q", result_rank=1,
        ),
        SearchHit(
            title="Agency C - AI Development Agency",
            url="https://agency-c.example/services",
            snippet="We build custom AI for clients.",
            query="q", result_rank=2,
        ),
    ]

    def fake_search(query, num=10):
        return hits

    monkeypatch.setattr(pipeline_mod, "search_serper", fake_search)

    def fake_crawl(url):
        from app.scrape import domain_of
        d = domain_of(url)
        if "agency-b" in d:
            raise ConnectionError("boom")
        return _make_site(STRONG_TEXT, d, title=d)

    monkeypatch.setattr(pipeline_mod, "crawl_company", fake_crawl)
    monkeypatch.setattr(
        pipeline_mod, "analyze_agency",
        lambda *a, **k: {"summary": "s", "services": "ai", "fit_reason": "f",
                         "proof_project": "WingerX", "outreach_angle": "a"},
    )
    monkeypatch.setattr(
        pipeline_mod, "draft_outreach", lambda *a, **k: ("Subject", "Body")
    )

    result = run_pipeline(limit=3)
    assert result["attempted"] == 3
    assert result["failed"] == 1
    assert result["attempted"] == result["processed"] + result["skipped"] + result["failed"]


# ---------------------------------------------------------------------------
# Query-level observability
# ---------------------------------------------------------------------------


def test_query_level_observability_in_discover_only(tmp_path, monkeypatch):
    """discover_only should return per-query metrics."""
    _set_db(tmp_path)

    def fake_search(query, num=10):
        return [
            SearchHit(
                title="Acme AI - AI Development Agency",
                url="https://acme.ai/services",
                snippet="We build custom AI for clients.",
                query=query, result_rank=0,
            ),
        ]

    monkeypatch.setattr(pipeline_mod, "search_serper", fake_search)

    disc = discover_only(limit=5)
    assert "per_query" in disc
    assert isinstance(disc["per_query"], list)
    assert len(disc["per_query"]) >= 1
    q0 = disc["per_query"][0]
    for key in ("category", "query", "results", "unique", "accepted", "rejected", "selected"):
        assert key in q0
    assert q0["results"] == 1
    assert q0["unique"] == 1
    assert q0["accepted"] == 1


def test_discovery_summary_metrics_present(tmp_path, monkeypatch):
    """discover_only should expose query_count, search_results_total,
    ranked_candidate_domains, candidate_priority_avg."""
    _set_db(tmp_path)

    def fake_search(query, num=10):
        return [
            SearchHit(
                title="Acme AI - AI Development Agency",
                url="https://acme.ai/services",
                snippet="We build custom AI for clients.",
                query=query, result_rank=0,
            ),
        ]

    monkeypatch.setattr(pipeline_mod, "search_serper", fake_search)

    disc = discover_only(limit=5)
    for key in (
        "query_count", "search_results_total", "raw_candidate_domains",
        "rejected_candidate_domains", "candidate_domains",
        "ranked_candidate_domains", "candidate_priority_avg", "ranked",
    ):
        assert key in disc, f"missing {key}"
    assert disc["query_count"] >= 1
    assert disc["search_results_total"] >= 1
    assert disc["ranked_candidate_domains"] == disc["candidate_domains"]
    assert isinstance(disc["candidate_priority_avg"], float)
    assert len(disc["ranked"]) == disc["candidate_domains"]
    row = disc["ranked"][0]
    for key in ("rank", "domain", "priority", "category", "source_query", "title", "url"):
        assert key in row


# ---------------------------------------------------------------------------
# discover_only must not mutate leads / call LLM / crawl
# ---------------------------------------------------------------------------


def test_discover_only_does_not_crawl_or_call_llm(tmp_path, monkeypatch):
    """discover_only must NOT invoke crawl_company, analyze_agency,
    draft_outreach, upsert_lead, or any Gmail operation."""
    _set_db(tmp_path)

    def fail_if_crawl(*a, **k):
        raise AssertionError("crawl_company must not be called by discover_only")

    def fail_if_analyze(*a, **k):
        raise AssertionError("analyze_agency must not be called by discover_only")

    def fail_if_draft(*a, **k):
        raise AssertionError("draft_outreach must not be called by discover_only")

    def fail_if_upsert(*a, **k):
        raise AssertionError("upsert_lead must not be called by discover_only")

    monkeypatch.setattr(pipeline_mod, "crawl_company", fail_if_crawl)
    monkeypatch.setattr(pipeline_mod, "analyze_agency", fail_if_analyze)
    monkeypatch.setattr(pipeline_mod, "draft_outreach", fail_if_draft)
    monkeypatch.setattr(pipeline_mod, "upsert_lead", fail_if_upsert)

    def fake_search(query, num=10):
        return [
            SearchHit(
                title="Acme AI - AI Development Agency",
                url="https://acme.ai/services",
                snippet="We build custom AI for clients.",
                query=query, result_rank=0,
            ),
        ]

    monkeypatch.setattr(pipeline_mod, "search_serper", fake_search)

    # Should complete without raising
    disc = discover_only(limit=5)
    assert disc["candidate_domains"] >= 1


# ---------------------------------------------------------------------------
# Discovery priority logged during pipeline.run
# ---------------------------------------------------------------------------


def test_pipeline_logs_discovery_priority(tmp_path, monkeypatch, caplog):
    """run() should log discovery_priority= for attempted candidates."""
    _set_db(tmp_path)

    def fake_search(query, num=10):
        return [
            SearchHit(
                title="Acme AI - AI Development Agency",
                url="https://acme.ai/services",
                snippet="We build custom AI for clients.",
                query=query, result_rank=0,
            ),
        ]

    monkeypatch.setattr(pipeline_mod, "search_serper", fake_search)
    monkeypatch.setattr(
        pipeline_mod, "crawl_company", lambda url: _make_site(STRONG_TEXT, "acme.ai")
    )
    monkeypatch.setattr(
        pipeline_mod, "analyze_agency",
        lambda *a, **k: {"summary": "s", "services": "ai", "fit_reason": "f",
                         "proof_project": "WingerX", "outreach_angle": "a"},
    )
    monkeypatch.setattr(
        pipeline_mod, "draft_outreach", lambda *a, **k: ("Subject", "Body")
    )

    with caplog.at_level(logging.INFO, logger="pipeline"):
        run_pipeline(limit=1)

    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "discovery_priority=" in messages


# ===========================================================================
# R1 regression tests
# ===========================================================================


def _agency_hit(domain: str, rank: int = 0, query: str = "q") -> SearchHit:
    """A strong agency hit that passes candidate_filter and scores well."""
    return SearchHit(
        title=f"{domain} - AI Development Agency",
        url=f"https://{domain}/services",
        snippet="We build custom AI solutions for clients. Case studies.",
        query=query,
        result_rank=rank,
    )


def _setup_n_candidates(n: int, monkeypatch):
    """Patch search so that the first query returns n accepted agency hits
    and all remaining queries return empty."""
    hits = [_agency_hit(f"agency-{i}.example", rank=i) for i in range(n)]

    def fake_search(query, num=10):
        # Only the first call returns hits; the rest return empty so the
        # pool is exactly n domains.
        if not hasattr(fake_search, "_called"):
            fake_search._called = True  # type: ignore[attr-defined]
            return hits
        return []

    monkeypatch.setattr(pipeline_mod, "search_serper", fake_search)
    return hits


# ---------------------------------------------------------------------------
# R1-7 A & B: discover_only(limit) returns only top N rows; full count preserved
# ---------------------------------------------------------------------------


def test_discover_only_limit_truncates_displayed_rows(tmp_path, monkeypatch):
    """discover_only(limit=20) with 25 candidates returns exactly 20 rows
    while candidate_domains remains 25."""
    _set_db(tmp_path)
    _setup_n_candidates(25, monkeypatch)

    result = discover_only(limit=20)

    assert result["candidate_domains"] == 25
    assert result["ranked_candidate_domains"] == 25
    assert result["displayed_candidate_domains"] == 20
    assert len(result["ranked"]) == 20
    # Rows are ranked 1..20
    assert [r["rank"] for r in result["ranked"]] == list(range(1, 21))


def test_discover_only_limit_5_returns_exactly_top_5(tmp_path, monkeypatch):
    """limit=5 -> exactly top 5 rows."""
    _set_db(tmp_path)
    _setup_n_candidates(25, monkeypatch)

    result = discover_only(limit=5)

    assert result["candidate_domains"] == 25
    assert result["displayed_candidate_domains"] == 5
    assert len(result["ranked"]) == 5


def test_discover_only_limit_above_pool_returns_all(tmp_path, monkeypatch):
    """limit > pool size -> all candidates displayed."""
    _set_db(tmp_path)
    _setup_n_candidates(10, monkeypatch)

    result = discover_only(limit=50)

    assert result["candidate_domains"] == 10
    assert result["displayed_candidate_domains"] == 10
    assert len(result["ranked"]) == 10


# ---------------------------------------------------------------------------
# R1-7 C & D: per-query selected tracked by query identity, not category
# ---------------------------------------------------------------------------


def test_per_query_selected_independent_for_same_category(tmp_path, monkeypatch):
    """Two QuerySpecs sharing the same category must receive independent
    selected counts based on which query actually contributed the best hit."""
    _set_db(tmp_path)

    # We need two queries with the same category.  Patch DEFAULT_QUERY_SPECS
    # so the first two specs share category "automation" and each returns
    # distinct domains.
    from app.search import QuerySpec
    from app import pipeline as pm

    specs = (
        QuerySpec("query A", "automation", 100),
        QuerySpec("query B", "automation", 90),
    )
    monkeypatch.setattr(pm, "DEFAULT_QUERY_SPECS", specs)

    query_a_domains = [f"a-agency-{i}.example" for i in range(3)]
    query_b_domains = [f"b-agency-{i}.example" for i in range(2)]

    call = {"n": 0}

    def fake_search(query, num=10):
        call["n"] += 1
        if call["n"] == 1:
            return [_agency_hit(d, rank=i, query="query A") for i, d in enumerate(query_a_domains)]
        return [_agency_hit(d, rank=i, query="query B") for i, d in enumerate(query_b_domains)]

    monkeypatch.setattr(pm, "search_serper", fake_search)

    result = discover_only(limit=20)

    per_q = result["per_query"]
    assert len(per_q) == 2
    # Both have category "automation"
    assert per_q[0]["category"] == "automation"
    assert per_q[1]["category"] == "automation"
    # But selected counts must be independent
    assert per_q[0]["selected"] == 3  # query A contributed 3
    assert per_q[1]["selected"] == 2  # query B contributed 2
    # NOT both 5
    assert not (per_q[0]["selected"] == 5 and per_q[1]["selected"] == 5)


def test_per_query_selected_sum_equals_candidate_domains(tmp_path, monkeypatch):
    """sum(per_query.selected) == candidate_domains for successful queries."""
    _set_db(tmp_path)

    from app.search import QuerySpec
    from app import pipeline as pm

    specs = (
        QuerySpec("query A", "automation", 100),
        QuerySpec("query B", "ai-consulting", 90),
    )
    monkeypatch.setattr(pm, "DEFAULT_QUERY_SPECS", specs)

    call = {"n": 0}

    def fake_search(query, num=10):
        call["n"] += 1
        if call["n"] == 1:
            return [_agency_hit(f"a-{i}.example", rank=i, query="query A") for i in range(4)]
        return [_agency_hit(f"b-{i}.example", rank=i, query="query B") for i in range(3)]

    monkeypatch.setattr(pm, "search_serper", fake_search)

    result = discover_only(limit=20)

    total_selected = sum(q["selected"] for q in result["per_query"])
    assert total_selected == result["candidate_domains"]


# ---------------------------------------------------------------------------
# R1-7 E: source_query uses base QuerySpec query, not expanded -site query
# ---------------------------------------------------------------------------


def test_source_query_is_base_spec_query_not_expanded(tmp_path, monkeypatch):
    """The chosen best-hit source_query must be the human-readable base
    QuerySpec.query, not the expanded Google query with -site exclusions."""
    _set_db(tmp_path)

    from app.search import QuerySpec, NEGATIVE_SITE_EXCLUSIONS
    from app import pipeline as pm

    specs = (QuerySpec("AI agent development agency", "ai-agents", 100),)
    monkeypatch.setattr(pm, "DEFAULT_QUERY_SPECS", specs)

    captured_queries = []

    def fake_search(query, num=10):
        captured_queries.append(query)
        return [
            SearchHit(
                title="Acme AI - AI Agent Development Agency",
                url="https://acme.ai/services",
                snippet="We build custom AI agents for clients. Case studies.",
                query=query,
                result_rank=0,
            ),
        ]

    monkeypatch.setattr(pm, "search_serper", fake_search)

    result = discover_only(limit=20)

    # The actual Serper query WAS expanded with -site exclusions
    assert len(captured_queries) == 1
    for excl in NEGATIVE_SITE_EXCLUSIONS:
        assert excl in captured_queries[0]

    # But the source_query in the output is the base spec query
    assert len(result["ranked"]) >= 1
    row = result["ranked"][0]
    assert row["source_query"] == "AI agent development agency"
    # And it does NOT contain -site exclusions
    for excl in NEGATIVE_SITE_EXCLUSIONS:
        assert excl not in row["source_query"]


# ---------------------------------------------------------------------------
# R1-7 F: run(limit=2) still crawls top 2 from full ranked pool
# ---------------------------------------------------------------------------


def test_run_limit_2_crawls_top_2_from_full_pool(tmp_path, monkeypatch):
    """run(limit=2) must still build the full pool, rank it, and crawl only
    the top 2 -- not truncate discovery before all queries finish."""
    _set_db(tmp_path)

    from app.search import QuerySpec
    from app import pipeline as pm

    specs = (
        QuerySpec("query A", "automation", 100),
        QuerySpec("query B", "ai-consulting", 90),
    )
    monkeypatch.setattr(pm, "DEFAULT_QUERY_SPECS", specs)

    call = {"n": 0}

    def fake_search(query, num=10):
        call["n"] += 1
        if call["n"] == 1:
            return [_agency_hit(f"a-{i}.example", rank=i, query="query A") for i in range(5)]
        return [_agency_hit(f"b-{i}.example", rank=i, query="query B") for i in range(5)]

    monkeypatch.setattr(pm, "search_serper", fake_search)

    crawled: list[str] = []

    def fake_crawl(url):
        from app.scrape import domain_of
        d = domain_of(url)
        crawled.append(d)
        return _make_site(STRONG_TEXT, d, title=d)

    monkeypatch.setattr(pm, "crawl_company", fake_crawl)
    monkeypatch.setattr(
        pm, "analyze_agency",
        lambda *a, **k: {"summary": "s", "services": "ai", "fit_reason": "f",
                         "proof_project": "WingerX", "outreach_angle": "a"},
    )
    monkeypatch.setattr(pm, "draft_outreach", lambda *a, **k: ("Subject", "Body"))

    result = run_pipeline(limit=2)

    # Both queries ran (full pool built, not truncated)
    assert call["n"] == 2
    # Pool has 10 candidates, but only 2 attempted
    assert result["candidate_domains"] == 10
    assert result["attempted"] == 2
    assert len(crawled) == 2
    # Invariant holds
    assert result["attempted"] == result["processed"] + result["skipped"] + result["failed"]

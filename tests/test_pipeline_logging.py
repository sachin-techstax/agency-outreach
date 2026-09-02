"""Tests for pipeline observability, error handling and logging behavior.

All external HTTP (Serper, scraping) and LLM calls are mocked so the tests do
not depend on real network services or API keys.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from app import pipeline as pipeline_mod
from app.config import settings
from app.logging_config import get_logger
from app.pipeline import run as run_pipeline


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


def _setup_search(domains: list[str], monkeypatch):
    """Patch search_serper to return hits for the given domains.

    Each hit includes strong agency identity signals in the title/snippet so
    it passes the pre-crawl candidate quality filter.
    """
    from app.search import SearchHit

    hits = [
        SearchHit(
            title=f"{d} - AI Development Agency",
            url=f"https://{d}",
            snippet="We build AI agents and custom software for clients.",
            query="q",
        )
        for d in domains
    ]

    def fake_search(query, num=10):
        return hits

    monkeypatch.setattr(pipeline_mod, "search_serper", fake_search)


def test_strong_agency_still_scores_and_drafts(tmp_path, monkeypatch):
    _set_db(tmp_path)
    _setup_search(["example.ai"], monkeypatch)

    monkeypatch.setattr(
        pipeline_mod, "crawl_company", lambda url: _make_site(STRONG_TEXT, "example.ai")
    )
    monkeypatch.setattr(
        pipeline_mod,
        "analyze_agency",
        lambda company, website, text: {
            "summary": "s",
            "services": "ai",
            "fit_reason": "fit",
            "proof_project": "WingerX",
            "outreach_angle": "angle",
        },
    )
    monkeypatch.setattr(
        pipeline_mod,
        "draft_outreach",
        lambda company, fit, proof, angle: ("Subject", "Body"),
    )

    result = run_pipeline(limit=1)

    assert result["attempted"] == 1
    assert result["processed"] == 1
    assert result["drafted"] == 1
    assert result["qualified"] == 1
    assert result["failed"] == 0
    # Backwards-compatible keys preserved
    assert "processed" in result
    assert "drafted" in result
    assert "candidate_domains" in result


def test_failing_agency_does_not_terminate_batch(tmp_path, monkeypatch):
    _set_db(tmp_path)
    _setup_search(["broken-ai.dev", "good-ai.com"], monkeypatch)

    def fake_crawl(url):
        if "broken-ai.dev" in url:
            raise ConnectionError("boom")
        return _make_site(STRONG_TEXT, "good-ai.com")

    monkeypatch.setattr(pipeline_mod, "crawl_company", fake_crawl)
    monkeypatch.setattr(
        pipeline_mod,
        "analyze_agency",
        lambda company, website, text: {
            "summary": "s",
            "services": "ai",
            "fit_reason": "fit",
            "proof_project": "WingerX",
            "outreach_angle": "angle",
        },
    )
    monkeypatch.setattr(
        pipeline_mod,
        "draft_outreach",
        lambda company, fit, proof, angle: ("Subject", "Body"),
    )

    result = run_pipeline(limit=2)

    # broken-ai.dev fails (attempt 1), good-ai.com succeeds (attempt 2)
    assert result["attempted"] == 2
    assert result["failed"] == 1
    assert result["processed"] == 1
    assert result["drafted"] == 1
    assert any(d == "broken-ai.dev" for d, _ in result["failures"])


def test_failure_counter_increments(tmp_path, monkeypatch):
    _set_db(tmp_path)
    _setup_search(["a.com", "b.com", "c.com"], monkeypatch)

    def fake_crawl(url):
        raise TimeoutError("timeout")

    monkeypatch.setattr(pipeline_mod, "crawl_company", fake_crawl)

    result = run_pipeline(limit=3)

    assert result["attempted"] == 3
    assert result["failed"] == 3
    assert result["processed"] == 0
    assert result["drafted"] == 0


def test_exception_is_logged(tmp_path, monkeypatch, caplog):
    _set_db(tmp_path)
    _setup_search(["broken-ai.dev", "good-ai.com"], monkeypatch)

    def fake_crawl(url):
        if "broken-ai.dev" in url:
            raise RuntimeError("specific failure xyz")
        return _make_site(STRONG_TEXT, "good-ai.com")

    monkeypatch.setattr(pipeline_mod, "crawl_company", fake_crawl)
    monkeypatch.setattr(
        pipeline_mod,
        "analyze_agency",
        lambda company, website, text: {
            "summary": "s",
            "services": "ai",
            "fit_reason": "fit",
            "proof_project": "WingerX",
            "outreach_angle": "angle",
        },
    )
    monkeypatch.setattr(
        pipeline_mod,
        "draft_outreach",
        lambda company, fit, proof, angle: ("Subject", "Body"),
    )

    with caplog.at_level(logging.ERROR, logger="pipeline"):
        run_pipeline(limit=2)

    assert any("Failed processing agency" in r.message for r in caplog.records)
    assert any("broken-ai.dev" in r.message for r in caplog.records)


def test_missing_serper_key_fails_clearly(monkeypatch):
    object.__setattr__(settings, "serper_api_key", "")
    from app.cli import run_cmd
    import typer

    with pytest.raises(typer.BadParameter):
        # typer.BadParameter is raised when SERPER_API_KEY is missing
        run_cmd(limit=1, verbose=False)


def test_missing_openai_key_fallback_works(tmp_path, monkeypatch):
    _set_db(tmp_path)
    object.__setattr__(settings, "openai_api_key", "")
    _setup_search(["example.ai"], monkeypatch)

    monkeypatch.setattr(
        pipeline_mod, "crawl_company", lambda url: _make_site(STRONG_TEXT, "example.ai")
    )

    result = run_pipeline(limit=1)

    # Fallback analysis/draft path should still produce a draft
    assert result["processed"] == 1
    assert result["drafted"] == 1


def test_logger_does_not_expose_api_keys(tmp_path, monkeypatch, caplog):
    _set_db(tmp_path)
    object.__setattr__(settings, "serper_api_key", "super-secret-serper-key-123")
    object.__setattr__(settings, "openai_api_key", "sk-secret-openai-key-456")
    _setup_search(["example.ai"], monkeypatch)

    monkeypatch.setattr(
        pipeline_mod, "crawl_company", lambda url: _make_site(STRONG_TEXT, "example.ai")
    )
    monkeypatch.setattr(
        pipeline_mod,
        "analyze_agency",
        lambda company, website, text: {
            "summary": "s",
            "services": "ai",
            "fit_reason": "fit",
            "proof_project": "WingerX",
            "outreach_angle": "angle",
        },
    )
    monkeypatch.setattr(
        pipeline_mod,
        "draft_outreach",
        lambda company, fit, proof, angle: ("Subject", "Body"),
    )

    with caplog.at_level(logging.DEBUG):
        run_pipeline(limit=1)

    full_text = " ".join(r.getMessage() for r in caplog.records)
    assert "super-secret-serper-key-123" not in full_text
    assert "sk-secret-openai-key-456" not in full_text


def test_below_threshold_counter(tmp_path, monkeypatch):
    _set_db(tmp_path)
    _setup_search(["branding.com"], monkeypatch)

    weak_text = (
        "Branding agency focused on logo design and graphic design. "
        "We create visual identities, brand guidelines, and creative collateral. "
        "Our studio has been crafting beautiful brands for over ten years. "
        "Contact us at hello@branding.com"
    )
    monkeypatch.setattr(
        pipeline_mod, "crawl_company", lambda url: _make_site(weak_text, "branding.com")
    )
    monkeypatch.setattr(
        pipeline_mod,
        "analyze_agency",
        lambda company, website, text: {
            "summary": "s",
            "services": "",
            "fit_reason": "fit",
            "proof_project": "WingerX",
            "outreach_angle": "angle",
        },
    )

    result = run_pipeline(limit=1)

    assert result["attempted"] == 1
    assert result["processed"] == 1
    assert result["below_score"] == 1
    assert result["drafted"] == 0
    assert result["qualified"] == 0


# ---------------------------------------------------------------------------
# R1-1: CLI banner must show the effective limit, not settings.discovery_limit.
# ---------------------------------------------------------------------------


def test_cli_banner_shows_effective_limit(tmp_path, monkeypatch, capsys):
    _set_db(tmp_path)
    object.__setattr__(settings, "serper_api_key", "fake-key")
    object.__setattr__(settings, "discovery_limit", 15)

    from app.cli import _startup_banner

    _startup_banner(effective_limit=7, effective_log_level="INFO")
    out = capsys.readouterr().out
    assert "Limit:            7" in out
    # Must not show the default discovery_limit when an explicit limit is passed
    assert "Limit:            15" not in out


# ---------------------------------------------------------------------------
# R1-2: attempted semantics and invariant.
# ---------------------------------------------------------------------------


def test_attempted_invariant_holds(tmp_path, monkeypatch):
    """attempted == processed + skipped + failed."""
    _set_db(tmp_path)
    _setup_search(["good.com", "broken.com", "empty.com"], monkeypatch)

    def fake_crawl(url):
        if "broken.com" in url:
            raise ConnectionError("boom")
        if "empty.com" in url:
            return _make_site("tiny", "empty.com")  # < 200 chars -> skipped
        return _make_site(STRONG_TEXT, "good.com")

    monkeypatch.setattr(pipeline_mod, "crawl_company", fake_crawl)
    monkeypatch.setattr(
        pipeline_mod,
        "analyze_agency",
        lambda company, website, text: {
            "summary": "s",
            "services": "ai",
            "fit_reason": "fit",
            "proof_project": "WingerX",
            "outreach_angle": "angle",
        },
    )
    monkeypatch.setattr(
        pipeline_mod,
        "draft_outreach",
        lambda company, fit, proof, angle: ("Subject", "Body"),
    )

    result = run_pipeline(limit=3)

    assert result["attempted"] == 3
    assert result["processed"] == 1
    assert result["skipped"] == 1
    assert result["failed"] == 1
    assert result["attempted"] == result["processed"] + result["skipped"] + result["failed"]


def test_limit_one_never_attempts_second(tmp_path, monkeypatch, caplog):
    """For --limit 1, at most one candidate may be attempted; progress must
    never say 'Processing agency 2/1'."""
    _set_db(tmp_path)
    _setup_search(["broken-ai.dev", "good-ai.com"], monkeypatch)

    def fake_crawl(url):
        if "broken-ai.dev" in url:
            raise ConnectionError("boom")
        return _make_site(STRONG_TEXT, "good-ai.com")

    monkeypatch.setattr(pipeline_mod, "crawl_company", fake_crawl)

    with caplog.at_level(logging.INFO, logger="pipeline"):
        result = run_pipeline(limit=1)

    assert result["attempted"] == 1
    assert result["failed"] == 1
    assert result["processed"] == 0
    # No progress line should ever reference "2/1"
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "2/1" not in messages


# ---------------------------------------------------------------------------
# R1-3: file logging failure must emit a WARNING, not be silent.
# ---------------------------------------------------------------------------


def test_file_logging_failure_emits_warning(tmp_path, caplog):
    from app.logging_config import configure_logging

    # Point LOG_FILE at a path whose parent is a file (cannot be mkdir'd).
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    bad_path = str(blocker / "log.log")

    with caplog.at_level(logging.WARNING, logger="logging_config"):
        configure_logging(level="INFO", log_file=bad_path)

    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "File logging could not be initialized" in messages
    assert bad_path in messages
    # Console handler must still be active
    root = logging.getLogger()
    assert any(getattr(h, "_agency_outreach_owned", False) and isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in root.handlers)
    # We must not have added a NullHandler as a substitute for the failed file handler
    assert not any(getattr(h, "_agency_outreach_owned", False) and isinstance(h, logging.NullHandler) for h in root.handlers)


# ---------------------------------------------------------------------------
# R1-4: db must not log full update values (e.g. draft bodies).
# ---------------------------------------------------------------------------


def test_db_does_not_log_draft_body(tmp_path, caplog):
    from app.db import init_db, update_lead, upsert_lead

    db_path = tmp_path / "test.db"
    object.__setattr__(settings, "db_path", db_path)
    init_db()

    secret_body = "SUPER-SECRET-DRAFT-BODY-CONTENT-XYZ-DO-NOT-LEAK"
    lead_id = upsert_lead({
        "company": "Co",
        "domain": "co.com",
        "website": "https://co.com",
        "status": "qualified",
    })

    with caplog.at_level(logging.DEBUG, logger="db"):
        update_lead(lead_id, subject="Subject", draft=secret_body, status="drafted")

    full_text = " ".join(r.getMessage() for r in caplog.records)
    assert secret_body not in full_text
    # Field names and status should appear
    assert "draft" in full_text
    assert "status=drafted" in full_text


# ---------------------------------------------------------------------------
# R1-5: discovery must fail clearly if ALL searches fail, but continue if at
# least one succeeds.
# ---------------------------------------------------------------------------


def test_all_searches_fail_raises_clearly(tmp_path, monkeypatch):
    _set_db(tmp_path)

    def fake_search(query, num=10):
        raise RuntimeError("serper down")

    monkeypatch.setattr(pipeline_mod, "search_serper", fake_search)

    with pytest.raises(RuntimeError, match="Agency discovery failed"):
        run_pipeline(limit=1)


def test_one_search_fails_another_succeeds_continues(tmp_path, monkeypatch):
    _set_db(tmp_path)
    from app.search import SearchHit

    call_count = {"n": 0}

    def fake_search(query, num=10):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("serper transient error")
        return [SearchHit(title="Good AI Agency", url="https://good.com", snippet="We build AI solutions for clients.", query=query)]

    monkeypatch.setattr(pipeline_mod, "search_serper", fake_search)
    monkeypatch.setattr(
        pipeline_mod, "crawl_company", lambda url: _make_site(STRONG_TEXT, "good.com")
    )
    monkeypatch.setattr(
        pipeline_mod,
        "analyze_agency",
        lambda company, website, text: {
            "summary": "s",
            "services": "ai",
            "fit_reason": "fit",
            "proof_project": "WingerX",
            "outreach_angle": "angle",
        },
    )
    monkeypatch.setattr(
        pipeline_mod,
        "draft_outreach",
        lambda company, fit, proof, angle: ("Subject", "Body"),
    )

    result = run_pipeline(limit=1)

    # Pipeline continued despite the first search failing
    assert result["attempted"] == 1
    assert result["processed"] == 1
    assert result["candidate_domains"] == 1


def test_search_returning_zero_results_is_not_failure(tmp_path, monkeypatch):
    """A successful search that returns zero organic results must not be
    treated as an API failure, and must not trigger the all-failed error."""
    _set_db(tmp_path)

    def fake_search(query, num=10):
        return []  # successful but empty

    monkeypatch.setattr(pipeline_mod, "search_serper", fake_search)

    # Should NOT raise; just produce an empty batch.
    result = run_pipeline(limit=1)
    assert result["candidate_domains"] == 0
    assert result["attempted"] == 0


# ---------------------------------------------------------------------------
# R2-1: Outcome counter invariant — processed and failed are mutually exclusive.
# ---------------------------------------------------------------------------


def test_draft_outreach_failure_counts_as_failed_not_processed(tmp_path, monkeypatch):
    """If draft_outreach raises after a successful crawl/analysis/upsert,
    the attempt must count as failed (not processed) so the invariant
    attempted == processed + skipped + failed holds."""
    _set_db(tmp_path)
    _setup_search(["example.ai"], monkeypatch)

    monkeypatch.setattr(
        pipeline_mod, "crawl_company", lambda url: _make_site(STRONG_TEXT, "example.ai")
    )
    monkeypatch.setattr(
        pipeline_mod,
        "analyze_agency",
        lambda company, website, text: {
            "summary": "s",
            "services": "ai",
            "fit_reason": "fit",
            "proof_project": "WingerX",
            "outreach_angle": "angle",
        },
    )

    def failing_draft(company, fit, proof, angle):
        raise RuntimeError("LLM outreach generation exploded")

    monkeypatch.setattr(pipeline_mod, "draft_outreach", failing_draft)

    result = run_pipeline(limit=1)

    assert result["attempted"] == 1
    assert result["processed"] == 0
    assert result["skipped"] == 0
    assert result["failed"] == 1
    assert result["drafted"] == 0
    # Invariant must hold
    assert result["attempted"] == result["processed"] + result["skipped"] + result["failed"]
    assert any(d == "example.ai" for d, _ in result["failures"])


def test_db_update_failure_after_qualification_counts_as_failed(tmp_path, monkeypatch):
    """If the final update_lead (status=drafted) raises after qualification,
    the attempt must count as failed, not processed."""
    _set_db(tmp_path)
    _setup_search(["example.ai"], monkeypatch)

    monkeypatch.setattr(
        pipeline_mod, "crawl_company", lambda url: _make_site(STRONG_TEXT, "example.ai")
    )
    monkeypatch.setattr(
        pipeline_mod,
        "analyze_agency",
        lambda company, website, text: {
            "summary": "s",
            "services": "ai",
            "fit_reason": "fit",
            "proof_project": "WingerX",
            "outreach_angle": "angle",
        },
    )
    monkeypatch.setattr(
        pipeline_mod,
        "draft_outreach",
        lambda company, fit, proof, angle: ("Subject", "Body"),
    )

    original_update_lead = pipeline_mod.update_lead

    def failing_update(lead_id, **updates):
        if updates.get("status") == "drafted":
            raise sqlite3.OperationalError("database is locked")
        return original_update_lead(lead_id, **updates)

    monkeypatch.setattr(pipeline_mod, "update_lead", failing_update)

    result = run_pipeline(limit=1)

    assert result["attempted"] == 1
    assert result["processed"] == 0
    assert result["skipped"] == 0
    assert result["failed"] == 1
    assert result["drafted"] == 0
    assert result["attempted"] == result["processed"] + result["skipped"] + result["failed"]


def test_invariant_holds_on_mixed_batch_with_late_failure(tmp_path, monkeypatch):
    """A batch with a successful agency, a late-failing agency, and a skipped
    agency must still satisfy attempted == processed + skipped + failed."""
    _set_db(tmp_path)
    _setup_search(["good.com", "late-fail.com", "empty.com"], monkeypatch)

    def fake_crawl(url):
        if "empty.com" in url:
            return _make_site("tiny", "empty.com")  # < 200 chars -> skipped
        domain = "good.com" if "good.com" in url else "late-fail.com"
        return _make_site(STRONG_TEXT, domain, title=domain)

    monkeypatch.setattr(pipeline_mod, "crawl_company", fake_crawl)
    monkeypatch.setattr(
        pipeline_mod,
        "analyze_agency",
        lambda company, website, text: {
            "summary": "s",
            "services": "ai",
            "fit_reason": "fit",
            "proof_project": "WingerX",
            "outreach_angle": "angle",
        },
    )

    def conditional_draft(company, fit, proof, angle):
        if "late-fail" in company.lower():
            raise RuntimeError("outreach generation failed for late-fail")
        return ("Subject", "Body")

    monkeypatch.setattr(pipeline_mod, "draft_outreach", conditional_draft)

    result = run_pipeline(limit=3)

    assert result["attempted"] == 3
    assert result["processed"] == 1  # only good.com
    assert result["skipped"] == 1    # empty.com
    assert result["failed"] == 1     # late-fail.com
    assert result["drafted"] == 1
    assert result["attempted"] == result["processed"] + result["skipped"] + result["failed"]


# ---------------------------------------------------------------------------
# R2-2: Banner must show the effective log level for the invocation.
# ---------------------------------------------------------------------------


def test_cli_banner_shows_verbose_log_level(tmp_path, monkeypatch, capsys):
    _set_db(tmp_path)
    object.__setattr__(settings, "serper_api_key", "fake-key")
    object.__setattr__(settings, "log_level", "INFO")

    from app.cli import _startup_banner

    _startup_banner(effective_limit=5, effective_log_level="DEBUG")
    out = capsys.readouterr().out
    assert "Log level:        DEBUG" in out
    assert "Log level:        INFO" not in out


def test_cli_banner_shows_env_log_level_without_verbose(tmp_path, monkeypatch, capsys):
    _set_db(tmp_path)
    object.__setattr__(settings, "serper_api_key", "fake-key")
    object.__setattr__(settings, "log_level", "INFO")

    from app.cli import _startup_banner

    _startup_banner(effective_limit=5, effective_log_level="INFO")
    out = capsys.readouterr().out
    assert "Log level:        INFO" in out


# ---------------------------------------------------------------------------
# Candidate quality: Reddit regression + discovery filtering.
# ---------------------------------------------------------------------------


def test_reddit_regression_rejected_before_attempt(tmp_path, monkeypatch, caplog):
    """The exact real-world bug: Reddit appears first in search results and
    consumes the only --limit 1 attempt.  After the fix, Reddit must be
    rejected by candidate filtering BEFORE any processing attempt, and the
    real agency gets the attempt instead."""
    _set_db(tmp_path)
    from app.search import SearchHit

    # Simulate Serper returning Reddit first, then a real agency.
    hits = [
        SearchHit(
            title="Best AI tools - Reddit",
            url="https://www.reddit.com/r/MachineLearning/comments/abc",
            snippet="Discussion about AI tools",
            query="q",
        ),
        SearchHit(
            title="Real AI Agency - Generative AI Development & Automation",
            url="https://real-ai-agency.example/services",
            snippet="We build LLM applications, AI agents and custom software.",
            query="q",
        ),
    ]

    def fake_search(query, num=10):
        return hits

    monkeypatch.setattr(pipeline_mod, "search_serper", fake_search)
    monkeypatch.setattr(
        pipeline_mod,
        "crawl_company",
        lambda url: _make_site(STRONG_TEXT, "real-ai-agency.example"),
    )
    monkeypatch.setattr(
        pipeline_mod,
        "analyze_agency",
        lambda company, website, text: {
            "summary": "s",
            "services": "ai",
            "fit_reason": "fit",
            "proof_project": "WingerX",
            "outreach_angle": "angle",
        },
    )
    monkeypatch.setattr(
        pipeline_mod,
        "draft_outreach",
        lambda company, fit, proof, angle: ("Subject", "Body"),
    )

    with caplog.at_level(logging.DEBUG, logger="pipeline"):
        result = run_pipeline(limit=1)

    # Reddit was rejected, not attempted
    assert result["attempted"] == 1
    assert result["processed"] == 1
    assert result["skipped"] == 0
    assert result["failed"] == 0
    # The attempted domain is the real agency, not reddit
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "real-ai-agency.example" in messages
    # No progress line for reddit
    assert "Processing agency 1/1: reddit.com" not in messages
    assert "Processing agency 1/1: real-ai-agency.example" in messages
    # DEBUG rejection log for reddit
    assert any("reddit.com" in r.getMessage() and "blocked-domain" in r.getMessage() for r in caplog.records)
    # Discovery stats
    assert result["raw_candidate_domains"] == 2
    assert result["rejected_candidate_domains"] == 1
    assert result["candidate_domains"] == 1


def test_all_results_rejected_returns_zero_candidate_batch(tmp_path, monkeypatch):
    """If Serper works but every result is rejected by candidate filtering,
    return a valid zero-candidate batch (NOT a search failure)."""
    _set_db(tmp_path)
    from app.search import SearchHit

    hits = [
        SearchHit(title="Reddit thread", url="https://www.reddit.com/r/ai", snippet="", query="q"),
        SearchHit(title="Quora answer", url="https://www.quora.com/ai", snippet="", query="q"),
    ]

    def fake_search(query, num=10):
        return hits

    monkeypatch.setattr(pipeline_mod, "search_serper", fake_search)

    result = run_pipeline(limit=1)

    # Not a failure -- just no eligible candidates
    assert result["raw_candidate_domains"] == 2
    assert result["rejected_candidate_domains"] == 2
    assert result["candidate_domains"] == 0
    assert result["attempted"] == 0
    assert result["processed"] == 0
    assert result["failed"] == 0
    assert result["skipped"] == 0


def test_discovery_stats_in_summary(tmp_path, monkeypatch):
    """The final summary must include raw_candidate_domains and
    rejected_candidate_domains alongside candidate_domains."""
    _set_db(tmp_path)
    from app.search import SearchHit

    hits = [
        SearchHit(title="Reddit", url="https://www.reddit.com/r/ai", snippet="", query="q"),
        SearchHit(
            title="Acme AI - AI Development Agency",
            url="https://acme-ai.com/",
            snippet="We build AI agents and automation.",
            query="q",
        ),
    ]

    def fake_search(query, num=10):
        return hits

    monkeypatch.setattr(pipeline_mod, "search_serper", fake_search)
    monkeypatch.setattr(
        pipeline_mod,
        "crawl_company",
        lambda url: _make_site(STRONG_TEXT, "acme-ai.com"),
    )
    monkeypatch.setattr(
        pipeline_mod,
        "analyze_agency",
        lambda company, website, text: {
            "summary": "s",
            "services": "ai",
            "fit_reason": "fit",
            "proof_project": "WingerX",
            "outreach_angle": "angle",
        },
    )
    monkeypatch.setattr(
        pipeline_mod,
        "draft_outreach",
        lambda company, fit, proof, angle: ("Subject", "Body"),
    )

    result = run_pipeline(limit=1)

    assert "raw_candidate_domains" in result
    assert "rejected_candidate_domains" in result
    assert "candidate_domains" in result
    assert result["raw_candidate_domains"] == 2
    assert result["rejected_candidate_domains"] == 1
    assert result["candidate_domains"] == 1


def test_dedup_by_normalized_domain(tmp_path, monkeypatch):
    """Multiple results from the same domain must collapse to one candidate."""
    _set_db(tmp_path)
    from app.search import SearchHit

    hits = [
        SearchHit(title="Agency Blog", url="https://www.agency.ai/blog/agents", snippet="AI agency blog", query="q"),
        SearchHit(title="Agency Services", url="https://agency.ai/services", snippet="AI development services", query="q"),
        SearchHit(title="Agency About", url="https://agency.ai/about", snippet="About our AI consultancy", query="q"),
    ]

    def fake_search(query, num=10):
        return hits

    monkeypatch.setattr(pipeline_mod, "search_serper", fake_search)
    monkeypatch.setattr(
        pipeline_mod,
        "crawl_company",
        lambda url: _make_site(STRONG_TEXT, "agency.ai"),
    )
    monkeypatch.setattr(
        pipeline_mod,
        "analyze_agency",
        lambda company, website, text: {
            "summary": "s",
            "services": "ai",
            "fit_reason": "fit",
            "proof_project": "WingerX",
            "outreach_angle": "angle",
        },
    )
    monkeypatch.setattr(
        pipeline_mod,
        "draft_outreach",
        lambda company, fit, proof, angle: ("Subject", "Body"),
    )

    result = run_pipeline(limit=1)

    # Three URLs from the same domain -> one candidate
    assert result["raw_candidate_domains"] == 1
    assert result["candidate_domains"] == 1
    assert result["attempted"] == 1


# ---------------------------------------------------------------------------
# R1-1: Same-domain rejection must not poison later acceptable hits.
# ---------------------------------------------------------------------------


def test_same_domain_rejected_then_accepted(tmp_path, monkeypatch):
    """R1-7 test A: A rejected first URL must not permanently reject the
    domain if a later hit from the same domain is acceptable."""
    _set_db(tmp_path)
    from app.search import SearchHit

    hits = [
        SearchHit(
            title="Top 10 AI Agencies",
            url="https://realagency.com/top-10-ai-agencies",
            snippet="A list of the best AI agencies.",
            query="q",
        ),
        SearchHit(
            title="Generative AI Development Services",
            url="https://realagency.com/services/generative-ai",
            snippet="We build AI agents and LLM applications for clients.",
            query="q",
        ),
    ]

    def fake_search(query, num=10):
        return hits

    monkeypatch.setattr(pipeline_mod, "search_serper", fake_search)
    monkeypatch.setattr(
        pipeline_mod,
        "crawl_company",
        lambda url: _make_site(STRONG_TEXT, "realagency.com"),
    )
    monkeypatch.setattr(
        pipeline_mod,
        "analyze_agency",
        lambda company, website, text: {
            "summary": "s",
            "services": "ai",
            "fit_reason": "fit",
            "proof_project": "WingerX",
            "outreach_angle": "angle",
        },
    )
    monkeypatch.setattr(
        pipeline_mod,
        "draft_outreach",
        lambda company, fit, proof, angle: ("Subject", "Body"),
    )

    result = run_pipeline(limit=1)

    # The domain was observed once, the listicle was rejected, but the services
    # page was accepted.  So the domain is NOT counted as rejected.
    assert result["raw_candidate_domains"] == 1
    assert result["rejected_candidate_domains"] == 0
    assert result["candidate_domains"] == 1
    assert result["attempted"] == 1
    assert result["processed"] == 1


def test_same_domain_two_rejected(tmp_path, monkeypatch):
    """R1-7 test B: Two rejected URLs from the same domain → raw=1,
    rejected=1, eligible=0."""
    _set_db(tmp_path)
    from app.search import SearchHit

    hits = [
        SearchHit(
            title="Top 10 AI Agencies",
            url="https://realagency.com/top-10-ai-agencies",
            snippet="A list of the best AI agencies.",
            query="q",
        ),
        SearchHit(
            title="Discussion: AI Tools",
            url="https://realagency.com/thread/123",
            snippet="Join the community discussion about AI tools.",
            query="q",
        ),
    ]

    def fake_search(query, num=10):
        return hits

    monkeypatch.setattr(pipeline_mod, "search_serper", fake_search)

    result = run_pipeline(limit=1)

    assert result["raw_candidate_domains"] == 1
    assert result["rejected_candidate_domains"] == 1
    assert result["candidate_domains"] == 0
    assert result["attempted"] == 0


# ---------------------------------------------------------------------------
# Commercial fit: LLM must NOT be called for below-threshold candidates.
# ---------------------------------------------------------------------------

WEAK_TEXT = (
    "AI agent platform for building conversational AI. Start free today. "
    "Pricing plans for every team. Our SaaS platform offers self-service "
    "deployment. Subscribe to our developer platform today. API documentation and "
    "developer tools available. Product documentation and SDK access. "
    "Sign up free and start building your first AI agent in minutes. "
    "Our self-service platform makes it easy to deploy AI solutions."
)


def test_below_threshold_skips_llm_analysis_and_outreach(tmp_path, monkeypatch):
    """A below-threshold candidate must NOT invoke analyze_agency or
    draft_outreach.  The pipeline should persist lightweight lead data and
    count it as processed + below_score."""
    _set_db(tmp_path)
    _setup_search(["saas-platform.com"], monkeypatch)

    monkeypatch.setattr(
        pipeline_mod, "crawl_company", lambda url: _make_site(WEAK_TEXT, "saas-platform.com")
    )

    # If analyze_agency is called, raise to make the test fail loudly.
    def fail_if_called(*args, **kwargs):
        raise AssertionError("analyze_agency should NOT be called for below-threshold candidate")

    monkeypatch.setattr(pipeline_mod, "analyze_agency", fail_if_called)

    def fail_draft_if_called(*args, **kwargs):
        raise AssertionError("draft_outreach should NOT be called for below-threshold candidate")

    monkeypatch.setattr(pipeline_mod, "draft_outreach", fail_draft_if_called)

    result = run_pipeline(limit=1)

    assert result["attempted"] == 1
    assert result["processed"] == 1
    assert result["below_score"] == 1
    assert result["qualified"] == 0
    assert result["drafted"] == 0
    assert result["failed"] == 0
    # LLM counters
    assert result["llm_analysis_calls"] == 0
    assert result["llm_skipped_below_threshold"] == 1
    assert result["outreach_drafts_generated"] == 0
    # Invariant
    assert result["attempted"] == result["processed"] + result["skipped"] + result["failed"]


def test_qualified_lead_calls_llm_and_generates_draft(tmp_path, monkeypatch):
    """A qualified candidate must invoke analyze_agency and draft_outreach."""
    _set_db(tmp_path)
    _setup_search(["good-agency.com"], monkeypatch)

    analysis_called = {"n": 0}
    draft_called = {"n": 0}

    monkeypatch.setattr(
        pipeline_mod, "crawl_company", lambda url: _make_site(STRONG_TEXT, "good-agency.com")
    )

    def track_analysis(company, website, text):
        analysis_called["n"] += 1
        return {
            "summary": "s",
            "services": "ai",
            "fit_reason": "fit",
            "proof_project": "WingerX",
            "outreach_angle": "angle",
        }

    def track_draft(company, fit, proof, angle):
        draft_called["n"] += 1
        return ("Subject", "Body")

    monkeypatch.setattr(pipeline_mod, "analyze_agency", track_analysis)
    monkeypatch.setattr(pipeline_mod, "draft_outreach", track_draft)

    result = run_pipeline(limit=1)

    assert result["attempted"] == 1
    assert result["processed"] == 1
    assert result["qualified"] == 1
    assert result["drafted"] == 1
    assert result["below_score"] == 0
    assert analysis_called["n"] == 1
    assert draft_called["n"] == 1
    assert result["llm_analysis_calls"] == 1
    assert result["llm_skipped_below_threshold"] == 0
    assert result["outreach_drafts_generated"] == 1


def test_llm_counters_in_summary(tmp_path, monkeypatch):
    """The summary must include LLM call counters."""
    _set_db(tmp_path)
    _setup_search(["good.com", "bad.com"], monkeypatch)

    def fake_crawl(url):
        if "bad.com" in url:
            return _make_site(WEAK_TEXT, "bad.com")
        return _make_site(STRONG_TEXT, "good.com")

    monkeypatch.setattr(pipeline_mod, "crawl_company", fake_crawl)
    monkeypatch.setattr(
        pipeline_mod,
        "analyze_agency",
        lambda company, website, text: {
            "summary": "s",
            "services": "ai",
            "fit_reason": "fit",
            "proof_project": "WingerX",
            "outreach_angle": "angle",
        },
    )
    monkeypatch.setattr(
        pipeline_mod,
        "draft_outreach",
        lambda company, fit, proof, angle: ("Subject", "Body"),
    )

    result = run_pipeline(limit=2)

    assert "llm_analysis_calls" in result
    assert "llm_skipped_below_threshold" in result
    assert "outreach_drafts_generated" in result
    assert result["llm_analysis_calls"] == 1
    assert result["llm_skipped_below_threshold"] == 1
    assert result["outreach_drafts_generated"] == 1

"""Tests for pipeline observability, error handling and logging behavior.

All external HTTP (Serper, scraping) and LLM calls are mocked so the tests do
not depend on real network services or API keys.
"""
from __future__ import annotations

import json
import logging
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
    "Generative AI development agency. We build AI agents, workflow automation, "
    "custom software, RAG systems, APIs and backend products. See our client case studies. "
    "We deliver production AI systems for clients across multiple industries. "
    "Our team specializes in LLM development, retrieval augmented generation, "
    "and end-to-end AI product engineering. Contact us at hello@example.ai"
)


def _setup_search(domains: list[str], monkeypatch):
    """Patch search_serper to return hits for the given domains."""
    from app.search import SearchHit

    hits = [
        SearchHit(title=d, url=f"https://{d}", snippet="", query="q")
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

    _startup_banner(effective_limit=7)
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
        return [SearchHit(title="good.com", url="https://good.com", snippet="", query=query)]

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

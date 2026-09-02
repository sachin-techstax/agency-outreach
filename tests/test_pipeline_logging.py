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

    result = run_pipeline(limit=1)

    # broken-ai.dev fails, then good-ai.com succeeds and reaches processed=1
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

    result = run_pipeline(limit=1)

    assert result["failed"] >= 1
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
        run_pipeline(limit=1)

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

    assert result["processed"] == 1
    assert result["below_score"] == 1
    assert result["drafted"] == 0
    assert result["qualified"] == 0

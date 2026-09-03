"""Tests for existing-lead suppression in normal discovery runs.

Covers:
- Suppression policy (drafted, approved, gmail_drafted, sent, do_not_contact)
- Retryable statuses (rejected-fit, rejected, discovered, qualified)
- Limit backfill semantics (suppressed leads do not consume attempt slots)
- All-suppressed pool (no error, attempted=0)
- do_not_contact CLI behavior and suppression
- No crawl/LLM/DB mutation for suppressed leads
- Bulk DB lookup
- Domain normalization consistency
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest
from typer.testing import CliRunner

from app import pipeline as pipeline_mod
from app.cli import app as cli_app
from app.config import settings
from app.db import (
    SUPPRESSED_STATUSES,
    get_lead_statuses_by_domains,
    get_suppressed_domains,
    init_db,
    is_suppressed_status,
    now_iso,
)
from app.pipeline import run as run_pipeline


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


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


def _insert_existing_lead(db_path, domain, status, **extra):
    """Insert a lead with a specific status and extra fields for testing."""
    init_db()
    stamp = now_iso()
    data = {
        "company": "Test Co",
        "domain": domain,
        "website": f"https://{domain}",
        "source_query": "q",
        "source_url": f"https://{domain}",
        "summary": "old summary",
        "services": "old services",
        "score": 80,
        "score_reasons": "[]",
        "fit_reason": "old fit",
        "proof_project": "WingerX",
        "outreach_angle": "old angle",
        "contact_email": "founder@old.com",
        "contact_source": "website",
        "contact_name": "",
        "contact_role": "Founder",
        "contact_quality": "high",
        "subject": "Old subject",
        "draft": "Old draft body",
        "status": status,
        "gmail_draft_id": "gmail-123",
        "created_at": stamp,
        "updated_at": stamp,
        "last_contact_at": "2026-01-01T00:00:00+00:00",
        "followup_due_at": "2026-01-05T00:00:00+00:00",
    }
    data.update(extra)
    with sqlite3.connect(str(db_path)) as conn:
        cols = list(data.keys())
        vals = list(data.values())
        q = ",".join("?" for _ in cols)
        conn.execute(f"INSERT INTO leads ({','.join(cols)}) VALUES ({q})", vals)
        conn.commit()


def _get_lead_row(db_path, domain):
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM leads WHERE domain=?", (domain,)).fetchone()


def _setup_search(domains: list[str], monkeypatch):
    """Patch search_serper to return hits for the given domains."""
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


def _setup_crawl_and_llm(monkeypatch, crawled: list[str] | None = None):
    """Patch crawl/LLM to succeed.  If `crawled` is provided, append each
    crawled domain to it for assertion."""
    if crawled is None:
        crawled = []

    def fake_crawl(url):
        from app.scrape import domain_of
        d = domain_of(url)
        crawled.append(d)
        return _make_site(STRONG_TEXT, d)

    monkeypatch.setattr(pipeline_mod, "crawl_company", fake_crawl)
    monkeypatch.setattr(
        pipeline_mod, "analyze_agency",
        lambda *a, **k: {"summary": "s", "services": "ai", "fit_reason": "f",
                         "proof_project": "WingerX", "outreach_angle": "a"},
    )
    monkeypatch.setattr(pipeline_mod, "draft_outreach", lambda *a, **k: ("Subject", "Body"))
    return crawled


# ===========================================================================
# 1. Suppression policy unit tests
# ===========================================================================


class TestSuppressionPolicy:
    def test_suppressed_statuses(self):
        assert "drafted" in SUPPRESSED_STATUSES
        assert "approved" in SUPPRESSED_STATUSES
        assert "gmail_drafted" in SUPPRESSED_STATUSES
        assert "sent" in SUPPRESSED_STATUSES
        assert "do_not_contact" in SUPPRESSED_STATUSES

    def test_retryable_statuses_not_suppressed(self):
        assert "rejected-fit" not in SUPPRESSED_STATUSES
        assert "rejected" not in SUPPRESSED_STATUSES
        assert "discovered" not in SUPPRESSED_STATUSES
        assert "qualified" not in SUPPRESSED_STATUSES

    @pytest.mark.parametrize("status", ["drafted", "approved", "gmail_drafted", "sent", "do_not_contact"])
    def test_is_suppressed_status_true(self, status):
        assert is_suppressed_status(status) is True

    @pytest.mark.parametrize("status", ["rejected-fit", "rejected", "discovered", "qualified"])
    def test_is_suppressed_status_false(self, status):
        assert is_suppressed_status(status) is False

    def test_is_suppressed_status_none(self):
        assert is_suppressed_status(None) is False


# ===========================================================================
# 2. Bulk DB lookup tests
# ===========================================================================


class TestBulkDBLookup:
    def test_get_lead_statuses_by_domains_returns_existing(self, tmp_path):
        db_path = _set_db(tmp_path)
        _insert_existing_lead(db_path, "a.example", "drafted")
        _insert_existing_lead(db_path, "b.example", "sent")

        result = get_lead_statuses_by_domains(["a.example", "b.example", "c.example"])
        assert result["a.example"] == "drafted"
        assert result["b.example"] == "sent"
        assert "c.example" not in result  # no existing lead

    def test_get_lead_statuses_by_domains_empty_input(self, tmp_path):
        _set_db(tmp_path)
        assert get_lead_statuses_by_domains([]) == {}

    def test_get_suppressed_domains_filters_retryable(self, tmp_path):
        db_path = _set_db(tmp_path)
        _insert_existing_lead(db_path, "drafted.example", "drafted")
        _insert_existing_lead(db_path, "sent.example", "sent")
        _insert_existing_lead(db_path, "rejected.example", "rejected")
        _insert_existing_lead(db_path, "rejectedfit.example", "rejected-fit")

        result = get_suppressed_domains([
            "drafted.example", "sent.example",
            "rejected.example", "rejectedfit.example",
            "new.example",
        ])
        assert "drafted.example" in result
        assert "sent.example" in result
        assert "rejected.example" not in result
        assert "rejectedfit.example" not in result
        assert "new.example" not in result

    def test_get_suppressed_domains_single_query(self, tmp_path, monkeypatch):
        """Verify the bulk lookup uses a single SQL query (not one per domain)."""
        db_path = _set_db(tmp_path)
        _insert_existing_lead(db_path, "a.example", "drafted")
        _insert_existing_lead(db_path, "b.example", "sent")

        # Patch the db.conn context manager to wrap the connection with a
        # counting cursor.  We count queries containing the suppression SELECT.
        from app import db as db_mod

        call_count = {"n": 0}
        original_conn = db_mod.conn

        @contextmanager
        def counting_conn():
            with original_conn() as real_db:
                class CountingWrapper:
                    def __getattr__(self, name):
                        return getattr(real_db, name)
                    def execute(self, sql, *args, **kwargs):
                        if "SELECT domain, status FROM leads" in sql:
                            call_count["n"] += 1
                        return real_db.execute(sql, *args, **kwargs)
                    row_factory = real_db.row_factory
                yield CountingWrapper()

        monkeypatch.setattr(db_mod, "conn", counting_conn)
        get_suppressed_domains(["a.example", "b.example", "c.example", "d.example"])
        assert call_count["n"] == 1  # single query for all domains


# ===========================================================================
# 3. Limit backfill test (R1-15)
# ===========================================================================


class TestLimitBackfill:
    def test_suppressed_leads_do_not_consume_attempt_slots(self, tmp_path, monkeypatch):
        """run(limit=3) with sent, drafted, approved at top of ranking must
        backfill with new-a, new-b, new-c — not waste slots on suppressed."""
        db_path = _set_db(tmp_path)
        _insert_existing_lead(db_path, "sent.example", "sent")
        _insert_existing_lead(db_path, "drafted.example", "drafted")
        _insert_existing_lead(db_path, "approved.example", "approved")

        # Ranked pool order: sent, drafted, approved, new-a, new-b, new-c
        _setup_search(
            ["sent.example", "drafted.example", "approved.example",
             "new-a.example", "new-b.example", "new-c.example"],
            monkeypatch,
        )

        crawled: list[str] = []
        _setup_crawl_and_llm(monkeypatch, crawled)

        result = run_pipeline(limit=3)

        assert result["attempted"] == 3
        assert result["suppressed_existing"] == 3
        assert set(crawled) == {"new-a.example", "new-b.example", "new-c.example"}
        assert "sent.example" not in crawled
        assert "drafted.example" not in crawled
        assert "approved.example" not in crawled
        # Invariant holds
        assert result["attempted"] == result["processed"] + result["skipped"] + result["failed"]


# ===========================================================================
# 4. All-suppressed test (R1-16)
# ===========================================================================


class TestAllSuppressed:
    def test_all_suppressed_pool_no_error(self, tmp_path, monkeypatch):
        """If ranked pool contains only suppressed domains, run succeeds with
        attempted=0 and no error."""
        db_path = _set_db(tmp_path)
        _insert_existing_lead(db_path, "sent.example", "sent")
        _insert_existing_lead(db_path, "drafted.example", "drafted")

        _setup_search(["sent.example", "drafted.example"], monkeypatch)

        def fail_if_crawl(*a, **k):
            raise AssertionError("crawl_company should NOT be called for suppressed leads")

        monkeypatch.setattr(pipeline_mod, "crawl_company", fail_if_crawl)

        result = run_pipeline(limit=10)

        assert result["attempted"] == 0
        assert result["processed"] == 0
        assert result["failed"] == 0
        assert result["suppressed_existing"] == 2
        assert result["fresh_retryable_pool"] == 0


# ===========================================================================
# 5. Existing draft regression (R1-13)
# ===========================================================================


class TestExistingDraftRegression:
    def test_drafted_lead_suppressed_before_crawl(self, tmp_path, monkeypatch):
        """theaiautomationagency.ai status=drafted, ranked highly again:
        suppressed before crawl, no LLM, no new draft, DB row unchanged."""
        db_path = _set_db(tmp_path)
        _insert_existing_lead(
            db_path, "theaiautomationagency.ai", "drafted",
            subject="Existing subject",
            draft="Existing draft body",
        )
        _setup_search(["theaiautomationagency.ai"], monkeypatch)

        def fail_if_crawl(*a, **k):
            raise AssertionError("crawl_company should NOT be called for suppressed drafted lead")

        monkeypatch.setattr(pipeline_mod, "crawl_company", fail_if_crawl)
        monkeypatch.setattr(
            pipeline_mod, "analyze_agency",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")),
        )
        monkeypatch.setattr(
            pipeline_mod, "draft_outreach",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")),
        )

        result = run_pipeline(limit=1)

        assert result["attempted"] == 0
        assert result["suppressed_existing"] == 1
        assert result["llm_analysis_calls"] == 0
        assert result["outreach_draft_calls"] == 0
        assert result["drafted"] == 0

        row = _get_lead_row(db_path, "theaiautomationagency.ai")
        assert row["status"] == "drafted"
        assert row["subject"] == "Existing subject"
        assert row["draft"] == "Existing draft body"


# ===========================================================================
# 6. Sent regression (R1-14)
# ===========================================================================


class TestSentRegression:
    def test_sent_lead_does_not_consume_attempt_slot(self, tmp_path, monkeypatch):
        """agency.example status=sent, ranked #1: suppressed, next fresh
        candidate becomes attempt #1."""
        db_path = _set_db(tmp_path)
        _insert_existing_lead(db_path, "agency.example", "sent")
        _setup_search(["agency.example", "fresh.example"], monkeypatch)

        crawled: list[str] = []
        _setup_crawl_and_llm(monkeypatch, crawled)

        result = run_pipeline(limit=1)

        assert result["attempted"] == 1
        assert result["suppressed_existing"] == 1
        assert crawled == ["fresh.example"]
        assert "agency.example" not in crawled

        # Sent lead DB row unchanged
        row = _get_lead_row(db_path, "agency.example")
        assert row["status"] == "sent"


# ===========================================================================
# 7. Retryable leads are NOT suppressed (R1-11)
# ===========================================================================


class TestRetryableLeads:
    @pytest.mark.parametrize("status", ["rejected-fit", "rejected", "discovered", "qualified"])
    def test_retryable_status_is_crawled(self, tmp_path, monkeypatch, status):
        """Leads in retryable statuses must be crawled and re-qualified."""
        db_path = _set_db(tmp_path)
        _insert_existing_lead(db_path, "retry.example", status)
        _setup_search(["retry.example"], monkeypatch)

        crawled: list[str] = []
        _setup_crawl_and_llm(monkeypatch, crawled)

        result = run_pipeline(limit=1)

        assert result["attempted"] == 1
        assert result["suppressed_existing"] == 0
        assert crawled == ["retry.example"]


# ===========================================================================
# 8. do_not_contact tests (R1-17)
# ===========================================================================


class TestDoNotContact:
    def test_do_not_contact_lead_is_suppressed(self, tmp_path, monkeypatch):
        """A do_not_contact lead must be suppressed before crawl."""
        db_path = _set_db(tmp_path)
        _insert_existing_lead(db_path, "dnc.example", "do_not_contact")
        _setup_search(["dnc.example", "fresh.example"], monkeypatch)

        crawled: list[str] = []
        _setup_crawl_and_llm(monkeypatch, crawled)

        result = run_pipeline(limit=1)

        assert result["attempted"] == 1
        assert result["suppressed_existing"] == 1
        assert "dnc.example" not in crawled
        assert crawled == ["fresh.example"]

    def test_do_not_contact_lead_not_crawled(self, tmp_path, monkeypatch):
        """do_not_contact lead alone in pool: not crawled, no error."""
        db_path = _set_db(tmp_path)
        _insert_existing_lead(db_path, "dnc.example", "do_not_contact")
        _setup_search(["dnc.example"], monkeypatch)

        def fail_if_crawl(*a, **k):
            raise AssertionError("crawl_company should NOT be called for do_not_contact lead")

        monkeypatch.setattr(pipeline_mod, "crawl_company", fail_if_crawl)

        result = run_pipeline(limit=5)

        assert result["attempted"] == 0
        assert result["suppressed_existing"] == 1

    def test_cli_do_not_contact_sets_status(self, tmp_path):
        """CLI do-not-contact command sets status correctly."""
        db_path = _set_db(tmp_path)
        _insert_existing_lead(db_path, "target.example", "drafted")
        row = _get_lead_row(db_path, "target.example")
        lead_id = int(row["id"])

        runner = CliRunner()
        result = runner.invoke(cli_app, ["do-not-contact", str(lead_id)])
        assert result.exit_code == 0

        updated = _get_lead_row(db_path, "target.example")
        assert updated["status"] == "do_not_contact"
        # Historical content preserved
        assert updated["draft"] == "Old draft body"
        assert updated["subject"] == "Old subject"

    def test_cli_do_not_contact_nonexistent_lead(self, tmp_path):
        """CLI do-not-contact on nonexistent lead fails cleanly."""
        _set_db(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli_app, ["do-not-contact", "99999"])
        assert result.exit_code != 0

    def test_cli_allow_contact_reverses_dnc(self, tmp_path):
        """CLI allow-contact reverses do_not_contact to rejected (retryable)."""
        db_path = _set_db(tmp_path)
        _insert_existing_lead(db_path, "dnc.example", "do_not_contact")
        row = _get_lead_row(db_path, "dnc.example")
        lead_id = int(row["id"])

        runner = CliRunner()
        result = runner.invoke(cli_app, ["allow-contact", str(lead_id)])
        assert result.exit_code == 0

        updated = _get_lead_row(db_path, "dnc.example")
        assert updated["status"] == "rejected"
        # Historical content preserved
        assert updated["draft"] == "Old draft body"

    def test_cli_allow_contact_on_non_dnc_fails(self, tmp_path):
        """allow-contact on a non-do_not_contact lead fails cleanly."""
        db_path = _set_db(tmp_path)
        _insert_existing_lead(db_path, "target.example", "drafted")
        row = _get_lead_row(db_path, "target.example")
        lead_id = int(row["id"])

        runner = CliRunner()
        result = runner.invoke(cli_app, ["allow-contact", str(lead_id)])
        assert result.exit_code != 0

    def test_dnc_then_rediscovery_after_allow_contact(self, tmp_path, monkeypatch):
        """Full cycle: do_not_contact -> allow-contact -> rediscovered."""
        db_path = _set_db(tmp_path)
        _insert_existing_lead(db_path, "cycle.example", "do_not_contact")
        _setup_search(["cycle.example"], monkeypatch)

        # First: suppressed
        def fail_if_crawl(*a, **k):
            raise AssertionError("should not crawl do_not_contact")

        monkeypatch.setattr(pipeline_mod, "crawl_company", fail_if_crawl)
        result = run_pipeline(limit=1)
        assert result["suppressed_existing"] == 1
        assert result["attempted"] == 0

        # allow-contact
        row = _get_lead_row(db_path, "cycle.example")
        lead_id = int(row["id"])
        runner = CliRunner()
        res = runner.invoke(cli_app, ["allow-contact", str(lead_id)])
        assert res.exit_code == 0

        # Now retryable — should be crawled
        crawled: list[str] = []
        _setup_crawl_and_llm(monkeypatch, crawled)
        result = run_pipeline(limit=1)
        assert result["attempted"] == 1
        assert result["suppressed_existing"] == 0
        assert crawled == ["cycle.example"]


# ===========================================================================
# 9. No crawl/LLM/DB mutation for suppressed leads (R1-10)
# ===========================================================================


class TestNoSideEffectsForSuppressed:
    def test_suppressed_lead_no_crawl_no_llm_no_upsert(self, tmp_path, monkeypatch):
        """A suppressed lead must not invoke crawl_company, discover_contact,
        analyze_agency, draft_outreach, or upsert_lead."""
        db_path = _set_db(tmp_path)
        _insert_existing_lead(db_path, "suppressed.example", "sent")
        _setup_search(["suppressed.example"], monkeypatch)

        def fail_if_called(name):
            def _f(*a, **k):
                raise AssertionError(f"{name} should NOT be called for suppressed lead")
            return _f

        monkeypatch.setattr(pipeline_mod, "crawl_company", fail_if_called("crawl_company"))
        monkeypatch.setattr(pipeline_mod, "discover_contact", fail_if_called("discover_contact"))
        monkeypatch.setattr(pipeline_mod, "analyze_agency", fail_if_called("analyze_agency"))
        monkeypatch.setattr(pipeline_mod, "draft_outreach", fail_if_called("draft_outreach"))
        monkeypatch.setattr(pipeline_mod, "upsert_lead", fail_if_called("upsert_lead"))

        result = run_pipeline(limit=1)

        assert result["attempted"] == 0
        assert result["suppressed_existing"] == 1


# ===========================================================================
# 10. Suppression counters in summary (R1-9)
# ===========================================================================


class TestSuppressionCounters:
    def test_summary_has_suppression_keys(self, tmp_path, monkeypatch):
        db_path = _set_db(tmp_path)
        _insert_existing_lead(db_path, "sent.example", "sent")
        _insert_existing_lead(db_path, "drafted.example", "drafted")
        _insert_existing_lead(db_path, "dnc.example", "do_not_contact")
        _setup_search(["sent.example", "drafted.example", "dnc.example", "fresh.example"], monkeypatch)

        crawled: list[str] = []
        _setup_crawl_and_llm(monkeypatch, crawled)

        result = run_pipeline(limit=1)

        assert "suppressed_existing" in result
        assert "suppressed_by_status" in result
        assert "fresh_retryable_pool" in result
        assert result["suppressed_existing"] == 3
        assert result["suppressed_by_status"]["sent"] == 1
        assert result["suppressed_by_status"]["drafted"] == 1
        assert result["suppressed_by_status"]["do_not_contact"] == 1
        assert result["fresh_retryable_pool"] == 1


# ===========================================================================
# 11. Domain normalization consistency (R1-5)
# ===========================================================================


class TestDomainNormalization:
    def test_suppression_uses_canonical_domain(self, tmp_path, monkeypatch):
        """Suppression must match on canonical domain (without www.)."""
        db_path = _set_db(tmp_path)
        # Lead stored as example.com (canonical)
        _insert_existing_lead(db_path, "example.com", "sent")
        _setup_search(["example.com"], monkeypatch)

        def fail_if_crawl(*a, **k):
            raise AssertionError("should not crawl suppressed domain")

        monkeypatch.setattr(pipeline_mod, "crawl_company", fail_if_crawl)

        result = run_pipeline(limit=1)
        assert result["suppressed_existing"] == 1
        assert result["attempted"] == 0


# ===========================================================================
# 12. discover-only remains read-only (R1-8)
# ===========================================================================


class TestDiscoverOnlyUnaffected:
    def test_discover_only_does_not_suppress(self, tmp_path, monkeypatch):
        """discover_only must NOT apply DB suppression — it shows the raw
        ranked pool for search quality evaluation."""
        db_path = _set_db(tmp_path)
        _insert_existing_lead(db_path, "sent.example", "sent")
        _insert_existing_lead(db_path, "fresh.example", "discovered")
        _setup_search(["sent.example", "fresh.example"], monkeypatch)

        from app.pipeline import discover_only
        result = discover_only(limit=20)

        # Both domains should appear in the ranked pool (no suppression)
        ranked_domains = [r["domain"] for r in result["ranked"]]
        assert "sent.example" in ranked_domains
        assert "fresh.example" in ranked_domains
        # No suppression keys in discover-only output
        assert "suppressed_existing" not in result

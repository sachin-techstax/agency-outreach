"""Tests for domain normalization, blocked-domain matching, and the
pre-crawl candidate quality filter.

All tests are pure unit tests -- no network or LLM calls.
"""
from __future__ import annotations

from app.candidate_filter import (
    BLOCKED_DOMAINS,
    CandidateDecision,
    evaluate_candidate,
    is_blocked_domain,
    normalize_domain,
)
from app.search import SearchHit


# ---------------------------------------------------------------------------
# Domain normalization
# ---------------------------------------------------------------------------


class TestNormalizeDomain:
    def test_full_url(self):
        assert normalize_domain("https://www.example.ai/services") == "example.ai"

    def test_bare_host_with_www(self):
        assert normalize_domain("www.example.com") == "example.com"

    def test_bare_host_without_www(self):
        assert normalize_domain("example.com") == "example.com"

    def test_subdomain(self):
        assert normalize_domain("https://old.reddit.com/r/ai") == "old.reddit.com"

    def test_uppercase(self):
        assert normalize_domain("HTTPS://WWW.Example.AI/About") == "example.ai"

    def test_port_stripped(self):
        assert normalize_domain("https://example.com:8080/path") == "example.com"

    def test_empty(self):
        assert normalize_domain("") == ""

    def test_no_scheme_with_path(self):
        assert normalize_domain("example.ai/blog/post") == "example.ai"


# ---------------------------------------------------------------------------
# Blocked domain suffix matching
# ---------------------------------------------------------------------------


class TestIsBlockedDomain:
    def test_reddit_exact(self):
        assert is_blocked_domain("reddit.com") is True

    def test_reddit_www(self):
        assert is_blocked_domain("www.reddit.com") is True

    def test_reddit_subdomain(self):
        assert is_blocked_domain("old.reddit.com") is True

    def test_linkedin_exact(self):
        assert is_blocked_domain("linkedin.com") is True

    def test_linkedin_www(self):
        assert is_blocked_domain("www.linkedin.com") is True

    def test_linkedin_country_subdomain(self):
        assert is_blocked_domain("uk.linkedin.com") is True

    def test_realreddit_not_blocked(self):
        # Must NOT match via substring -- safe suffix matching only
        assert is_blocked_domain("realreddit.com") is False

    def test_notlinkedin_not_blocked(self):
        assert is_blocked_domain("notlinkedin.com") is False

    def test_empty_not_blocked(self):
        assert is_blocked_domain("") is False

    def test_github_blocked(self):
        assert is_blocked_domain("github.com") is True

    def test_github_pages_subdomain_blocked(self):
        assert is_blocked_domain("user.github.io") is True

    def test_indeed_blocked(self):
        assert is_blocked_domain("indeed.com") is True

    def test_glassdoor_blocked(self):
        assert is_blocked_domain("glassdoor.com") is True

    def test_wellfound_blocked(self):
        assert is_blocked_domain("wellfound.com") is True


# ---------------------------------------------------------------------------
# Candidate evaluation
# ---------------------------------------------------------------------------


class TestEvaluateCandidate:
    def _hit(self, url, title="", snippet=""):
        return SearchHit(title=title, url=url, snippet=snippet, query="q")

    def test_blocked_platform_rejected(self):
        decision = evaluate_candidate(self._hit(
            "https://www.reddit.com/r/MachineLearning/comments/abc",
            "Best AI tools 2024 - Reddit",
            "Discussion about AI tools",
        ))
        assert decision.accepted is False
        assert decision.reason == "blocked-domain"
        assert decision.domain == "reddit.com"

    def test_genuine_ai_agency_accepted(self):
        decision = evaluate_candidate(self._hit(
            "https://acme-ai.com/",
            "Acme AI - Generative AI Development & Automation",
            "We build LLM applications, AI agents and custom software.",
        ))
        assert decision.accepted is True
        assert decision.reason == "accepted"
        assert decision.domain == "acme-ai.com"

    def test_job_listing_rejected(self):
        decision = evaluate_candidate(self._hit(
            "https://some-job-board.com/jobs/ai-engineer",
            "Senior AI Engineer - Hiring Now | Some Job Board",
            "Apply now for this AI engineer position. Salary $150k.",
        ))
        assert decision.accepted is False
        assert decision.reason == "job-board"

    def test_forum_thread_rejected(self):
        decision = evaluate_candidate(self._hit(
            "https://some-forum.com/thread/123",
            "Discussion: Best AI automation tools?",
            "Join the community discussion about AI tools.",
        ))
        assert decision.accepted is False
        assert decision.reason == "forum"

    def test_editorial_listicle_rejected(self):
        decision = evaluate_candidate(self._hit(
            "https://tech-blog.com/top-10-ai-agencies",
            "Top 10 AI Agencies in 2024",
            "A list of the best AI agencies and companies.",
        ))
        assert decision.accepted is False
        assert decision.reason == "editorial-listicle"

    def test_agency_blog_accepted(self):
        decision = evaluate_candidate(self._hit(
            "https://realagency.com/blog/building-rag-systems",
            "Building RAG Systems for Enterprise Clients",
            "Our AI consultancy shares lessons from production RAG deployments.",
        ))
        assert decision.accepted is True
        assert decision.reason == "accepted"
        assert decision.domain == "realagency.com"

    def test_agency_services_page_accepted(self):
        decision = evaluate_candidate(self._hit(
            "https://agency.ai/services",
            "AI Development Services",
            "Custom AI development, LLM integration, and automation services.",
        ))
        assert decision.accepted is True
        assert decision.reason == "accepted"

    def test_non_http_url_rejected(self):
        decision = evaluate_candidate(self._hit(
            "mailto:contact@example.com",
            "Contact Example",
            "",
        ))
        assert decision.accepted is False
        assert decision.reason == "unsupported-url"

    def test_pdf_rejected(self):
        decision = evaluate_candidate(self._hit(
            "https://example.com/whitepaper.pdf",
            "AI Whitepaper",
            "Download our PDF whitepaper on AI.",
        ))
        assert decision.accepted is False
        assert decision.reason == "unsupported-url"

    def test_generic_domain_accepted_conservatively(self):
        """A non-blocked domain with no strong signals but a valid structure
        should be accepted conservatively (the crawl + score will filter)."""
        decision = evaluate_candidate(self._hit(
            "https://some-company.io/",
            "Some Company",
            "We do things.",
        ))
        assert decision.accepted is True
        assert decision.reason == "accepted"

    def test_job_title_with_agency_signal_still_accepted(self):
        """If the title mentions 'jobs' but snippet has strong agency signals,
        we should accept (avoid over-filtering real agencies with careers pages)."""
        decision = evaluate_candidate(self._hit(
            "https://real-agency.com/careers",
            "Jobs at Real AI Agency",
            "We are an AI development agency hiring AI engineers. "
            "Custom software and LLM development.",
        ))
        assert decision.accepted is True
        assert decision.reason == "accepted"

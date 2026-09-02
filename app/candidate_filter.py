"""Pre-crawl candidate quality filter for discovery results.

This module sits between Serper search results and the agency processing loop.
Its job is to reject obvious non-agency sources (social platforms, forums, job
boards, directories, editorial listicles) *before* they consume a crawl
attempt, while remaining conservative enough that real agencies ranking
through ``/blog/``, ``/services/`` or ``/case-studies/`` pages are still
accepted.

The filter uses only search-result metadata (domain, URL path, title, snippet)
-- it does not fetch any pages.  The authoritative lead score is still produced
by :mod:`app.scoring` after crawling.

Public API
----------
- :func:`normalize_domain` -- canonical domain from a URL or host string
- :func:`is_blocked_domain` -- safe suffix-match against the blocked set
- :func:`evaluate_candidate` -- full accept/reject decision for a ``SearchHit``
- :class:`CandidateDecision` -- result of ``evaluate_candidate``
- :data:`BLOCKED_DOMAINS` -- centralized, documented blocked-domain set
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Blocked domains
# ---------------------------------------------------------------------------
# Centralized set of canonical (non-www) domains that are never agency
# prospects.  Matching uses safe suffix equality (see ``is_blocked_domain``)
# so ``www.reddit.com``, ``old.reddit.com``, ``uk.linkedin.com`` all match
# their canonical entries without listing every subdomain.
#
# Categories:
#   - Social / community platforms
#   - Q&A / forum platforms
#   - Code hosting / developer communities
#   - Media / publishing platforms
#   - Agency directories / freelance marketplaces
#   - Job boards / employer-review sites
#   - Video platforms
BLOCKED_DOMAINS: frozenset[str] = frozenset({
    # Social / community
    "reddit.com",
    "quora.com",
    "linkedin.com",
    "facebook.com",
    "instagram.com",
    "x.com",
    "twitter.com",
    # Q&A / forums / developer communities
    "stackoverflow.com",
    "stackexchange.com",
    "github.com",
    "github.io",
    # Media / publishing
    "medium.com",
    "youtube.com",
    # Agency directories / freelance marketplaces
    "clutch.co",
    "upwork.com",
    "contra.com",
    # Job boards / employer-review
    "indeed.com",
    "glassdoor.com",
    "wellfound.com",
})

# ---------------------------------------------------------------------------
# Positive agency signals (checked against title + snippet)
# ---------------------------------------------------------------------------
# Used only as a lightweight sanity check to decide whether a search result is
# plausible enough to spend a crawl attempt on.  This is NOT the lead score.
#
# Split into *strong* signals (sufficient alone to accept, can override
# negative-pattern matches) and *weak* signals (topical AI terms that support
# a decision but cannot alone prove the entity is an agency).
#
# Strong signals indicate the entity IS an agency/company selling technical
# services.  Weak signals indicate the content is ABOUT AI/automation but the
# entity might be a blog, news site, forum, or job board.
_STRONG_SIGNALS: tuple[str, ...] = (
    "agency", "consultancy", "consulting",
    "development company", "software development",
    "ai development services", "ai consulting", "ai consultancy",
    "ai solutions", "custom software", "product studio",
    "we build", "we develop", "our services",
    "for clients", "client solutions",
    "implementation services", "engineering services",
)

_WEAK_SIGNALS: tuple[str, ...] = (
    "ai development", "ai engineering", "generative ai",
    "llm development", "automation", "ai agents",
    "rag", "machine learning", "ai", "artificial intelligence",
    "llm",
)

# URL path *segments* (not substrings) that indicate a legitimate agency
# content page.  The path is split on "/" and each segment is checked for
# exact membership.  This prevents false matches like "ai" inside "mail" or
# "details".  Path segments support acceptance only when combined with at
# least one weak topical signal; they are not sufficient alone.
_AGENCY_PATH_SEGMENTS: frozenset[str] = frozenset({
    "about", "service", "services", "solution", "solutions",
    "team", "contact", "work", "case-studies", "case_studies",
    "portfolio", "projects", "blog", "insights",
})

# Patterns that strongly indicate a non-agency editorial/listicle result.
# These are checked case-insensitively against the title.
#
# The regex is structured to catch common editorial discovery patterns
# without rejecting real agencies whose names might contain "Best" or "Top":
#   - "Top N ..." / "Best N ..." (numbered listicles)
#   - "Top/Best ... <plural-entity>" (e.g. "Top AI Consulting Firms")
#   - "N <adj> <plural-entity>" (e.g. "10 AI Agencies")
#   - "List of ..." / "Directory of ..."
#   - "Guide to ... <plural-entity>"
#   - "<plural-entity> to Watch"
#
# Plural entity nouns are used (not singular) so "Best AI Agency" (a company
# name) does NOT match, but "Best AI Agencies" (a listicle) does.
_PLURAL_ENTITIES = r"agencies|companies|firms|startups|consultancies|platforms"
_LISTICLE_TITLE_RE = re.compile(
    # "Top N ..." / "Best N ..." / "N Best/Top ..."
    r"\b(top\s+\d+|best\s+\d+|\d+\s+(best|top))\b"
    # "Top/Best ... <plural-entity>" within 40 chars
    rf"|\b(top|best)\b.{{0,40}}\b({_PLURAL_ENTITIES})\b"
    # "N <word> <plural-entity>" e.g. "10 AI Agencies"
    rf"|\b\d+\s+\w+\s+({_PLURAL_ENTITIES})\b"
    # "List of ..." / "Directory of ..."
    r"|\b(list\s+of|directory\s+of)\b"
    # "Guide to ... <plural-entity>"
    rf"|\bguide\s+to\b.{{0,40}}\b({_PLURAL_ENTITIES})\b"
    # "<plural-entity> to Watch"
    rf"|\b({_PLURAL_ENTITIES})\s+to\s+watch\b",
    re.I,
)

# Forum/community thread title patterns.
_FORUM_TITLE_RE = re.compile(
    r"\b(discussion|thread|forum|community|q&a|question|answer|"
    r"reddit|quora|stack\s*overflow|hacker\s*news)\b",
    re.I,
)

# Job listing title patterns.
_JOB_TITLE_RE = re.compile(
    r"\b(job|jobs|hiring|career|careers|vacanc|position|role|"
    r"apply|salary|engineer\s+wanted|we'?re\s+hiring)\b",
    re.I,
)

# File/document extensions that are never agency pages.
_FILE_EXT_RE = re.compile(r"\.(pdf|docx?|xlsx?|pptx?|zip|rar|tar|gz|csv|json|xml)(?:$|\?)", re.I)


# ---------------------------------------------------------------------------
# Domain helpers
# ---------------------------------------------------------------------------

def normalize_domain(url_or_host: str) -> str:
    """Return a canonical lowercase domain without ``www.`` prefix.

    Accepts either a full URL or a bare host string.  Returns an empty string
    for invalid/empty input.
    """
    if not url_or_host:
        return ""
    raw = url_or_host.strip().lower()
    if "://" in raw or raw.startswith("//"):
        host = urlparse(raw).netloc
    elif "/" in raw:
        host = urlparse("https://" + raw).netloc
    else:
        host = raw
    host = host.split(":")[0].strip()
    if host.startswith("www."):
        host = host[4:]
    return host


def is_blocked_domain(domain: str) -> bool:
    """Return ``True`` if *domain* matches any entry in :data:`BLOCKED_DOMAINS`.

    Uses safe suffix equality so ``old.reddit.com`` matches ``reddit.com`` but
    ``realreddit.com`` does **not**.
    """
    if not domain:
        return False
    for blocked in BLOCKED_DOMAINS:
        if domain == blocked or domain.endswith("." + blocked):
            return True
    return False


# ---------------------------------------------------------------------------
# Path segment helper
# ---------------------------------------------------------------------------

def _has_path_hint(path: str) -> bool:
    """Return True if any URL path segment matches a known agency page hint.

    The path is split on ``/`` and each non-empty segment is checked for exact
    membership in :data:`_AGENCY_PATH_SEGMENTS`.  This prevents false matches
    like ``"ai"`` inside ``"mail"`` or ``"details"`` that substring matching
    would produce.
    """
    segments = [s for s in path.lower().split("/") if s]
    return any(seg in _AGENCY_PATH_SEGMENTS for seg in segments)


# ---------------------------------------------------------------------------
# Candidate evaluation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CandidateDecision:
    """Result of evaluating a search hit before crawling."""

    accepted: bool
    reason: str
    domain: str


def evaluate_candidate(hit) -> CandidateDecision:
    """Decide whether *hit* is a plausible agency prospect worth crawling.

    Returns a :class:`CandidateDecision` with ``accepted`` True/False and a
    stable ``reason`` string.  Rejection reasons are one of:

    - ``blocked-domain``
    - ``unsupported-url``
    - ``job-board``
    - ``forum``
    - ``editorial-listicle``
    - ``insufficient-agency-signal``

    Accept reason is always ``accepted``.

    Acceptance rules (in priority order):

    1. Hard rejects: blocked domain, non-http(s) URL, file extension.
    2. Listicle titles are always rejected.
    3. Forum/job-board titles are rejected unless a strong signal is present.
    4. Strong agency identity signal in title/snippet → accept.
    5. Weak topical signal + agency URL path segment → accept.
    6. Otherwise → reject ``insufficient-agency-signal``.
    """
    domain = normalize_domain(hit.url)

    # --- hard rejects -------------------------------------------------------
    if not domain:
        return CandidateDecision(False, "unsupported-url", "")

    if is_blocked_domain(domain):
        return CandidateDecision(False, "blocked-domain", domain)

    parsed = urlparse(hit.url)
    scheme = (parsed.scheme or "").lower()

    # Non-http(s) URLs (e.g. mailto:, tel:, ftp:)
    if scheme and scheme not in ("http", "https"):
        return CandidateDecision(False, "unsupported-url", domain)

    # Obvious file/document results
    if _FILE_EXT_RE.search(parsed.path):
        return CandidateDecision(False, "unsupported-url", domain)

    combined = f"{hit.title} {hit.snippet}".strip().lower()

    has_strong = any(sig in combined for sig in _STRONG_SIGNALS)
    has_weak = any(sig in combined for sig in _WEAK_SIGNALS)

    # --- negative pattern checks -------------------------------------------
    # Listicles are always rejected (a "Top 10 AI Agencies" article is never
    # itself an agency, even if it mentions the word "agency").
    if _LISTICLE_TITLE_RE.search(hit.title):
        return CandidateDecision(False, "editorial-listicle", domain)

    # Job listings and forum threads are rejected UNLESS the result has strong
    # identity signals indicating the entity itself is an agency (e.g. a real
    # agency's careers page or a company blog post that happens to mention
    # "discussion").
    if not has_strong:
        if _FORUM_TITLE_RE.search(hit.title):
            return CandidateDecision(False, "forum", domain)
        if _JOB_TITLE_RE.search(hit.title):
            return CandidateDecision(False, "job-board", domain)

    # --- accept heuristics --------------------------------------------------
    # Strong agency identity signals in title/snippet are sufficient to accept.
    if has_strong:
        return CandidateDecision(True, "accepted", domain)

    # Weak (topical) signals plus a legitimate agency URL path segment are
    # sufficient to accept.  The path segment provides structural evidence
    # that the domain is a company site, while the topical signal confirms
    # the content is relevant.  Neither alone is sufficient.
    if has_weak and _has_path_hint(parsed.path):
        return CandidateDecision(True, "accepted", domain)

    # No sufficient evidence that this is an agency prospect.
    return CandidateDecision(False, "insufficient-agency-signal", domain)

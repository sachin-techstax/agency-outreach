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
# Split into *identity* signals (strongly indicate the entity IS an agency) and
# *topical* signals (indicate the content is about AI/automation but the entity
# might be a blog, forum, or job board).  Identity signals can override
# negative-pattern matches; topical signals cannot.
_IDENTITY_SIGNALS: tuple[str, ...] = (
    "agency", "consultancy", "consulting",
    "software development", "product studio",
    "custom software", "ai solutions",
)

_TOPICAL_SIGNALS: tuple[str, ...] = (
    "ai development", "ai engineering", "generative ai",
    "llm development", "automation", "ai agents",
    "rag", "machine learning", "ai", "artificial intelligence",
    "llm",
)

# URL path segments that indicate a legitimate agency content page rather than
# a non-agency listing.  If the domain is not blocked and the path contains one
# of these, we lean towards accepting even without strong title/snippet signals
# because the domain itself is the prospect.
_AGENCY_PATH_HINTS: tuple[str, ...] = (
    "about", "services", "service", "solution", "solutions",
    "ai", "team", "contact", "work", "case-studies", "case_studies",
    "blog", "insights", "portfolio", "projects",
)

# Patterns that strongly indicate a non-agency result when found in the
# title.  These are checked case-insensitively as whole-word-ish matches.
_FORUM_TITLE_RE = re.compile(
    r"\b(discussion|thread|forum|community|q&a|question|answer|"
    r"reddit|quora|stack\s*overflow|hacker\s*news)\b",
    re.I,
)
_JOB_TITLE_RE = re.compile(
    r"\b(job|jobs|hiring|career|careers|vacanc|position|role|"
    r"apply|salary|engineer\s+wanted|we'?re\s+hiring)\b",
    re.I,
)
_LISTICLE_TITLE_RE = re.compile(
    r"\b(top\s+\d+|best\s+\d+|\d+\s+(best|top|agencies|companies|firms|"
    r"startups|tools|platforms))\b|"
    r"\b(list\s+of|directory\s+of)\b",
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

    has_identity = any(sig in combined for sig in _IDENTITY_SIGNALS)
    has_topical = any(sig in combined for sig in _TOPICAL_SIGNALS)
    has_positive = has_identity or has_topical

    # --- negative pattern checks -------------------------------------------
    # Listicles are always rejected (a "Top 10 AI Agencies" article is never
    # itself an agency, even if it mentions the word "agency").
    if _LISTICLE_TITLE_RE.search(hit.title):
        return CandidateDecision(False, "editorial-listicle", domain)

    # Job listings and forum threads are rejected UNLESS the result has strong
    # identity signals indicating the entity itself is an agency (e.g. a real
    # agency's careers page or a company blog post that happens to mention
    # "discussion").
    if not has_identity:
        if _FORUM_TITLE_RE.search(hit.title):
            return CandidateDecision(False, "forum", domain)
        if _JOB_TITLE_RE.search(hit.title):
            return CandidateDecision(False, "job-board", domain)

    # --- accept heuristics --------------------------------------------------
    # If we have positive agency signals in title/snippet, accept.
    if has_positive:
        return CandidateDecision(True, "accepted", domain)

    # If the URL path looks like a legitimate agency content page, accept
    # conservatively (the domain is the prospect; the crawler will root it).
    path_lower = parsed.path.lower()
    if any(hint in path_lower for hint in _AGENCY_PATH_HINTS):
        return CandidateDecision(True, "accepted", domain)

    # Fallback: if the domain is not blocked and has a plausible TLD structure
    # (at least one dot), accept conservatively.  We prefer false-accepts over
    # false-rejects at this stage; the crawl + score will filter further.
    if "." in domain and len(domain) > 4:
        return CandidateDecision(True, "accepted", domain)

    return CandidateDecision(False, "insufficient-agency-signal", domain)

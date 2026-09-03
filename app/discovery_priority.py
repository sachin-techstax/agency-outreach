"""Search-hit discovery priority scoring.

This module answers a single, narrow question:

    "How promising is this SEARCH RESULT as an agency/consultancy prospect
    *before* we crawl it?"

It is intentionally cheap, deterministic, and based only on the search-result
metadata that Serper already returns (title + snippet + URL).  It does NOT
replace the post-crawl commercial-fit score in :mod:`app.commercial_fit`; it
only ranks the discovery pool so that the most promising candidates are
crawled first when ``--limit`` caps the number of attempts.

Design rules
------------
- **Commercial intent dominates topical AI terms.**  A result titled
  "Enterprise AI Platform | Start Free" must rank below
  "Acme AI Development Agency | Custom AI Solutions for Clients" even if the
  platform contains more AI keywords.  Generic AI/LLM/automation terms by
  themselves contribute almost nothing.
- **Positive signals** reward agency/consultancy/delivery identity and
  explicit client-delivery language.
- **Negative signals** penalize SaaS/product, enterprise-scale, editorial,
  and community/training patterns.  The hard-rejects in
  :mod:`app.candidate_filter` still run first; this scoring mainly helps
  rank ambiguous accepted candidates.
- **No company-size inference** unless an explicit size proxy
  (``boutique``, ``specialist``, ``studio``, ``small team``) is present.

Public API
----------
- :func:`score_discovery_priority` -- returns a :class:`DiscoveryPriority`
- :class:`DiscoveryPriority` -- frozen dataclass with ``score`` and ``reasons``
- :func:`rank_hits` -- deterministic ordering of accepted hits
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Positive commercial-delivery signals
# ---------------------------------------------------------------------------
# Each entry: (points, [phrases], reason-tag).  Phrases are matched
# case-insensitively as substrings against the combined title+snippet.
_POSITIVE_DELIVERY: tuple[tuple[int, tuple[str, ...], str], ...] = (
    (25, ("agency",), "agency"),
    (25, ("consultancy",), "consultancy"),
    (22, ("consulting firm",), "consultancy"),
    (22, ("development company",), "development-company"),
    (20, ("software development services",), "development-services"),
    (20, ("ai development services",), "ai-development-services"),
    (20, ("custom ai development",), "custom-development"),
    (18, ("custom software",), "custom-software"),
    (18, ("implementation services",), "implementation-services"),
    (18, ("professional services",), "professional-services"),
    (18, ("development partner",), "development-partner"),
    (18, ("technology partner",), "technology-partner"),
    (18, ("ai implementation",), "ai-implementation"),
    (18, ("ai consulting",), "ai-consulting"),
    (18, ("ai consultancy",), "ai-consultancy"),
    (15, ("we build",), "we-build"),
    (15, ("we develop",), "we-develop"),
    (15, ("for clients",), "client-delivery"),
    (15, ("client projects",), "client-delivery"),
    (15, ("case studies",), "case-studies"),
)

# Modest bonus for explicit small/mid size proxies.  We do NOT infer size
# when it is not stated.
_SIZE_BONUS: tuple[tuple[int, tuple[str, ...], str], ...] = (
    (6, ("boutique",), "boutique"),
    (6, ("specialist",), "specialist"),
    (6, ("studio",), "studio"),
    (4, ("small team",), "small-team"),
)

# ---------------------------------------------------------------------------
# Negative signals (penalties)
# ---------------------------------------------------------------------------
_NEGATIVE: tuple[tuple[int, tuple[str, ...], str], ...] = (
    # SaaS / products
    (20, ("platform",), "-platform"),
    (18, ("saas",), "-saas"),
    (18, ("start free",), "-self-service"),
    (18, ("free trial",), "-self-service"),
    (15, ("pricing plans",), "-pricing"),
    (15, ("developer platform",), "-platform"),
    (15, ("self-service",), "-self-service"),
    # Enterprise scale
    (22, ("fortune 500",), "-enterprise-scale"),
    (20, ("global advisory",), "-enterprise-scale"),
    (20, ("global consulting",), "-enterprise-scale"),
    (18, ("multinational",), "-enterprise-scale"),
    (18, ("public company",), "-enterprise-scale"),
    (20, ("nyse",), "-enterprise-scale"),
    (20, ("nasdaq",), "-enterprise-scale"),
    (18, ("thousands of employees",), "-enterprise-scale"),
    # Editorial
    (15, ("top agencies",), "-editorial"),
    (15, ("best companies",), "-editorial"),
    (12, ("guide to",), "-editorial"),
    (12, ("list of",), "-editorial"),
    (12, ("directory",), "-editorial"),
    # Community / training
    (15, ("community",), "-community"),
    (15, ("academy",), "-training"),
    (15, ("course",), "-training"),
    (15, ("bootcamp",), "-training"),
    (12, ("membership",), "-community"),
)

# Generic topical AI terms.  These contribute a tiny amount on their own so a
# result with NO commercial signal does not accidentally outrank a real
# agency.  They are only meaningful when combined with delivery signals.
_TOPICAL_BONUS: tuple[tuple[int, tuple[str, ...], str], ...] = (
    (2, ("ai", "artificial intelligence"), "ai-topic"),
    (2, ("llm", "large language model"), "llm-topic"),
    (2, ("rag", "retrieval augmented generation"), "rag-topic"),
    (2, ("machine learning",), "ml-topic"),
    (2, ("automation",), "automation-topic"),
    (2, ("generative ai",), "genai-topic"),
    (2, ("ai agents",), "ai-agents-topic"),
)

# URL path segments that hint at a real company services/about page (vs. a
# blog post or product docs).  Provides a small structural bonus.
_DELIVERY_PATH_SEGMENTS: frozenset[str] = frozenset({
    "services", "service", "solutions", "solution",
    "about", "team", "contact", "work", "case-studies", "case_studies",
    "portfolio", "projects",
})

# URL path segments that hint at non-promotional content (blog/docs).  Small
# penalty so a /services hit outranks a /blog hit for the same domain.
_CONTENT_PATH_SEGMENTS: frozenset[str] = frozenset({
    "blog", "insights", "news", "resources", "docs", "documentation",
    "help", "support", "guide", "guides",
})

_FILE_EXT_RE = re.compile(r"\.(pdf|docx?|xlsx?|pptx?|zip|rar|tar|gz|csv|json|xml)(?:$|\?)", re.I)


@dataclass(frozen=True)
class DiscoveryPriority:
    """Result of scoring a search hit for discovery priority.

    ``score`` is an integer (higher = more promising as a prospect).
    ``reasons`` is a list of short stable tags explaining the score, ordered
    by contribution.  This is NOT the commercial-fit score.
    """

    score: int
    reasons: list[str] = field(default_factory=list)


def _has_segment(path: str, segments: frozenset[str]) -> bool:
    parts = [s for s in path.lower().split("/") if s]
    return any(seg in segments for seg in parts)


def _match_signals(
    text: str,
    signals: tuple[tuple[int, tuple[str, ...], str], ...],
) -> list[tuple[int, str]]:
    """Return ``[(points, tag), ...]`` for signals whose phrases match *text*.

    For each signal group, only the first matching phrase contributes (so a
    single signal group is counted at most once even if multiple phrases
    match).
    """
    matched: list[tuple[int, str]] = []
    for points, phrases, tag in signals:
        for phrase in phrases:
            if phrase in text:
                matched.append((points, tag))
                break
    return matched


def score_discovery_priority(hit) -> DiscoveryPriority:
    """Score a search hit for discovery priority.

    Uses only ``hit.title``, ``hit.snippet`` and ``hit.url``.  Deterministic.
    """
    title = (getattr(hit, "title", "") or "").strip()
    snippet = (getattr(hit, "snippet", "") or "").strip()
    combined = f"{title} {snippet}".lower()

    parsed = urlparse(getattr(hit, "url", "") or "")
    path = parsed.path or ""

    # --- file/document results are never promising ---
    if _FILE_EXT_RE.search(path):
        return DiscoveryPriority(0, ["-file"])

    contributions: list[tuple[int, str]] = []

    # Positive delivery signals
    contributions.extend(_match_signals(combined, _POSITIVE_DELIVERY))

    # Size proxy bonus (only when explicitly stated)
    contributions.extend(_match_signals(combined, _SIZE_BONUS))

    # Topical AI terms: tiny bonus, only meaningful with delivery signals
    contributions.extend(_match_signals(combined, _TOPICAL_BONUS))

    # Negative signals (penalties) -- stored as positive magnitudes, applied
    # as deductions.
    neg_matched = _match_signals(combined, _NEGATIVE)
    contributions.extend((-pts, tag) for pts, tag in neg_matched)

    # URL structural hints
    if _has_segment(path, _DELIVERY_PATH_SEGMENTS):
        contributions.append((5, "delivery-path"))
    if _has_segment(path, _CONTENT_PATH_SEGMENTS):
        contributions.append((-3, "-content-path"))

    # Commercial intent must dominate topical AI terms.  If there are no
    # positive delivery signals AND no size proxy, cap the topical bonus so a
    # generic "AI Platform" cannot outrank a real agency.
    has_delivery = any(
        tag in {
            "agency", "consultancy", "development-company", "development-services",
            "ai-development-services", "custom-development", "custom-software",
            "implementation-services", "professional-services",
            "development-partner", "technology-partner", "ai-implementation",
            "ai-consulting", "ai-consultancy", "we-build", "we-develop",
            "client-delivery", "case-studies", "delivery-path",
        }
        for _, tag in contributions
    )
    if not has_delivery:
        # Strip topical bonuses entirely -- they should not help a result
        # with no commercial-delivery signal.
        contributions = [
            (pts, tag) for pts, tag in contributions
            if not tag.endswith("-topic")
        ]

    score = sum(pts for pts, _ in contributions)
    if score < 0:
        score = 0
    if score > 100:
        score = 100

    # Order reasons by absolute contribution descending, then by tag for
    # deterministic output.
    ordered = sorted(contributions, key=lambda c: (-abs(c[0]), c[1]))
    reasons = [tag for _, tag in ordered]
    return DiscoveryPriority(score, reasons)


def rank_hits(hits: list) -> list:
    """Return *hits* sorted by descending discovery priority.

    Tie-breaking is deterministic:
      1. discovery priority score (descending)
      2. query_rank (ascending) -- earlier query wins
      3. result_rank (ascending) -- earlier Serper position wins

    The returned list is a new list; the input is not mutated.
    """
    scored = [(score_discovery_priority(h), h) for h in hits]
    scored.sort(key=lambda pair: (-pair[0].score, pair[1].query_rank, pair[1].result_rank))
    return [h for _, h in scored]

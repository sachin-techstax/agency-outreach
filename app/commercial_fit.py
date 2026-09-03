"""Commercial fit qualification for crawled agency candidates.

This module answers a specific sales question:

    "How plausible is this company as a target for our overflow / white-label
    senior AI engineering pitch?"

It is NOT generic AI relevance.  A company can be deeply technical (e.g. an AI
SaaS platform) and still be a poor outreach target because they sell a product,
not client delivery services.

The model uses a two-stage deterministic score:

1. **technical_score** — does the company work on AI / software relevant to us?
2. **commercial_score** — does the company sell client delivery and plausibly
   need overflow engineering capacity?

The final score is a weighted combination where commercial fit dominates::

    final = 0.35 * technical + 0.65 * commercial

Both sub-scores are clamped to [0, 100] before combining.  The final score is
also clamped to [0, 100].

Public API
----------
- :func:`score_commercial_fit` — returns a :class:`CommercialFit` with score,
  category, and transparent reasons.
- :class:`CommercialFit` — dataclass result.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

CATEGORIES = (
    "agency",
    "consultancy",
    "product-company",
    "platform",
    "community",
    "training",
    "marketplace",
    "enterprise-consultancy",
    "unknown",
)


# ---------------------------------------------------------------------------
# Commercial delivery signals (strong — sufficient for commercial score)
# ---------------------------------------------------------------------------
# These indicate the company sells client delivery services, not just a product.
_COMMERCIAL_STRONG = [
    (20, ["agency", "consultancy", "consulting firm"], "client-services delivery identity"),
    (15, ["client services", "professional services", "implementation services",
          "engineering services", "delivery team", "team augmentation",
          "staff augmentation", "embedded engineers", "forward deployed",
          "done for you", "done-for-you"],
     "service delivery team"),
    (15, ["custom development", "custom software", "bespoke software",
          "software development services", "ai development services",
          "custom software development", "custom automation",
          "custom workflow automation", "ai agent development",
          "rag pipeline development", "saas mvp development",
          "custom built agents", "custom built", "we build automation",
          "we create hands-off", "build and deploy"],
     "custom development services"),
    (10, ["ai consulting", "ai consultancy", "ai implementation",
          "digital transformation consulting", "technology consulting",
          "ai strategy", "fractional caio", "ai workshops",
          "ai setup", "ai plan", "ai systems"],
     "AI/tech consulting"),
    (10, ["system integration", "systems integration", "implementation partner",
          "technology partner", "development partner",
          "we integrate", "integrate into your existing"],
     "integration/partnership delivery"),
    (10, ["for clients", "client projects", "our clients", "we help companies",
          "we build for clients", "case studies", "we embed",
          "scale your business", "without hiring more staff",
          "we design and implement", "we deployed"],
     "client delivery evidence"),
]

# ---------------------------------------------------------------------------
# Technical relevance signals (supporting — contribute to technical score)
# ---------------------------------------------------------------------------
_TECHNICAL_SIGNALS = [
    (15, ["ai development", "generative ai", "llm", "llm development",
          "large language model"], "AI/LLM development capability"),
    (10, ["automation", "ai agent", "ai agents", "agentic", "workflow automation"],
     "automation/agent capability"),
    (10, ["rag", "retrieval augmented", "vector database"], "RAG/retrieval capability"),
    (10, ["api", "backend", "fastapi", "python", "microservices"],
     "backend/API engineering"),
    (5, ["machine learning", "data engineering", "ai product development"],
     "ML/data engineering capability"),
]

# ---------------------------------------------------------------------------
# Negative signals — product/platform/community
# ---------------------------------------------------------------------------
# These use more specific phrases to avoid false positives.  A real agency
# that mentions "platform" or "training" in passing should not be penalized
# the same way as a SaaS company whose primary business model is a platform.
_PRODUCT_PENALTIES = [
    (-20, ["saas platform", "product-led", "developer platform", "self-service platform",
           "saas product", "our platform", "platform for building",
           "platform for creating", "platform for deploying"], "product/platform-first business model"),
    (-15, ["pricing plans", "start free", "free trial", "subscribe today",
           "subscribe to", "subscription plans", "sign up free", "start building free"],
     "self-service SaaS sign-up model"),
    (-10, ["product documentation", "api documentation", "developer docs",
           "sdk", "developer tools", "developer api"], "developer-facing product (not agency)"),
]

_COMMUNITY_PENALTIES = [
    (-20, ["join our community", "join the community", "membership community",
           "community platform", "community of creators", "join creators",
           "become a member", "membership includes"], "community/membership platform"),
    (-15, ["online course", "course platform", "cohort-based", "bootcamp program",
           "certification program", "training program for",
           "enroll in course", "course curriculum"], "education/training platform"),
]

_MARKETPLACE_PENALTIES = [
    (-15, ["marketplace for", "browse providers", "find experts",
           "find talent", "hire freelancers", "service marketplace",
           "provider directory"], "marketplace/directory model"),
]

# ---------------------------------------------------------------------------
# Enterprise-size penalty (applied to commercial score, not hard reject)
# ---------------------------------------------------------------------------
_ENTERPRISE_PENALTIES = [
    (-15, ["fortune 500", "global consulting", "multinational",
           "global advisory", "thousands of employees", "public company",
           "nyse", "nasdaq", "global offices"], "large enterprise scale — lower priority for overflow pitch"),
]

# ---------------------------------------------------------------------------
# Regex patterns for category detection
# ---------------------------------------------------------------------------
_PLATFORM_RE = re.compile(
    r"\b(saas platform|developer platform|product platform|self-service platform|"
    r"our platform|platform for building|platform for creating|platform for deploying)\b",
    re.I,
)
_COMMUNITY_RE = re.compile(
    r"\b(join our community|join the community|community platform|"
    r"community of creators|join creators|become a member|membership includes)\b",
    re.I,
)
_MARKETPLACE_RE = re.compile(
    r"\b(marketplace for|browse providers|find experts|find talent|"
    r"hire freelancers|service marketplace|provider directory)\b",
    re.I,
)
_ENTERPRISE_RE = re.compile(
    r"\b(fortune 500|global consulting|multinational|global advisory|"
    r"thousands of employees|public company|nyse|nasdaq)\b",
    re.I,
)
_AGENCY_RE = re.compile(
    r"\b(agency|consultancy|consulting firm|consulting practice)\b",
    re.I,
)
_TRAINING_RE = re.compile(
    r"\b(online course|course platform|cohort-based|bootcamp program|"
    r"certification program|training program for|enroll in course|"
    r"course curriculum|academy)\b",
    re.I,
)


@dataclass
class CommercialFit:
    """Result of commercial-fit qualification."""

    score: int
    category: str
    reasons: list[str] = field(default_factory=list)
    technical_score: int = 0
    commercial_score: int = 0


def _check_signals(text_lower: str, signals: list[tuple[int, list[str], str]]) -> list[tuple[int, str]]:
    """Return list of (points, reason) for matching signals."""
    hits = []
    for points, terms, reason in signals:
        if any(term in text_lower for term in terms):
            hits.append((points, reason))
    return hits


def _clamp(value: int, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, value))


def _determine_category(text_lower: str, commercial_hits: list, product_hits: list,
                        community_hits: list, marketplace_hits: list,
                        enterprise_hits: list) -> str:
    """Determine the commercial category from signal hits."""
    has_agency = bool(_AGENCY_RE.search(text_lower))
    has_platform = bool(_PLATFORM_RE.search(text_lower))
    has_community = bool(_COMMUNITY_RE.search(text_lower))
    has_marketplace = bool(_MARKETPLACE_RE.search(text_lower))
    has_enterprise = bool(_ENTERPRISE_RE.search(text_lower))
    has_training = bool(_TRAINING_RE.search(text_lower))

    # Community takes priority for categorization (membership/community platform)
    if has_community and not has_agency:
        return "community"
    # Training/education platform (courses, bootcamps, academies) — but only
    # when there is no stronger agency identity.  A real agency that offers
    # employee training or workshops should not be classified as training.
    if has_training and not has_agency and not commercial_hits:
        return "training"
    if has_marketplace and not has_agency:
        return "marketplace"

    # Enterprise consultancy: consulting + enterprise signals
    if has_agency and has_enterprise:
        return "enterprise-consultancy"

    # Agency/consultancy: strong commercial delivery signals
    if has_agency or (commercial_hits and not has_platform):
        return "agency" if "agency" in text_lower else "consultancy"

    # Product/platform
    if has_platform:
        return "platform" if has_platform else "product-company"

    if product_hits and not commercial_hits:
        return "product-company"

    return "unknown"


def score_commercial_fit(text: str) -> CommercialFit:
    """Score a crawled site for commercial fit as an outreach target.

    Parameters
    ----------
    text
        Combined crawled text from the company website.

    Returns
    -------
    CommercialFit
        With ``score`` (0-100), ``category``, ``reasons`` (transparent list of
        ``+N reason`` / ``-N reason`` strings), and sub-scores.
    """
    t = text.lower()

    # --- technical score ---
    tech_hits = _check_signals(t, _TECHNICAL_SIGNALS)
    technical_score = _clamp(sum(p for p, _ in tech_hits))

    # --- commercial score ---
    commercial_hits = _check_signals(t, _COMMERCIAL_STRONG)
    product_hits = _check_signals(t, _PRODUCT_PENALTIES)
    community_hits = _check_signals(t, _COMMUNITY_PENALTIES)
    marketplace_hits = _check_signals(t, _MARKETPLACE_PENALTIES)
    enterprise_hits = _check_signals(t, _ENTERPRISE_PENALTIES)

    commercial_positive = sum(p for p, _ in commercial_hits)
    commercial_penalty = (
        sum(p for p, _ in product_hits)
        + sum(p for p, _ in community_hits)
        + sum(p for p, _ in marketplace_hits)
        + sum(p for p, _ in enterprise_hits)
    )

    # If the site has strong agency/consultancy identity signals, dampen
    # penalties.  A real agency that mentions "platform" or "training" in
    # passing should not be penalized the same as a SaaS company.
    has_strong_agency = any(
        sig in t for sig in ("agency", "consultancy", "consulting firm",
                             "we build for clients", "delivery team",
                             "client services", "implementation services")
    )
    if has_strong_agency and commercial_positive > 0:
        # Dampen penalties by 50% when strong agency identity is present
        commercial_penalty = round(commercial_penalty * 0.5)

    commercial_raw = commercial_positive + commercial_penalty
    commercial_score = _clamp(commercial_raw)

    # --- final score: commercial dominates ---
    # Use the weighted average, but if commercial_score alone is high enough
    # to qualify, don't let a lower technical score pull it below threshold.
    weighted = round(0.30 * technical_score + 0.70 * commercial_score)
    final = _clamp(max(weighted, commercial_score))

    # --- category ---
    category = _determine_category(
        t, commercial_hits, product_hits, community_hits,
        marketplace_hits, enterprise_hits,
    )

    # --- transparent reasons ---
    reasons: list[str] = []
    for points, reason in tech_hits:
        reasons.append(f"{points:+d} {reason}")
    for points, reason in commercial_hits:
        reasons.append(f"{points:+d} {reason}")
    for points, reason in product_hits:
        reasons.append(f"{points:+d} {reason}")
    for points, reason in community_hits:
        reasons.append(f"{points:+d} {reason}")
    for points, reason in marketplace_hits:
        reasons.append(f"{points:+d} {reason}")
    for points, reason in enterprise_hits:
        reasons.append(f"{points:+d} {reason}")

    return CommercialFit(
        score=final,
        category=category,
        reasons=reasons,
        technical_score=technical_score,
        commercial_score=commercial_score,
    )

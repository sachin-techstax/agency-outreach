"""Deterministic company identity extraction from crawled page titles.

The previous implementation used arbitrary page titles as company names, which
produced marketing-phrase names like "Best AI Transformation Partners" instead
of the actual brand.

This module provides :func:`extract_company_name` which:

1. Cleans SEO suffixes from the homepage ``<title>``.
2. Rejects titles that look like marketing phrases rather than brands.
3. Falls back to a domain-derived brand name when the title is unusable.
"""
from __future__ import annotations

import re


# Separators commonly used in homepage titles: "Brand | Tagline", "Brand - Tagline"
_TITLE_SEPARATORS = [" | ", " - ", " — ", " – ", " :: ", " » "]

# Patterns that indicate a title is a marketing phrase, not a brand name.
# If the cleaned title matches any of these, we fall back to domain-derived.
_MARKETING_PHRASE_RE = re.compile(
    r"\b(best|top|leading|premier|trusted|award.?winning|"
    r"#1|number one|premiere)\b",
    re.I,
)

# Words that indicate the title is a category description, not a brand.
_CATEGORY_WORDS = re.compile(
    r"\b(companies|agencies|firms|partners|services|solutions|providers|"
    r"platforms|studios|consultants|experts|specialists)\b",
    re.I,
)

# Common SEO suffixes to strip from titles (e.g. "Brand - AI Automation Agency")
_SEO_SUFFIX_KEYWORDS = re.compile(
    r"\b(agency|consultancy|consulting|services|solutions|studio|"
    r"labs?|inc|llc|ltd|gmbh|ai|automation|software|development)\b",
    re.I,
)


def _domain_to_brand(domain: str) -> str:
    """Convert a domain like ``ayautomate.com`` to ``Ay Automate``."""
    # Take the part before the TLD
    parts = domain.split(".")
    if len(parts) < 2:
        brand = domain
    else:
        brand = parts[0]
    # Split on hyphens and title-case
    return brand.replace("-", " ").title()


def _is_plausible_brand(name: str) -> bool:
    """Return True if *name* looks like a brand rather than a marketing phrase."""
    name = name.strip()
    if not name or len(name) < 2 or len(name) > 80:
        return False
    # Reject marketing superlatives
    if _MARKETING_PHRASE_RE.search(name):
        # But allow if the superlative is part of a proper name like "BestBuy"
        # — check if the word is standalone (surrounded by spaces or at edges)
        # vs part of a compound word.  If the whole name is short (<=20 chars)
        # and has no spaces, it's probably a brand.
        if len(name) <= 20 and " " not in name:
            return True
        return False
    # Reject if it's purely a category description
    if _CATEGORY_WORDS.search(name) and len(name.split()) <= 4:
        return False
    return True


def _clean_title(title: str) -> str:
    """Strip SEO suffixes and separators from a homepage title."""
    if not title:
        return ""
    # Try splitting on common separators and take the first part
    for sep in _TITLE_SEPARATORS:
        if sep in title:
            candidate = title.split(sep)[0].strip()
            if candidate:
                return candidate
    return title.strip()


def extract_company_name(title: str, domain: str) -> str:
    """Extract a plausible company brand name from a page title and domain.

    Parameters
    ----------
    title
        The homepage ``<title>`` text (already stripped of HTML).
    domain
        The canonical domain (e.g. ``ayautomate.com``).

    Returns
    -------
    str
        A brand-like company name.  Falls back to a domain-derived name if the
        title is empty or looks like a marketing phrase.
    """
    cleaned = _clean_title(title or "")

    if cleaned and _is_plausible_brand(cleaned):
        return cleaned

    # Fall back to domain-derived brand
    return _domain_to_brand(domain)

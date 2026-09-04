from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from .logging_config import get_logger

logger = get_logger("contacts")

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)

# Local parts that are never valid outreach targets.
REJECTED_LOCAL_PARTS = frozenset({
    "media", "press", "pr", "publicrelations", "newsroom", "news",
    "privacy", "legal", "compliance", "security", "abuse",
    "careers", "career", "jobs", "recruiting", "recruitment", "hr",
    "support", "help", "billing", "accounts", "finance",
    "noreply", "no-reply", "donotreply", "do-not-reply",
    "notifications", "webmaster", "postmaster", "mailer-daemon",
    "unsubscribe", "admin", "administrator", "root",
})

# Tier 1: named individual / role-like personal email (highest priority).
# These are inferred from the local part only — we do not scrape LinkedIn or
# guess person names.
TIER1_LOCAL_PARTS = frozenset({
    "founder", "ceo", "cto", "cofounder", "co-founder",
    "partnerships", "partners", "business", "businessdevelopment",
    "bizdev", "growth", "coo", "cmo", "vp",
})

# Tier 2: generic but acceptable business contacts.
TIER2_LOCAL_PARTS = frozenset({
    "hello", "contact", "info", "sales", "team", "office",
})

# Roles we can detect from text (do not default to "Founder / CTO").
DETECTABLE_ROLES = [
    "founder", "co-founder", "ceo", "cto", "coo", "cmo",
    "head of engineering", "head of delivery", "technical director",
    "head of product", "vp of engineering", "director of engineering",
    "partnerships", "head of partnerships",
]


def _email_quality(local_part: str) -> str:
    """Return quality tier for a local part: high, medium, low, none."""
    if local_part in REJECTED_LOCAL_PARTS:
        return "none"
    if local_part in TIER1_LOCAL_PARTS:
        return "high"
    if local_part in TIER2_LOCAL_PARTS:
        return "medium"
    # Unknown local parts — could be a named person, treat as medium
    return "medium"


def _is_rejected(local_part: str) -> bool:
    return local_part in REJECTED_LOCAL_PARTS


def _normalize_mailto(raw: str) -> str:
    """Normalize a raw ``mailto:`` href to a bare lowercase email.

    Strips the ``mailto:`` prefix and any query parameters:
    ``mailto:hello@example.com?subject=Project`` -> ``hello@example.com``.
    """
    value = raw.strip()
    if value.lower().startswith("mailto:"):
        value = value[7:]
    # Strip any query/fragment that followed the address.
    parsed = urlparse(value)
    return parsed.path.lower().strip()


def _is_company_domain(email: str, domain: str) -> bool:
    return email.endswith("@" + domain)


def discover_contact(
    text: str,
    pages: list[tuple[str, str]],
    domain: str,
    mailtos: list[tuple[str, str]] | None = None,
) -> dict:
    """Discover the best public contact email from crawled site text + mailtos.

    Returns a dict with:
    - ``contact_email``: best email or ""
    - ``contact_source``: where the email was found (page URL, or "")
    - ``contact_name``: "" (we do not infer person names)
    - ``contact_role``: detected role from text, or "" if none found
    - ``contact_quality``: "high", "medium", "low", or "none"

    Emails are collected from visible text on the homepage and every crawled
    research page, plus ``mailto:`` hrefs collected by the scraper.  Only
    emails on the company's own domain are accepted; third-party addresses
    are ignored.  ``mailto:`` values are normalized (prefix and query
    parameters stripped) and deduplicated against text-derived emails so a
    single address found in both places is only counted once.
    """
    # Collect all emails from the company's own domain.
    # Each entry: (email, source_url).  Source is the page URL where the
    # email was found, so provenance can be persisted accurately.
    emails: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _add(email: str, source: str) -> None:
        e = email.lower()
        if not _is_company_domain(e, domain):
            return
        if e in seen:
            return
        seen.add(e)
        emails.append((e, source))

    # Visible text on the homepage and crawled pages.
    for source, chunk in [("website", text)] + list(pages):
        for email in EMAIL_RE.findall(chunk):
            _add(email, source)

    # mailto: hrefs collected by the scraper (homepage + crawled pages).
    if mailtos:
        for raw, source in mailtos:
            normalized = _normalize_mailto(raw)
            if normalized and "@" in normalized:
                _add(normalized, source)

    # Filter out rejected local parts.
    rejected_count = 0
    filtered: list[tuple[str, str, str]] = []  # (email, source, quality)
    for email, source in emails:
        local = email.split("@")[0]
        if _is_rejected(local):
            rejected_count += 1
            logger.debug("Rejected email %s reason=non-outreach-role", email)
            continue
        quality = _email_quality(local)
        filtered.append((email, source, quality))

    # Sort by quality priority: high > medium > low, with stable ordering so
    # the first-seen email wins within a tier (homepage text before pages).
    quality_order = {"high": 0, "medium": 1, "low": 2, "none": 3}
    filtered.sort(key=lambda x: quality_order.get(x[2], 99))

    preferred = None
    preferred_quality = "none"
    if filtered:
        preferred = (filtered[0][0], filtered[0][1])
        preferred_quality = filtered[0][2]

    # Detect role from text (do not default to "Founder / CTO").
    lowered = text.lower()
    role = ""
    for r in DETECTABLE_ROLES:
        if r in lowered:
            role = r.title()
            break

    if preferred:
        logger.info(
            "Selected public contact %s quality=%s source=%s",
            preferred[0], preferred_quality, preferred[1],
        )
    else:
        if emails and rejected_count > 0:
            logger.info(
                "No usable outreach email for %s (%d rejected non-outreach addresses)",
                domain, rejected_count,
            )
        else:
            logger.info("No public company-domain email found for %s", domain)

    return {
        "contact_email": preferred[0] if preferred else "",
        "contact_source": preferred[1] if preferred else "",
        "contact_name": "",
        "contact_role": role,
        "contact_quality": preferred_quality if preferred else "none",
    }

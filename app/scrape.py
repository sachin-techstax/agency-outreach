from __future__ import annotations

import re
import time
from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode

import httpx
from bs4 import BeautifulSoup

from .logging_config import get_logger

logger = get_logger("scrape")

UA = "Mozilla/5.0 (compatible; AgencyOutreachResearchBot/1.0)"

# Bounded number of same-domain research pages to fetch in addition to the
# homepage.  This is intentionally small to keep crawl cost predictable; the
# deterministic path scorer below decides WHICH pages earn those slots.
MAX_EXTRA_PAGES = 6

# Path keywords that mark a URL as worth researching, grouped by priority.
# Higher groups are ranked first.  Contact variants are intentionally broad
# so common contact pages are not displaced by service/work pages.
_PATH_PRIORITY_GROUPS: tuple[tuple[tuple[str, ...], int], ...] = (
    (
        ("contact-us", "contactus", "contact", "get-in-touch", "getintouch",
         "talk-to-us", "talktous", "connect", "reach-us", "reachus"),
        100,
    ),
    (
        ("about", "team", "who-we-are", "company"),
        50,
    ),
    (
        ("services", "service", "solutions", "solution", "capabilities",
         "what-we-do", "offering", "offerings"),
        30,
    ),
    (
        ("work", "case-stud", "casestud", "portfolio", "projects", "project"),
        20,
    ),
    (
        ("ai", "agent", "llm", "automation", "machine-learning", "ml"),
        15,
    ),
)

# Obvious irrelevant path fragments we never want to crawl.
_IGNORE_PATH_FRAGMENTS = (
    "/blog", "/news", "/post", "/article", "/tag/", "/category/",
    "/author/", "/wp-", "/cdn-cgi", "/legal", "/privacy", "/terms",
    "/cookie", "/jobs", "/careers", "/login", "/signup", "/cart",
    "/shop", "/product", "/feed", "/rss", "/comment",
)


def root_url(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme or 'https'}://{p.netloc}"


def domain_of(url: str) -> str:
    host = urlparse(url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


# Tracking-only query parameters that should be stripped for deduplication
# but do not change page content.  These are well-known analytics/marketing
# tracking params; meaningful params like ``form=agency`` are preserved.
_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "msclkid", "mc_eid", "mc_cid",
})


def _strip_tracking_params(query: str) -> str:
    """Remove tracking-only parameters from a query string, preserving
    meaningful parameters.  Returns the cleaned query string (may be empty)."""
    if not query:
        return ""
    pairs = parse_qsl(query, keep_blank_values=True)
    kept = [(k, v) for k, v in pairs if k.lower() not in _TRACKING_PARAMS]
    return urlencode(kept)


def _normalize_url(url: str) -> str:
    """Return a canonical dedupe key for a URL.

    Strips fragment, trailing slash, and tracking-only query parameters
    (utm_*, gclid, fbclid, etc.) so tracking variants of the same content
    page collapse to one dedupe key.  Meaningful query parameters (e.g.
    ``form=agency``) are PRESERVED in the key so semantically different
    pages are not incorrectly merged.

    This is a DEDUPE KEY only — it must NOT be used as the actual fetch URL.
    Use :func:`_canonical_fetch_url` for the URL to actually request.
    """
    p = urlparse(url)
    path = p.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    cleaned_query = _strip_tracking_params(p.query)
    return urlunparse((p.scheme or "https", p.netloc, path, "", cleaned_query, ""))


def _canonical_fetch_url(url: str) -> str:
    """Return the actual URL to fetch for a given link.

    Strips only the fragment and trailing slash (which are safe to remove for
    fetching) but PRESERVES the full query string, because query parameters
    can change page semantics (e.g. ``/contact?form=agency`` vs ``/contact``).
    """
    p = urlparse(url)
    path = p.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunparse((p.scheme or "https", p.netloc, path, "", p.query, ""))


def score_path(path: str) -> int:
    """Deterministic research-page priority score for a URL path.

    Higher is better.  Contact variants outrank service/work/about pages so a
    normal ``/contact`` is not displaced by four arbitrary service pages.
    Returns 0 for paths that match no research keyword (so they are dropped
    from the candidate set entirely).
    """
    p = path.lower()
    if any(frag in p for frag in _IGNORE_PATH_FRAGMENTS):
        return -1
    score = 0
    for keywords, weight in _PATH_PRIORITY_GROUPS:
        for kw in keywords:
            if kw in p:
                score += weight
                break  # one hit per group is enough
    return score


def clean_text(html: str, max_chars: int = 16000) -> tuple[str, str, list[str], list[str]]:
    """Return (title, visible_text, hrefs, mailtos) from raw HTML.

    ``mailtos`` are raw ``mailto:`` href values found on the page (the
    ``mailto:`` prefix and any query parameters are stripped later by the
    contact discovery layer, which also enforces the same-domain rule).
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))[:max_chars]
    hrefs: list[str] = []
    mailtos: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href:
            continue
        if href.lower().startswith("mailto:"):
            mailtos.append(href)
            continue
        hrefs.append(href)
    return title, text, [x for x in hrefs if x], mailtos


def fetch_page(url: str) -> tuple[str, str, list[str], list[str]]:
    """Fetch a URL and return (title, text, hrefs, mailtos)."""
    logger.debug("Fetching page: %s", url)
    try:
        with httpx.Client(headers={"User-Agent": UA}, follow_redirects=True, timeout=15) as client:
            r = client.get(url)
    except httpx.TimeoutException:
        logger.warning("Timeout fetching %s after 15s", url)
        raise
    except httpx.HTTPError as exc:
        logger.warning("Fetch failed for %s: %s", url, exc)
        raise
    logger.debug("HTTP %s for %s", r.status_code, url)
    if r.status_code in (403, 429):
        logger.warning("HTTP %s fetching %s", r.status_code, url)
    r.raise_for_status()
    if "text/html" not in r.headers.get("content-type", ""):
        logger.debug("Skipping non-HTML content at %s", url)
        return "", "", [], []
    return clean_text(r.text)


def _select_research_pages(root: str, domain: str, hrefs: list[str]) -> list[str]:
    """Pick the bounded, ranked set of same-domain research pages to crawl.

    Deterministic: score every same-domain link by path keywords, drop
    irrelevant ones, dedupe by normalized URL, then take the top
    ``MAX_EXTRA_PAGES`` by score (descending) with stable URL tie-breaking.

    Returns the CANONICAL FETCH URLs (query strings preserved) so the actual
    page requested by :func:`fetch_page` is the original link, not a
    query-stripped variant that could change semantics or 404.  Dedup uses
    :func:`_normalize_url` (tracking-stripped) as the key so tracking
    variants do not consume multiple slots.

    When multiple links share the same dedupe key, a query-free URL is
    preferred if one appeared among the links (e.g. ``/contact`` is
    preferred over ``/contact?utm_source=nav``).  Otherwise the first-seen
    variant's URL is used.
    """
    # Group candidates by dedupe key, tracking the best fetch URL for each.
    # Key: dedupe_key -> (score, fetch_url, has_query)
    groups: dict[str, tuple[int, str, bool]] = {}
    seen_keys: set[str] = set()
    for href in hrefs:
        absolute = urljoin(root, href)
        if domain_of(absolute) != domain:
            continue
        norm = _normalize_url(absolute)
        if norm in seen_keys:
            # Already have a candidate for this key; prefer query-free URL.
            existing = groups.get(norm)
            if existing is not None:
                _, existing_url, existing_has_q = existing
                this_url = _canonical_fetch_url(absolute)
                this_has_q = bool(urlparse(absolute).query)
                # Prefer the URL without a query string if the current one
                # has a query and this one doesn't.
                if existing_has_q and not this_has_q:
                    groups[norm] = (existing[0], this_url, False)
            continue
        seen_keys.add(norm)
        path = urlparse(absolute).path.lower()
        score = score_path(path)
        if score <= 0:
            continue
        fetch_url = _canonical_fetch_url(absolute)
        has_q = bool(urlparse(absolute).query)
        groups[norm] = (score, fetch_url, has_q)

    scored = [(score, url) for score, url, _ in groups.values()]
    # Sort by score desc, then URL asc for deterministic ordering.
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [url for _, url in scored[:MAX_EXTRA_PAGES]]


def crawl_company(url: str) -> dict:
    """Crawl a company site: homepage + a bounded, ranked set of research pages.

    Returns a dict with:
    - ``root``: site root URL
    - ``domain``: canonical domain
    - ``title``: homepage title
    - ``home_text``: visible text of the homepage only (used for accurate
      contact provenance — the homepage URL is the source, not a synthetic
      ``website`` label)
    - ``text``: combined visible text (homepage + crawled pages) used for
      commercial-fit scoring and LLM analysis
    - ``pages``: list of ``(url, text)`` for each crawled research page
    - ``mailtos``: list of ``(raw_mailto, source_url)`` collected from the
      homepage and every crawled page (raw ``mailto:`` values, normalized
      later by the contact discovery layer)
    """
    root = root_url(url)
    domain = domain_of(root)
    logger.info("Fetching homepage: %s", root)
    crawl_start = time.perf_counter()
    title, home, links, home_mailtos = fetch_page(root)
    if len(home) < 200:
        logger.warning("Homepage content too small for %s (%d chars)", domain, len(home))

    pages: list[tuple[str, str]] = []
    mailtos: list[tuple[str, str]] = []
    for raw in home_mailtos:
        mailtos.append((raw, root))

    targets = _select_research_pages(root, domain, links)
    logger.debug("Selected %d research pages for %s: %s", len(targets), domain, targets)
    for u in targets:
        try:
            _, text, _, page_mailtos = fetch_page(u)
        except httpx.HTTPError as exc:
            logger.warning("Skipping page %s: %s", u, exc)
            continue
        except Exception as exc:
            logger.warning("Skipping page %s: %s", u, exc)
            continue
        if text:
            pages.append((u, text[:8000]))
        else:
            logger.debug("Empty content for %s", u)
        for raw in page_mailtos:
            mailtos.append((raw, u))

    combined = home + "\n" + "\n".join(t for _, t in pages)
    elapsed = time.perf_counter() - crawl_start
    logger.info("Crawled %d pages, %d characters for %s", len(pages) + 1, len(combined), domain)
    logger.info("Crawled %s in %.1fs", domain, elapsed)
    return {
        "root": root,
        "domain": domain,
        "title": title,
        "home_text": home,
        "text": combined[:30000],
        "pages": pages,
        "mailtos": mailtos,
    }

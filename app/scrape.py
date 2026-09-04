from __future__ import annotations

import re
import time
from urllib.parse import unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .logging_config import get_logger

logger = get_logger("scrape")

UA = "Mozilla/5.0 (compatible; AgencyOutreachResearchBot/1.0)"


def root_url(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme or 'https'}://{p.netloc}"


def domain_of(url: str) -> str:
    host = urlparse(url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def clean_text(html: str, max_chars: int = 16000) -> tuple[str, str, list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    links = [a.get("href") for a in soup.find_all("a", href=True)]
    mailto_emails: list[str] = []
    for href in links:
        if not href or not href.lower().startswith("mailto:"):
            continue
        raw = unquote(href[7:].split("?", 1)[0]).strip()
        if raw:
            mailto_emails.append(raw)

    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    visible = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    # Preserve public mailto addresses even when the anchor text is merely
    # "Email us" or an icon. Contact discovery operates on extracted text,
    # so appending these addresses keeps the provenance while avoiding an
    # HTML-specific second parsing path downstream.
    mailto_text = " ".join(dict.fromkeys(mailto_emails))
    text = f"{visible} {mailto_text}".strip()[:max_chars]
    return title, text, [x for x in links if x]


def fetch_page(url: str) -> tuple[str, str, list[str]]:
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
        return "", "", []
    return clean_text(r.text)


def crawl_company(url: str) -> dict:
    root = root_url(url)
    domain = domain_of(root)
    logger.info("Fetching homepage: %s", root)
    crawl_start = time.perf_counter()
    title, home, links = fetch_page(root)
    if len(home) < 200:
        logger.warning("Homepage content too small for %s (%d chars)", domain, len(home))
    same_domain: list[tuple[int, str]] = []
    for href in links:
        absolute = urljoin(root, href)
        if domain_of(absolute) != domain:
            continue
        path = urlparse(absolute).path.lower()
        if not any(k in path for k in ["about", "service", "solution", "ai", "team", "contact", "work", "case"]):
            continue

        # Contact pages are a first-class crawl target because a valid public
        # business email is often available only there. About/team pages come
        # next, followed by capability and proof pages.
        if "contact" in path:
            priority = 0
        elif "about" in path or "team" in path:
            priority = 1
        elif any(k in path for k in ["service", "solution", "ai"]):
            priority = 2
        else:
            priority = 3
        same_domain.append((priority, absolute.split("#")[0]))

    same_domain.sort(key=lambda item: (item[0], item[1]))
    pages = []
    seen = set()
    for _, u in same_domain:
        if u in seen or len(pages) >= 4:
            continue
        seen.add(u)
        try:
            _, text, _ = fetch_page(u)
            if text:
                pages.append((u, text[:8000]))
            else:
                logger.debug("Empty content for %s", u)
        except httpx.HTTPError as exc:
            logger.warning("Skipping page %s: %s", u, exc)
            continue
        except Exception as exc:
            logger.warning("Skipping page %s: %s", u, exc)
            continue
    combined = home + "\n" + "\n".join(t for _, t in pages)
    elapsed = time.perf_counter() - crawl_start
    logger.info("Crawled %d pages, %d characters for %s", len(pages) + 1, len(combined), domain)
    logger.info("Crawled %s in %.1fs", domain, elapsed)
    return {"root": root, "domain": domain, "title": title, "text": combined[:30000], "pages": pages}

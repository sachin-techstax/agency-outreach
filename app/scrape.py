from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (compatible; AgencyOutreachResearchBot/1.0)"


def root_url(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme or 'https'}://{p.netloc}"


def domain_of(url: str) -> str:
    host = urlparse(url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def clean_text(html: str, max_chars: int = 16000) -> tuple[str, str, list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))[:max_chars]
    links = [a.get("href") for a in soup.find_all("a", href=True)]
    return title, text, [x for x in links if x]


def fetch_page(url: str) -> tuple[str, str, list[str]]:
    with httpx.Client(headers={"User-Agent": UA}, follow_redirects=True, timeout=15) as client:
        r = client.get(url)
        r.raise_for_status()
        if "text/html" not in r.headers.get("content-type", ""):
            return "", "", []
        return clean_text(r.text)


def crawl_company(url: str) -> dict:
    root = root_url(url)
    title, home, links = fetch_page(root)
    same_domain = []
    for href in links:
        absolute = urljoin(root, href)
        if domain_of(absolute) != domain_of(root):
            continue
        path = urlparse(absolute).path.lower()
        if any(k in path for k in ["about", "service", "solution", "ai", "team", "contact", "work", "case"]):
            same_domain.append(absolute.split("#")[0])
    pages = []
    seen = set()
    for u in same_domain:
        if u in seen or len(pages) >= 4:
            continue
        seen.add(u)
        try:
            _, text, _ = fetch_page(u)
            if text:
                pages.append((u, text[:8000]))
        except Exception:
            continue
    combined = home + "\n" + "\n".join(t for _, t in pages)
    return {"root": root, "domain": domain_of(root), "title": title, "text": combined[:30000], "pages": pages}

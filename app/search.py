from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from .config import settings
from .logging_config import get_logger

logger = get_logger("search")

DEFAULT_QUERIES = [
    '"AI automation" agency',
    '"generative AI" consultancy',
    '"LLM development" agency',
    '"AI product studio"',
    '"RAG development" company',
    '"AI software consultancy" agents automation',
    '"workflow automation" agency AI',
]

BLOCKED_HOSTS = {
    "linkedin.com", "www.linkedin.com", "facebook.com", "www.facebook.com",
    "x.com", "twitter.com", "instagram.com", "youtube.com", "www.youtube.com",
    "clutch.co", "upwork.com", "contra.com", "medium.com",
}


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str
    query: str


def search_serper(query: str, num: int = 10) -> list[SearchHit]:
    if not settings.serper_api_key:
        raise RuntimeError("SERPER_API_KEY is missing. Add it to .env.")
    logger.info("Query: %s", query)
    start = time.perf_counter()
    try:
        r = httpx.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": settings.serper_api_key, "Content-Type": "application/json"},
            json={"q": query, "num": num},
            timeout=30,
        )
    except httpx.TimeoutException as exc:
        logger.warning("Serper request timed out after 30s for query %r: %s", query, exc)
        raise
    except httpx.HTTPError as exc:
        logger.error("Serper network error for query %r: %s", query, exc)
        raise

    if r.status_code in (401, 403):
        logger.error("Serper returned HTTP %s (auth/permission denied) for query %r", r.status_code, query)
        r.raise_for_status()
    if r.status_code == 429:
        logger.warning("Serper rate limited (HTTP 429) for query %r", query)
        r.raise_for_status()
    if r.status_code >= 500:
        logger.error("Serper server error HTTP %s for query %r", r.status_code, query)
        r.raise_for_status()
    r.raise_for_status()

    data = r.json()
    hits = [
        SearchHit(
            title=i.get("title", ""),
            url=i.get("link", ""),
            snippet=i.get("snippet", ""),
            query=query,
        )
        for i in data.get("organic", [])
        if i.get("link")
    ]
    elapsed = time.perf_counter() - start
    logger.info("%d results returned in %.2fs", len(hits), elapsed)
    return hits

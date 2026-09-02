from __future__ import annotations

from dataclasses import dataclass

import httpx

from .config import settings

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
    r = httpx.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": settings.serper_api_key, "Content-Type": "application/json"},
        json={"q": query, "num": num},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    return [
        SearchHit(
            title=i.get("title", ""),
            url=i.get("link", ""),
            snippet=i.get("snippet", ""),
            query=query,
        )
        for i in data.get("organic", [])
        if i.get("link")
    ]

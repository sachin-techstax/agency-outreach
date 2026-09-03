from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from .candidate_filter import BLOCKED_DOMAINS
from .config import settings
from .logging_config import get_logger

logger = get_logger("search")

# ---------------------------------------------------------------------------
# Query specifications
# ---------------------------------------------------------------------------
# Discovery queries are structured around BUYER INTENT: we are looking for
# companies that SELL technical delivery to clients (agencies, consultancies,
# studios, custom-development partners), not SaaS platforms, communities, or
# generic enterprise vendors.
#
# Each :class:`QuerySpec` carries:
#   - ``query``    : the base Google query phrase (without exclusions)
#   - ``category`` : a short stable slug used for observability/routing
#   - ``priority`` : a discovery preference (higher = run earlier).  This is
#                    NOT the commercial-fit score; it only influences the order
#                    in which queries are issued and is otherwise informational.
#
# ``build_query`` appends shared negative-site exclusions so obvious
# non-target sources (social platforms, forums, directories, job boards) do
# not consume valuable Serper result slots.  The post-result
# :mod:`app.candidate_filter` remains in place as defense in depth.

VALID_CATEGORIES = frozenset({
    "ai-agents",
    "automation",
    "generative-ai",
    "llm",
    "rag",
    "ai-consulting",
    "custom-development",
    "product-development",
})

# Shared Google ``-site:`` exclusions for sources that are never agency
# prospects but routinely dominate generic AI queries.  Kept short on purpose
# so the query string stays well within Google's length budget.
NEGATIVE_SITE_EXCLUSIONS: tuple[str, ...] = (
    "-site:reddit.com",
    "-site:quora.com",
    "-site:linkedin.com",
    "-site:youtube.com",
    "-site:medium.com",
    "-site:clutch.co",
    "-site:upwork.com",
    "-site:indeed.com",
    "-site:glassdoor.com",
)


@dataclass(frozen=True)
class QuerySpec:
    """A single structured discovery query.

    ``priority`` is a discovery preference (higher = issued earlier) and is
    intentionally distinct from the commercial-fit score produced after
    crawling.
    """

    query: str
    category: str
    priority: int

    def __post_init__(self) -> None:
        if self.category not in VALID_CATEGORIES:
            raise ValueError(
                f"Unknown query category {self.category!r}; "
                f"expected one of {sorted(VALID_CATEGORIES)}"
            )
        if not self.query or not self.query.strip():
            raise ValueError("QuerySpec.query must be a non-empty string")

    def build_query(self) -> str:
        """Return the full Google query string with shared negative exclusions.

        The exclusions are appended exactly once.  Deterministic across calls.
        """
        return f"{self.query} " + " ".join(NEGATIVE_SITE_EXCLUSIONS)


# Approximately 8-12 high-signal queries covering distinct service
# categories.  Each phrase targets companies that SELL technical delivery.
DEFAULT_QUERY_SPECS: tuple[QuerySpec, ...] = (
    QuerySpec("AI automation agency", "automation", 90),
    QuerySpec("AI agent development agency", "ai-agents", 95),
    QuerySpec("generative AI development agency", "generative-ai", 85),
    QuerySpec("LLM development consultancy", "llm", 80),
    QuerySpec("RAG development consultancy", "rag", 75),
    QuerySpec("AI implementation partner", "ai-consulting", 70),
    QuerySpec("custom AI development company", "custom-development", 88),
    QuerySpec("AI software development agency", "custom-development", 82),
    QuerySpec("AI engineering consultancy", "ai-consulting", 72),
    QuerySpec("AI product development studio", "product-development", 78),
    QuerySpec("workflow automation consultancy", "automation", 68),
    QuerySpec("generative AI consulting boutique", "ai-consulting", 74),
)


def build_queries(specs: tuple[QuerySpec, ...] | None = None) -> list[str]:
    """Return the list of full Google query strings for the given specs.

    Defaults to :data:`DEFAULT_QUERY_SPECS`.  Output is deterministic and
    ordered by descending ``priority`` (stable on insertion order for ties).
    """
    specs = specs if specs is not None else DEFAULT_QUERY_SPECS
    # Sort by descending priority; stable sort preserves declaration order on
    # ties so the output is fully deterministic.
    ordered = sorted(specs, key=lambda s: s.priority, reverse=True)
    return [s.build_query() for s in ordered]


def ordered_query_specs(
    specs: tuple[QuerySpec, ...] | None = None,
) -> list[QuerySpec]:
    """Return specs ordered by descending priority (deterministic)."""
    specs = specs if specs is not None else DEFAULT_QUERY_SPECS
    return sorted(specs, key=lambda s: s.priority, reverse=True)


# Backwards-compatible alias.  Existing callers/tests that import
# ``DEFAULT_QUERIES`` get the *base* query phrases (without exclusions) in
# declaration order, matching the historical shape of a plain list[str].
DEFAULT_QUERIES: list[str] = [s.query for s in DEFAULT_QUERY_SPECS]

# Kept for backwards compatibility.  New code should use
# ``app.candidate_filter.BLOCKED_DOMAINS`` and ``is_blocked_domain`` for
# safe normalized suffix matching.
BLOCKED_HOSTS = BLOCKED_DOMAINS


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str
    query: str
    # Optional metadata populated by the discovery layer.  ``query_category``
    # is the :class:`QuerySpec` category that produced this hit (empty for
    # ad-hoc searches).  ``query_rank`` is the 0-based position of the query
    # spec in the run order, used as a deterministic tie-breaker.
    query_category: str = ""
    query_rank: int = 0
    # 0-based position of this hit within its search result list, used as a
    # secondary tie-breaker so earlier Serper ranks win when priority ties.
    result_rank: int = 0


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
            result_rank=idx,
        )
        for idx, i in enumerate(data.get("organic", []))
        if i.get("link")
    ]
    elapsed = time.perf_counter() - start
    logger.info("%d results returned in %.2fs", len(hits), elapsed)
    return hits

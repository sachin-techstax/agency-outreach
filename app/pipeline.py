from __future__ import annotations

import json
from urllib.parse import urlparse

from .config import settings
from .contacts import discover_contact
from .db import upsert_lead, update_lead
from .llm import analyze_agency, draft_outreach
from .scoring import score_agency
from .scrape import crawl_company, domain_of, root_url
from .search import BLOCKED_HOSTS, DEFAULT_QUERIES, search_serper


def company_from(title: str, domain: str) -> str:
    if title:
        for sep in [" | ", " - ", " — ", " – "]:
            if sep in title:
                title = title.split(sep)[0]
        if 2 <= len(title.strip()) <= 80:
            return title.strip()
    return domain.split(".")[0].replace("-", " ").title()


def run(limit: int | None = None) -> dict:
    target = limit or settings.discovery_limit
    candidates = {}

    for query in DEFAULT_QUERIES:
        for hit in search_serper(query, num=10):
            domain = domain_of(hit.url)
            host = urlparse(hit.url).netloc.lower()
            if not domain or host in BLOCKED_HOSTS:
                continue
            if domain not in candidates:
                candidates[domain] = hit
            if len(candidates) >= target * 3:
                break
        if len(candidates) >= target * 3:
            break

    processed = 0
    drafted = 0
    for domain, hit in candidates.items():
        if processed >= target:
            break
        try:
            site = crawl_company(hit.url)
            if len(site["text"]) < 200:
                continue
            company = company_from(site["title"] or hit.title, domain)
            s = score_agency(site["text"])
            contact = discover_contact(site["text"], site["pages"], site["domain"])
            analysis = analyze_agency(company, site["root"], site["text"])

            lead_id = upsert_lead({
                "company": company,
                "domain": site["domain"],
                "website": site["root"],
                "source_query": hit.query,
                "source_url": hit.url,
                "summary": analysis.get("summary", ""),
                "services": analysis.get("services", ""),
                "score": s.value,
                "score_reasons": json.dumps(s.reasons),
                "fit_reason": analysis.get("fit_reason", ""),
                "proof_project": analysis.get("proof_project", "WingerX"),
                "outreach_angle": analysis.get("outreach_angle", ""),
                **contact,
                "status": "qualified" if s.value >= settings.min_score else "discovered",
            })
            processed += 1

            if s.value >= settings.min_score:
                subject, body = draft_outreach(
                    company,
                    analysis.get("fit_reason", ""),
                    analysis.get("proof_project", "WingerX"),
                    analysis.get("outreach_angle", ""),
                )
                update_lead(lead_id, subject=subject, draft=body, status="drafted")
                drafted += 1
        except Exception as exc:
            # Skip broken/blocked sites without killing the daily run.
            continue

    return {"processed": processed, "drafted": drafted, "candidate_domains": len(candidates)}

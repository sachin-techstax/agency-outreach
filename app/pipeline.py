from __future__ import annotations

import json
import time
from urllib.parse import urlparse

from .config import settings
from .contacts import discover_contact
from .db import upsert_lead, update_lead
from .llm import analyze_agency, draft_outreach
from .logging_config import get_logger
from .scoring import score_agency
from .scrape import crawl_company, domain_of, root_url
from .search import BLOCKED_HOSTS, DEFAULT_QUERIES, search_serper

logger = get_logger("pipeline")


def company_from(title: str, domain: str) -> str:
    if title:
        for sep in [" | ", " - ", " — ", " – "]:
            if sep in title:
                title = title.split(sep)[0]
        if 2 <= len(title.strip()) <= 80:
            return title.strip()
    return domain.split(".")[0].replace("-", " ").title()


def _classify_failure(exc: Exception) -> str:
    """Return a short human-readable failure category for an exception."""
    name = type(exc).__name__
    msg = str(exc).lower()
    if "timeout" in name.lower() or "timeout" in msg:
        return "ConnectTimeout"
    if "403" in msg:
        return "HTTP 403"
    if "429" in msg:
        return "HTTP 429"
    if "401" in msg:
        return "HTTP 401"
    if "dns" in msg or "name or service not known" in msg:
        return "DNS error"
    if "json" in name.lower() or "json" in msg:
        return "JSON parse error"
    return name


def _discover_candidates(target: int) -> dict:
    candidates: dict[str, object] = {}
    for query in DEFAULT_QUERIES:
        try:
            hits = search_serper(query, num=10)
        except Exception as exc:
            logger.error("Search failed for query %r: %s", query, exc)
            continue
        for hit in hits:
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
    logger.info("Discovered %d unique candidate domains", len(candidates))
    return candidates


def run(limit: int | None = None) -> dict:
    target = limit or settings.discovery_limit
    batch_start = time.perf_counter()
    logger.info("Starting agency discovery. Target: %d", target)

    candidates = _discover_candidates(target)

    processed = 0
    drafted = 0
    qualified = 0
    skipped = 0
    failed = 0
    no_contact = 0
    below_score = 0
    failures: list[tuple[str, str]] = []

    for domain, hit in candidates.items():
        if processed >= target:
            break
        index = processed + skipped + failed + 1
        logger.info("Processing agency %d/%d: %s", index, target, domain)
        site_start = time.perf_counter()
        try:
            site = crawl_company(hit.url)
            if len(site["text"]) < 200:
                logger.warning("Skipping %s: insufficient content (%d chars)", domain, len(site["text"]))
                skipped += 1
                continue
            company = company_from(site["title"] or hit.title, domain)
            s = score_agency(site["text"])
            logger.info("%s score: %d", domain, s.value)
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

            if s.value < settings.min_score:
                below_score += 1
                logger.info("Below threshold (%d < %d): %s", s.value, settings.min_score, domain)
                continue

            qualified += 1
            if not contact.get("contact_email"):
                no_contact += 1
                logger.info("No contact email for %s; drafting anyway", domain)

            subject, body = draft_outreach(
                company,
                analysis.get("fit_reason", ""),
                analysis.get("proof_project", "WingerX"),
                analysis.get("outreach_angle", ""),
            )
            update_lead(lead_id, subject=subject, draft=body, status="drafted")
            drafted += 1
            logger.info("Draft created for %s", domain)
        except Exception as exc:
            logger.exception("Failed processing agency %s", domain)
            failed += 1
            failures.append((domain, _classify_failure(exc)))
            continue
        finally:
            elapsed = time.perf_counter() - site_start
            logger.info("Finished %s in %.1fs", domain, elapsed)

    batch_elapsed = time.perf_counter() - batch_start
    summary = {
        "processed": processed,
        "drafted": drafted,
        "candidate_domains": len(candidates),
        "qualified": qualified,
        "skipped": skipped,
        "failed": failed,
        "no_contact": no_contact,
        "below_score": below_score,
        "duration_s": round(batch_elapsed, 1),
        "failures": failures,
    }
    _log_summary(summary)
    return summary


def _log_summary(summary: dict) -> None:
    logger.info(
        "Batch complete: processed=%d drafted=%d skipped=%d failed=%d",
        summary["processed"],
        summary["drafted"],
        summary["skipped"],
        summary["failed"],
    )
    lines = [
        "Batch complete",
        "--------------",
        f"Candidates discovered: {summary['candidate_domains']}",
        f"Processed:             {summary['processed']}",
        f"Qualified:             {summary['qualified']}",
        f"Drafted:               {summary['drafted']}",
        f"Below threshold:       {summary['below_score']}",
        f"No contact found:      {summary['no_contact']}",
        f"Skipped:               {summary['skipped']}",
        f"Failed:                {summary['failed']}",
        f"Duration:              {summary['duration_s']}s",
    ]
    if summary["failures"]:
        lines.append("")
        lines.append("Failures:")
        for domain, reason in summary["failures"]:
            lines.append(f"- {domain}: {reason}")
    # Use print for the final formatted block so it renders as a clean summary
    # regardless of log level (operational logging still flows through logger).
    print("\n".join(lines))

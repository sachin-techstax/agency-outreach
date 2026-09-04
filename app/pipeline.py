from __future__ import annotations

import json
import time
from typing import Callable
from urllib.parse import urlparse

from .candidate_filter import evaluate_candidate, normalize_domain
from .commercial_fit import score_commercial_fit
from .company_identity import extract_company_name
from .config import settings
from .contacts import discover_contact
from .db import (
    is_workflow_state_protected,
    upsert_lead,
    update_lead,
    get_lead_by_domain,
    get_suppressed_domains,
)
from .discovery_priority import DiscoveryPriority, score_discovery_priority
from .llm import analyze_agency, draft_outreach
from .logging_config import get_logger
from .scrape import crawl_company, domain_of, root_url
from .search import (
    DEFAULT_QUERY_SPECS,
    SearchHit,
    ordered_query_specs,
    search_serper,
)

logger = get_logger("pipeline")
qual_logger = get_logger("qualification")
discovery_logger = get_logger("discovery")

# Optional progress callback signature: receives a dict snapshot.
ProgressCallback = Callable[[dict], None]


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


def _discover_candidates(
    target: int,
    progress_cb: ProgressCallback | None = None,
) -> tuple[dict, dict, dict]:
    """Run discovery searches, filter candidates, and return eligible domains.

    Returns a tuple of ``(candidates, priorities, discovery_stats)`` where:

    - *candidates* maps eligible domain strings to the BEST accepted
      :class:`SearchHit` for that domain (highest discovery priority, with
      deterministic tie-breaking), ordered by descending discovery priority.
    - *priorities* maps each eligible domain to its
      :class:`DiscoveryPriority`.
    - *discovery_stats* contains per-run counts plus per-query observability.

    Strategy (V1 simplicity, bounded Serper cost):

    1. Run ALL configured :class:`QuerySpec` (ordered by descending priority).
       We do not stop early just because the first queries produced
       ``target * 3`` eligible domains -- later query categories may surface
       stronger prospects.  Serper call count is bounded by the spec count.
    2. For each result: normalize domain, run :func:`evaluate_candidate`.
    3. For each accepted hit, score it with
       :func:`score_discovery_priority` and keep the BEST hit per domain
       (so ``/services/ai-agent-development`` beats ``/blog/ai-trends``).
    4. Rank the final eligible pool by descending discovery priority with
       deterministic tie-breaking (query rank, then result rank).

    Raises ``RuntimeError`` if every configured search query fails.  A
    successful search that returns zero organic results is not treated as a
    failure.  If all results are rejected by candidate filtering, an empty
    candidates dict is returned (this is NOT a search failure).
    """
    specs = ordered_query_specs(DEFAULT_QUERY_SPECS)

    # best_hit[domain] = (priority, hit); we keep the highest-priority hit.
    best_hit: dict[str, tuple[DiscoveryPriority, SearchHit]] = {}
    raw_domains: set[str] = set()
    # Track rejected domains that never produced an accepted hit, for stats.
    rejected_only: set[str] = set()
    # Per-domain best accepted priority (for stats/observability).
    domain_priority: dict[str, DiscoveryPriority] = {}

    search_attempts = 0
    search_success = 0
    search_failed = 0
    search_results_total = 0
    per_query: list[dict] = []

    for qrank, spec in enumerate(specs):
        query = spec.build_query()
        search_attempts += 1
        query_unique: set[str] = set()
        query_accepted = 0
        query_rejected = 0
        try:
            hits = search_serper(query, num=10)
        except Exception as exc:
            search_failed += 1
            logger.error("Search failed for query %r: %s", query, exc)
            per_query.append({
                "query_rank": qrank,
                "category": spec.category,
                "query": spec.query,
                "results": 0,
                "unique": 0,
                "accepted": 0,
                "rejected": 0,
                "selected": 0,
                "failed": True,
            })
            continue
        search_success += 1
        search_results_total += len(hits)

        for hit in hits:
            # Stamp provenance metadata onto the hit object.  Use the
            # human-readable base QuerySpec query (not the expanded -site
            # string) so source_query provenance stays readable.
            hit.query = spec.query
            hit.query_category = spec.category
            hit.query_rank = qrank

            domain = normalize_domain(hit.url)
            if not domain:
                continue
            query_unique.add(domain)
            raw_domains.add(domain)

            decision = evaluate_candidate(hit)
            if not decision.accepted:
                query_rejected += 1
                logger.debug("Rejected %s: %s", domain, decision.reason)
                # Mark as rejected-only unless a later/earlier hit accepted it.
                if domain not in best_hit:
                    rejected_only.add(domain)
                continue

            # Accepted: score and consider for best-hit-per-domain.
            query_accepted += 1
            rejected_only.discard(domain)
            priority = score_discovery_priority(hit)
            logger.debug(
                "Ranked %s priority=%d reasons=%s",
                domain, priority.score, ",".join(priority.reasons) or "-",
            )
            existing = best_hit.get(domain)
            if existing is None or _is_better_hit(priority, hit, existing[0], existing[1]):
                best_hit[domain] = (priority, hit)
                domain_priority[domain] = priority

        per_query.append({
            "query_rank": qrank,
            "category": spec.category,
            "query": spec.query,
            "results": len(hits),
            "unique": len(query_unique),
            "accepted": query_accepted,
            "rejected": query_rejected,
            "selected": 0,  # filled after ranking
            "failed": False,
        })
        discovery_logger.info(
            "Query %s: results=%d unique=%d accepted=%d rejected=%d",
            spec.category,
            len(hits),
            len(query_unique),
            query_accepted,
            query_rejected,
        )
        if progress_cb is not None:
            progress_cb({
                "stage": "discovery",
                "queries_completed": search_attempts,
                "raw_domains": len(raw_domains),
                "eligible_domains": len(best_hit),
            })

    # --- rank the eligible pool deterministically ---
    ranked = sorted(
        best_hit.items(),
        key=lambda item: (
            -item[1][0].score,        # discovery priority descending
            item[1][1].query_rank,    # earlier query wins
            item[1][1].result_rank,   # earlier Serper position wins
            item[0],                  # domain name final tie-break
        ),
    )
    candidates: dict[str, SearchHit] = {}
    candidates_priority: dict[str, DiscoveryPriority] = {}
    for domain, (priority, hit) in ranked:
        candidates[domain] = hit
        candidates_priority[domain] = priority

    # Fill in "selected" counts per query.  Track by query_rank (the stable
    # identity of each individual QuerySpec) -- NOT by category, because
    # multiple QuerySpecs share categories (e.g. two "automation" specs).
    selected_by_rank: dict[int, int] = {}
    for hit in candidates.values():
        selected_by_rank[hit.query_rank] = selected_by_rank.get(hit.query_rank, 0) + 1
    for q in per_query:
        q["selected"] = selected_by_rank.get(q["query_rank"], 0)

    rejected_domains = rejected_only
    eligible_count = len(candidates)

    # Discovery summary observability.
    discovery_logger.info(
        "Discovery searches: attempted=%d success=%d failed=%d",
        search_attempts, search_success, search_failed,
    )
    discovery_logger.info(
        "Discovery pool: raw_unique=%d rejected=%d eligible=%d ranked=%d",
        len(raw_domains), len(rejected_domains), eligible_count, eligible_count,
    )
    if search_success == 0 and search_attempts > 0:
        raise RuntimeError(
            f"Agency discovery failed: all {search_attempts} Serper searches failed."
        )

    # Per-query summary block.
    summary_lines = [
        "Discovery queries",
        "-----------------",
        f"Queries attempted: {search_attempts}",
        f"Queries succeeded: {search_success}",
        f"Raw unique domains: {len(raw_domains)}",
        f"Eligible domains: {eligible_count}",
        f"Ranked pool: {eligible_count}",
    ]
    discovery_logger.info("\n".join(summary_lines))

    avg_priority = (
        round(sum(p.score for p in candidates_priority.values()) / eligible_count, 1)
        if eligible_count else 0.0
    )
    stats = {
        "raw_candidate_domains": len(raw_domains),
        "rejected_candidate_domains": len(rejected_domains),
        "candidate_domains": eligible_count,
        "ranked_candidate_domains": eligible_count,
        "query_count": search_attempts,
        "search_results_total": search_results_total,
        "candidate_priority_avg": avg_priority,
        "per_query": per_query,
    }
    logger.info(
        "Discovery filtering: raw_domains=%d rejected=%d eligible=%d",
        stats["raw_candidate_domains"],
        stats["rejected_candidate_domains"],
        stats["candidate_domains"],
    )
    if search_success > 0 and eligible_count == 0:
        logger.info(
            "Search completed successfully, but no eligible agency candidates "
            "remained after filtering."
        )
    else:
        logger.info("Discovered %d eligible candidate domains", eligible_count)
    return candidates, candidates_priority, stats


def _is_better_hit(
    new_priority: DiscoveryPriority,
    new_hit: SearchHit,
    old_priority: DiscoveryPriority,
    old_hit: SearchHit,
) -> bool:
    """Return True if *new_hit* should replace *old_hit* for a domain.

    Tie-breaking: higher priority, then earlier query rank, then earlier
    Serper result rank.
    """
    if new_priority.score != old_priority.score:
        return new_priority.score > old_priority.score
    if new_hit.query_rank != old_hit.query_rank:
        return new_hit.query_rank < old_hit.query_rank
    return new_hit.result_rank < old_hit.result_rank


def discover_only(
    limit: int | None = None,
    progress_cb: ProgressCallback | None = None,
) -> dict:
    """Run discovery only (Serper + filter + dedupe + priority ranking).

    Read-only with respect to leads: no crawl, no OpenAI, no contact
    discovery, no Gmail, no DB writes.  Returns a summary dict with the
    full ranked pool metrics and per-query observability, plus a
    ``ranked`` list containing only the top ``limit`` rows for display.

    The full bounded discovery pool is always built and ranked; ``limit``
    only truncates the displayed/output rows, not the pool itself.

    Returned metrics:
      - ``candidate_domains``: total eligible domains in the full pool
      - ``ranked_candidate_domains``: total ranked domains in the full pool
      - ``displayed_candidate_domains``: min(limit, ranked_candidate_domains)
      - ``ranked``: top ``limit`` rows only
    """
    target = limit or settings.discovery_limit
    logger.info("Starting discovery-only run. Pool target: %d", target)
    candidates, priorities, stats = _discover_candidates(target, progress_cb)

    full_pool_size = len(candidates)
    display_n = min(target, full_pool_size)

    ranked_rows: list[dict] = []
    for rank, (domain, hit) in enumerate(candidates.items(), start=1):
        if rank > display_n:
            break
        prio = priorities.get(domain)
        ranked_rows.append({
            "rank": rank,
            "domain": domain,
            "priority": prio.score if prio else 0,
            "reasons": ",".join(prio.reasons) if prio else "",
            "category": hit.query_category,
            "source_query": hit.query,
            "title": hit.title,
            "url": hit.url,
        })
    return {
        "query_count": stats["query_count"],
        "search_results_total": stats["search_results_total"],
        "raw_candidate_domains": stats["raw_candidate_domains"],
        "rejected_candidate_domains": stats["rejected_candidate_domains"],
        "candidate_domains": stats["candidate_domains"],
        "ranked_candidate_domains": stats["ranked_candidate_domains"],
        "displayed_candidate_domains": display_n,
        "candidate_priority_avg": stats["candidate_priority_avg"],
        "per_query": stats["per_query"],
        "ranked": ranked_rows,
    }


def run(
    limit: int | None = None,
    progress_cb: ProgressCallback | None = None,
) -> dict:
    target = limit or settings.discovery_limit
    batch_start = time.perf_counter()
    logger.info("Starting agency discovery. Target: %d", target)

    candidates, priorities, discovery_stats = _discover_candidates(target, progress_cb)

    # --- suppress existing handled leads AFTER ranking, BEFORE crawl ---
    # This is DB-aware suppression: domains that already have a lead in a
    # suppressed status (drafted, approved, gmail_drafted, sent,
    # do_not_contact) are removed from the attempt pool so they do not
    # consume fresh attempt slots.  Retryable statuses (rejected-fit,
    # rejected, discovered, qualified) are NOT suppressed.
    ranked_domains = list(candidates.keys())
    suppressed = get_suppressed_domains(ranked_domains)
    suppressed_by_status: dict[str, int] = {}
    for domain, status in suppressed.items():
        suppressed_by_status[status] = suppressed_by_status.get(status, 0) + 1
        logger.info("Suppressed existing lead %s status=%s", domain, status)
        # Remove from the attempt pool (candidates is an ordered dict by
        # descending priority, so remaining domains stay ranked).
        del candidates[domain]
        # Clean up priorities side-channel.
        priorities.pop(domain, None)

    fresh_pool_size = len(candidates)
    if suppressed:
        logger.info(
            "Existing lead suppression: suppressed=%d fresh_retryable_pool=%d",
            len(suppressed), fresh_pool_size,
        )
    if fresh_pool_size == 0 and suppressed:
        logger.info(
            "No fresh/retryable candidates remain after existing-lead "
            "suppression."
        )
    if progress_cb is not None:
        progress_cb({
            "stage": "suppression",
            "raw_candidate_domains": discovery_stats["raw_candidate_domains"],
            "candidate_domains": discovery_stats["candidate_domains"],
            "fresh_retryable_pool": fresh_pool_size,
            "suppressed_existing": len(suppressed),
            "target": min(target, fresh_pool_size),
        })

    attempted = 0
    processed = 0
    drafted = 0
    qualified = 0
    skipped = 0
    failed = 0
    no_contact = 0
    below_score = 0
    failures: list[tuple[str, str]] = []

    # LLM cost counters (attempted = API call invoked, even if it raises)
    llm_analysis_calls = 0          # analyze_agency invocations attempted
    llm_skipped_below_threshold = 0 # candidates that skipped LLM entirely
    outreach_draft_calls = 0        # draft_outreach invocations attempted
    outreach_drafts_generated = 0   # draft_outreach calls that succeeded
    protected_existing = 0          # existing leads found in protected state
    protected_outreach_skipped = 0  # protected leads where outreach was skipped

    def _emit_progress(current_domain: str | None) -> None:
        if progress_cb is None:
            return
        progress_cb({
            "stage": "processing",
            "attempted": attempted,
            "target": min(target, fresh_pool_size),
            "current_domain": current_domain or "",
            "processed": processed,
            "qualified": qualified,
            "drafted": drafted,
            "below_score": below_score,
            "no_contact": no_contact,
            "skipped": skipped,
            "failed": failed,
        })

    for domain, hit in candidates.items():
        if attempted >= target:
            break
        attempted += 1
        dprio = priorities.get(domain)
        if dprio is not None:
            logger.info(
                "Processing agency %d/%d: %s discovery_priority=%d",
                attempted, target, domain, dprio.score,
            )
        else:
            logger.info("Processing agency %d/%d: %s", attempted, target, domain)
        _emit_progress(domain)
        site_start = time.perf_counter()
        try:
            site = crawl_company(hit.url)
            if len(site["text"]) < 200:
                logger.warning("Skipping %s: insufficient content (%d chars)", domain, len(site["text"]))
                skipped += 1
                continue

            # --- deterministic commercial-fit qualification (single source of truth) ---
            company = extract_company_name(site["title"] or hit.title, domain)
            fit = score_commercial_fit(site["text"])
            qual_logger.info(
                "%s commercial category=%s score=%d",
                domain, fit.category, fit.score,
            )
            logger.info("%s score: %d category: %s", domain, fit.score, fit.category)

            # --- check for existing protected lead ---
            existing = get_lead_by_domain(site["domain"])
            if existing and is_workflow_state_protected(existing["status"]):
                protected_existing += 1
                protected_outreach_skipped += 1
                logger.info(
                    "Existing protected lead %s status=%s; preserving workflow "
                    "state and skipping outreach regeneration",
                    domain, existing["status"],
                )
                # Refresh only safe deterministic research metadata.
                # upsert_lead() will drop protected fields automatically.
                upsert_lead({
                    "company": company,
                    "domain": site["domain"],
                    "website": site["root"],
                    "source_query": hit.query,
                    "source_url": hit.url,
                    "score": fit.score,
                    "score_reasons": json.dumps(fit.reasons),
                })
                processed += 1
                continue

            # --- below threshold: persist lightweight lead, skip LLM ---
            if fit.score < settings.min_score:
                below_score += 1
                llm_skipped_below_threshold += 1
                logger.info(
                    "Below threshold commercial fit: %s (%d < %d)",
                    domain, fit.score, settings.min_score,
                )
                logger.info(
                    "Skipping LLM analysis for %s: commercial score below threshold",
                    domain,
                )
                upsert_lead({
                    "company": company,
                    "domain": site["domain"],
                    "website": site["root"],
                    "source_query": hit.query,
                    "source_url": hit.url,
                    "summary": "",
                    "services": "",
                    "score": fit.score,
                    "score_reasons": json.dumps(fit.reasons),
                    "fit_reason": "",
                    "proof_project": "",
                    "outreach_angle": "",
                    "contact_email": "",
                    "contact_source": "",
                    "contact_name": "",
                    "contact_role": "",
                    "contact_quality": "",
                    "status": "rejected-fit",
                })
                processed += 1
                continue

            # --- qualified: run LLM analysis + contact discovery + outreach ---
            qualified += 1
            contact = discover_contact(
                site["text"], site["pages"], site["domain"],
                site.get("mailtos", []),
            )
            if not contact.get("contact_email"):
                no_contact += 1
                logger.info("No contact email for %s; drafting anyway", domain)

            # Count the attempt BEFORE the call so a raise still increments.
            llm_analysis_calls += 1
            analysis = analyze_agency(company, site["root"], site["text"])

            lead_id = upsert_lead({
                "company": company,
                "domain": site["domain"],
                "website": site["root"],
                "source_query": hit.query,
                "source_url": hit.url,
                "summary": analysis.get("summary", ""),
                "services": analysis.get("services", ""),
                "score": fit.score,
                "score_reasons": json.dumps(fit.reasons),
                "fit_reason": analysis.get("fit_reason", ""),
                "proof_project": analysis.get("proof_project", "WingerX"),
                "outreach_angle": analysis.get("outreach_angle", ""),
                **contact,
                "status": "qualified",
            })

            # Count the attempt BEFORE the call so a raise still increments.
            outreach_draft_calls += 1
            subject, body = draft_outreach(
                company,
                analysis.get("fit_reason", ""),
                analysis.get("proof_project", "WingerX"),
                analysis.get("outreach_angle", ""),
            )
            outreach_drafts_generated += 1
            update_lead(lead_id, subject=subject, draft=body, status="drafted")
            drafted += 1
            logger.info("Draft created for %s", domain)
            processed += 1
        except Exception as exc:
            logger.exception("Failed processing agency %s", domain)
            failed += 1
            failures.append((domain, _classify_failure(exc)))
            continue
        finally:
            elapsed = time.perf_counter() - site_start
            logger.info("Finished %s in %.1fs", domain, elapsed)
            _emit_progress(None)

    batch_elapsed = time.perf_counter() - batch_start
    summary = {
        "attempted": attempted,
        "processed": processed,
        "drafted": drafted,
        "candidate_domains": discovery_stats["candidate_domains"],
        "raw_candidate_domains": discovery_stats["raw_candidate_domains"],
        "rejected_candidate_domains": discovery_stats["rejected_candidate_domains"],
        "ranked_candidate_domains": discovery_stats["ranked_candidate_domains"],
        "query_count": discovery_stats["query_count"],
        "search_results_total": discovery_stats["search_results_total"],
        "candidate_priority_avg": discovery_stats["candidate_priority_avg"],
        "qualified": qualified,
        "skipped": skipped,
        "failed": failed,
        "no_contact": no_contact,
        "below_score": below_score,
        "llm_analysis_calls": llm_analysis_calls,
        "llm_skipped_below_threshold": llm_skipped_below_threshold,
        "outreach_draft_calls": outreach_draft_calls,
        "outreach_drafts_generated": outreach_drafts_generated,
        "protected_existing": protected_existing,
        "protected_outreach_skipped": protected_outreach_skipped,
        "suppressed_existing": len(suppressed),
        "suppressed_by_status": suppressed_by_status,
        "fresh_retryable_pool": fresh_pool_size,
        "duration_s": round(batch_elapsed, 1),
        "failures": failures,
    }
    _log_summary(summary)
    return summary


def _log_summary(summary: dict) -> None:
    logger.info(
        "Batch complete: attempted=%d processed=%d drafted=%d skipped=%d failed=%d",
        summary["attempted"],
        summary["processed"],
        summary["drafted"],
        summary["skipped"],
        summary["failed"],
    )
    lines = [
        "Batch complete",
        "--------------",
        "Discovery",
        "---------",
        f"Queries executed:           {summary['query_count']}",
        f"Search results total:        {summary['search_results_total']}",
        f"Raw candidate domains:      {summary['raw_candidate_domains']}",
        f"Rejected before crawl:       {summary['rejected_candidate_domains']}",
        f"Eligible candidate domains:  {summary['candidate_domains']}",
        f"Ranked candidate domains:    {summary['ranked_candidate_domains']}",
        f"Candidate priority avg:      {summary['candidate_priority_avg']}",
        "",
        "Existing lead suppression",
        "-------------------------",
        f"Suppressed total:        {summary['suppressed_existing']}",
    ]
    by_status = summary.get("suppressed_by_status") or {}
    status_labels = [
        ("drafted", "Drafted"),
        ("approved", "Approved"),
        ("gmail_drafted", "Gmail drafted"),
        ("sent", "Sent"),
        ("do_not_contact", "Do not contact"),
    ]
    for key, label in status_labels:
        lines.append(f"{label + ':':24s} {by_status.get(key, 0)}")
    lines.append(f"Fresh/retryable pool:   {summary['fresh_retryable_pool']}")
    lines += [
        "",
        "Attempts",
        "--------",
        f"Attempted:            {summary['attempted']}",
        f"Processed:            {summary['processed']}",
        f"Qualified:            {summary['qualified']}",
        f"Drafted:              {summary['drafted']}",
        f"Below threshold:      {summary['below_score']}",
        f"No contact found:     {summary['no_contact']}",
        f"Skipped:              {summary['skipped']}",
        f"Failed:               {summary['failed']}",
        "",
        "LLM",
        "---",
        f"Analysis calls (attempted):    {summary['llm_analysis_calls']}",
        f"Skipped below threshold:       {summary['llm_skipped_below_threshold']}",
        f"Outreach calls (attempted):    {summary['outreach_draft_calls']}",
        f"Outreach drafts generated:     {summary['outreach_drafts_generated']}",
        f"Protected existing:            {summary['protected_existing']}",
        f"Protected outreach skipped:    {summary['protected_outreach_skipped']}",
        "",
        f"Duration:             {summary['duration_s']}s",
    ]
    if summary["failures"]:
        lines.append("")
        lines.append("Failures:")
        for domain, reason in summary["failures"]:
            lines.append(f"- {domain}: {reason}")
    print("\n".join(lines))


# ---------------------------------------------------------------------------
# Per-lead research refresh (protected-state safe)
# ---------------------------------------------------------------------------

# Fields that represent human workflow decisions and must NEVER be touched by
# an automated research refresh, regardless of lead status.  This is a stricter
# invariant than the upsert_lead protection: refresh never regenerates outreach
# content or moves workflow state, even for non-protected leads.
_REFRESH_PROTECTED_FIELDS = frozenset({
    "status",
    "subject",
    "draft",
    "gmail_draft_id",
    "last_contact_at",
    "followup_due_at",
})


def refresh_lead_research(lead_id: int) -> dict:
    """Re-crawl and re-research an existing lead without changing workflow state.

    Re-runs deterministic commercial-fit scoring, contact discovery and LLM
    agency analysis against the lead's current website, then persists the
    refreshed research metadata (score, summary, services, fit_reason,
    proof_project, outreach_angle) and contact fields via explicit
    :func:`update_lead` calls.

    Safety invariants:
    - Workflow status is NEVER changed.
    - Existing outreach ``subject``/``draft`` and ``gmail_draft_id`` are NEVER
      overwritten, even for non-protected leads.  Refresh does not regenerate
      outreach content; that is the pipeline's job for fresh/retryable leads.
    - ``last_contact_at`` and ``followup_due_at`` are NEVER touched.
    - Contact fields (email/source/quality/role/name) are updated only when
      the newly discovered contact is an improvement: a non-empty email that
      is either missing today or strictly higher quality than the existing
      one.  This lets a refresh recover a missed contact (e.g. LaunchPad Lab)
      without downgrading an already-good contact.

    Returns a summary dict describing what was refreshed.
    """
    from .db import get_lead as _get_lead
    existing = _get_lead(lead_id)
    if not existing:
        raise ValueError(f"Lead {lead_id} not found")

    domain = existing["domain"]
    website = existing["website"] or f"https://{domain}"
    logger.info("Refreshing research for lead id=%s domain=%s", lead_id, domain)

    site = crawl_company(website)
    if len(site["text"]) < 200:
        logger.warning("Refresh: insufficient content for %s (%d chars)", domain, len(site["text"]))
        return {
            "lead_id": lead_id,
            "domain": domain,
            "refreshed": False,
            "reason": "insufficient content",
        }

    company = extract_company_name(site["title"] or existing["company"], domain)
    fit = score_commercial_fit(site["text"])
    qual_logger.info(
        "%s refresh commercial category=%s score=%d",
        domain, fit.category, fit.score,
    )

    contact = discover_contact(
        site["text"], site["pages"], site["domain"], site.get("mailtos", []),
    )

    analysis = analyze_agency(company, site["root"], site["text"])

    # Research metadata is always safe to refresh.
    updates = {
        "company": company,
        "website": site["root"],
        "score": fit.score,
        "score_reasons": json.dumps(fit.reasons),
        "summary": analysis.get("summary", ""),
        "services": analysis.get("services", ""),
        "fit_reason": analysis.get("fit_reason", ""),
        "proof_project": analysis.get("proof_project", existing["proof_project"] or "WingerX"),
        "outreach_angle": analysis.get("outreach_angle", ""),
    }

    # Contact fields: only update when the new contact is an improvement.
    new_email = contact.get("contact_email", "")
    new_quality = contact.get("contact_quality", "none")
    old_email = existing["contact_email"] or ""
    old_quality = existing["contact_quality"] or "none"
    quality_rank = {"high": 3, "medium": 2, "low": 1, "none": 0}
    contact_improved = bool(new_email) and (
        not old_email or quality_rank.get(new_quality, 0) > quality_rank.get(old_quality, 0)
    )
    contact_refreshed = False
    if contact_improved:
        updates["contact_email"] = new_email
        updates["contact_source"] = contact.get("contact_source", "")
        updates["contact_name"] = contact.get("contact_name", "")
        updates["contact_role"] = contact.get("contact_role", "")
        updates["contact_quality"] = new_quality
        contact_refreshed = True
        logger.info(
            "Refresh found improved contact for %s: %s (was %s)",
            domain, new_email, old_email or "(none)",
        )

    # Defensive: never allow refresh to touch protected workflow fields even
    # if a caller accidentally included them.
    updates = {k: v for k, v in updates.items() if k not in _REFRESH_PROTECTED_FIELDS}

    update_lead(lead_id, **updates)
    logger.info("Refreshed research for lead id=%s domain=%s", lead_id, domain)

    return {
        "lead_id": lead_id,
        "domain": domain,
        "refreshed": True,
        "score": fit.score,
        "contact_refreshed": contact_refreshed,
        "contact_email": new_email if contact_refreshed else old_email,
        "contact_source": contact.get("contact_source", "") if contact_refreshed else existing["contact_source"],
        "contact_quality": new_quality if contact_refreshed else old_quality,
    }

from __future__ import annotations

from datetime import datetime, timedelta, timezone

_NOW = datetime(2026, 9, 3, 8, 45, tzinfo=timezone.utc)


def _lead(
    lead_id: int,
    company: str,
    domain: str,
    score: int,
    status: str,
    proof: str,
    email: str,
    fit_reason: str,
    services: str,
) -> dict:
    updated = _NOW - timedelta(minutes=lead_id * 3)
    return {
        "id": lead_id,
        "company": company,
        "domain": domain,
        "website": f"https://{domain}",
        "source_query": "AI implementation partner",
        "source_url": f"https://{domain}/services/ai",
        "summary": fit_reason,
        "services": services,
        "team_hint": "boutique specialist delivery team",
        "score": score,
        "score_reasons": '["custom AI delivery","client services language","implementation signal"]',
        "fit_reason": fit_reason,
        "proof_project": proof,
        "outreach_angle": "overflow AI engineering support for uneven client delivery capacity",
        "contact_name": "",
        "contact_role": "",
        "contact_email": email,
        "contact_source": "public website",
        "contact_quality": "medium" if email else "",
        "subject": f"Overflow AI engineering support for {company}" if status in {"drafted", "approved", "gmail_drafted", "sent"} else "",
        "draft": (
            f"Saw that {company} is delivering custom AI work for client teams, which usually creates uneven delivery capacity.\n\n"
            f"I build production AI systems across agents, automation, RAG and FastAPI backends; {proof} is a relevant example from my work.\n\n"
            "Do you ever bring in an external senior AI engineer when client delivery capacity gets tight?"
        ) if status in {"drafted", "approved", "gmail_drafted", "sent"} else "",
        "status": status,
        "gmail_draft_id": "demo-gmail-draft" if status in {"gmail_drafted", "sent"} else "",
        "created_at": (_NOW - timedelta(days=2)).isoformat(),
        "updated_at": updated.isoformat(),
        "last_contact_at": (_NOW - timedelta(days=1)).isoformat() if status == "sent" else None,
        "followup_due_at": (_NOW + timedelta(days=3)).isoformat() if status == "sent" else None,
    }


DEMO_LEADS = [
    _lead(1, "Northstar AI", "northstar-ai.demo", 88, "drafted", "WingerX", "hello@northstar-ai.demo",
          "Boutique AI product studio delivering custom agentic workflows and backend systems for client teams.",
          "AI agents, workflow automation, RAG, FastAPI, product engineering"),
    _lead(2, "Kite Labs", "kitelabs.demo", 84, "approved", "Forge Crew", "founder@kitelabs.demo",
          "Specialist AI consultancy with strong implementation and client-delivery signals.",
          "AI consulting, LLM applications, product development"),
    _lead(3, "SignalForge", "signalforge.demo", 79, "qualified", "Aegis", "contact@signalforge.demo",
          "Custom AI engineering partner focused on production integrations and agent workflows.",
          "AI agents, integrations, automation"),
    _lead(4, "OrbitWorks", "orbitworks.demo", 76, "rejected-fit", "WingerX", "",
          "Some AI consulting signal, but product and training language weakens overflow-delivery fit.",
          "AI consulting, workshops, software"),
    _lead(5, "Greyline Systems", "greyline.demo", 74, "sent", "Forge Crew", "ops@greyline.demo",
          "Client-services firm with relevant engineering implementation work.",
          "custom software, AI engineering, automation"),
    _lead(6, "CommonThread AI", "commonthread.demo", 71, "do_not_contact", "Aegis", "team@commonthread.demo",
          "Relevant service profile but manually excluded from outreach.",
          "AI agents, code review, automation"),
    _lead(7, "Mosaic AI", "mosaic-ai.demo", 70, "discovered", "WingerX", "hello@mosaic-ai.demo",
          "Small AI delivery team with client implementation language.",
          "AI automation, implementation"),
    _lead(8, "Vector House", "vectorhouse.demo", 69, "rejected-fit", "Aegis", "",
          "Interesting technical work but insufficient evidence of external client delivery.",
          "AI tooling, developer platform"),
]


DEMO_LATEST_RUN = {
    "type": "outreach",
    "query_count": 12,
    "search_results_total": 120,
    "raw_candidate_domains": 97,
    "rejected_candidate_domains": 42,
    "candidate_domains": 55,
    "ranked_candidate_domains": 55,
    "suppressed_existing": 7,
    "fresh_retryable_pool": 47,
    "attempted": 10,
    "processed": 9,
    "qualified": 5,
    "drafted": 5,
    "below_score": 4,
    "failed": 1,
    "duration_s": 38.4,
}


def demo_dashboard() -> dict:
    counts = {
        "drafted": 13,
        "approved": 4,
        "gmail_drafted": 2,
        "sent": 24,
        "do_not_contact": 3,
        "retryable": 47,
        "total": 93,
    }
    return {
        "mode": "demo",
        "counts": counts,
        "due_followups": 5,
        "review_queue": DEMO_LEADS[:6],
        "latest_run": DEMO_LATEST_RUN,
    }

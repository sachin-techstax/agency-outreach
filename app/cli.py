from __future__ import annotations

import csv
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .config import settings
from .db import due_followups, get_lead, init_db, list_leads, now_iso, update_lead
from .gmail_client import create_draft
from .llm import draft_followup
from .logging_config import configure_logging, get_logger
from .pipeline import discover_only, run as run_pipeline

app = typer.Typer(help="Human-approved agency outreach pipeline")
console = Console()
logger = get_logger("cli")


@app.callback()
def main_callback(
    ctx: typer.Context,
) -> None:
    """Configure logging before any subcommand runs."""
    configure_logging()


def _startup_banner(effective_limit: int, effective_log_level: str) -> None:
    openai_enabled = "enabled" if settings.openai_api_key else "disabled (deterministic fallback)"
    serper_configured = "configured" if settings.serper_api_key else "MISSING"
    lines = [
        "Agency Outreach",
        "---------------",
        f"Limit:            {effective_limit}",
        f"Minimum score:    {settings.min_score}",
        f"OpenAI:           {openai_enabled}",
        f"Serper:           {serper_configured}",
        f"Database:         {settings.db_path}",
        f"Log level:        {effective_log_level}",
    ]
    print("\n".join(lines))


def print_leads(rows) -> None:
    table = Table(show_lines=False)
    for col in ["ID", "Score", "Status", "Company", "Proof", "Email"]:
        table.add_column(col)
    for r in rows:
        table.add_row(str(r["id"]), str(r["score"]), r["status"], r["company"], r["proof_project"] or "", r["contact_email"] or "")
    console.print(table)


@app.command("init-db")
def init_db_cmd():
    init_db()
    console.print(f"Database ready: {settings.db_path}")


@app.command("run")
def run_cmd(
    limit: int = typer.Option(None, help="Max agency sites to process"),
    verbose: bool = typer.Option(False, "--verbose", help="Enable DEBUG logging for this run"),
):
    effective_log_level = "DEBUG" if verbose else settings.log_level
    if verbose:
        configure_logging(level=logging.DEBUG)
    if not settings.serper_api_key:
        raise typer.BadParameter(
            "SERPER_API_KEY is missing. Add it to .env before running the pipeline."
        )
    effective_limit = limit or settings.discovery_limit
    _startup_banner(effective_limit, effective_log_level)
    result = run_pipeline(effective_limit)
    console.print(result)


@app.command("discover")
def discover_cmd(
    limit: int = typer.Option(20, help="Max discovery pool size to rank and display"),
    verbose: bool = typer.Option(False, "--verbose", help="Enable DEBUG logging for this run"),
):
    """Discovery-only: Serper + candidate filter + dedupe + priority ranking.

    Read-only with respect to leads.  No crawling, no OpenAI, no contact
    discovery, no Gmail, no DB writes.  Useful for tuning discovery without
    spending LLM tokens.
    """
    effective_log_level = "DEBUG" if verbose else settings.log_level
    if verbose:
        configure_logging(level=logging.DEBUG)
    if not settings.serper_api_key:
        raise typer.BadParameter(
            "SERPER_API_KEY is missing. Add it to .env before running discovery."
        )
    console.print("Agency Outreach — discovery-only")
    console.print(f"Pool limit: {limit}")
    console.print(f"Log level:  {effective_log_level}")
    result = discover_only(limit)

    console.print()
    console.print("Discovery summary")
    console.print("-----------------")
    console.print(f"Queries executed:           {result['query_count']}")
    console.print(f"Search results total:        {result['search_results_total']}")
    console.print(f"Raw candidate domains:       {result['raw_candidate_domains']}")
    console.print(f"Rejected before crawl:       {result['rejected_candidate_domains']}")
    console.print(f"Eligible candidate domains:  {result['candidate_domains']}")
    console.print(f"Ranked candidate domains:    {result['ranked_candidate_domains']}")
    console.print(f"Displayed candidate domains: {result['displayed_candidate_domains']}")
    console.print(f"Candidate priority avg:      {result['candidate_priority_avg']}")

    console.print()
    console.print("Per-query")
    console.print("---------")
    qtable = Table(show_lines=False)
    for col in ["Category", "Query", "Results", "Unique", "Accepted", "Rejected", "Selected"]:
        qtable.add_column(col)
    for q in result["per_query"]:
        qtable.add_row(
            q["category"],
            q["query"],
            str(q["results"]),
            str(q["unique"]),
            str(q["accepted"]),
            str(q["rejected"]),
            str(q["selected"]),
        )
    console.print(qtable)

    console.print()
    console.print("Ranked pool")
    console.print("-----------")
    rtable = Table(show_lines=False)
    for col in ["Rank", "Domain", "Priority", "Category", "Source query", "Title"]:
        rtable.add_column(col)
    for row in result["ranked"]:
        rtable.add_row(
            str(row["rank"]),
            row["domain"],
            str(row["priority"]),
            row["category"],
            row["source_query"],
            row["title"],
        )
    console.print(rtable)


@app.command("list")
def list_cmd(status: str = "drafted", min_score: int = 0, limit: int = 50):
    print_leads(list_leads(status=status or None, min_score=min_score, limit=limit))


@app.command("show")
def show_cmd(lead_id: int):
    r = get_lead(lead_id)
    if not r:
        raise typer.BadParameter("Lead not found")
    for k in r.keys():
        console.print(f"[bold]{k}[/bold]: {r[k]}")


@app.command("approve")
def approve_cmd(lead_id: int):
    r = get_lead(lead_id)
    if not r or r["status"] not in {"drafted", "rejected"}:
        raise typer.BadParameter("Lead must exist and have a draft")
    update_lead(lead_id, status="approved")
    console.print(f"Approved lead {lead_id}")


@app.command("reject")
def reject_cmd(lead_id: int):
    update_lead(lead_id, status="rejected")
    console.print(f"Rejected lead {lead_id}")


@app.command("do-not-contact")
def do_not_contact_cmd(lead_id: int):
    """Permanently suppress a lead from normal discovery runs.

    Sets status=do_not_contact.  Historical research/outreach content is
    preserved.  Future normal ``run`` commands will suppress this domain
    before crawl.  Gmail is not touched; no email is sent.
    """
    r = get_lead(lead_id)
    if not r:
        raise typer.BadParameter("Lead not found")
    update_lead(lead_id, status="do_not_contact")
    console.print(f"Marked lead {lead_id} ({r['company']}) as do_not_contact")


@app.command("allow-contact")
def allow_contact_cmd(lead_id: int):
    """Reverse a do_not_contact flag, restoring the lead to a retryable status.

    Sets status=rejected so the domain can be rediscovered on a future normal
    ``run``.  Historical research/outreach content is preserved.
    """
    r = get_lead(lead_id)
    if not r:
        raise typer.BadParameter("Lead not found")
    if r["status"] != "do_not_contact":
        raise typer.BadParameter(
            f"Lead {lead_id} is not do_not_contact (current status: {r['status']})"
        )
    update_lead(lead_id, status="rejected")
    console.print(f"Restored lead {lead_id} ({r['company']}) to rejected (retryable)")


@app.command("gmail-drafts")
def gmail_drafts_cmd(limit: int = 10):
    rows = list_leads(status="approved", min_score=settings.min_score, limit=limit)
    created = 0
    for r in rows:
        if not r["contact_email"]:
            console.print(f"Skipping {r['company']}: no public email found")
            continue
        draft_id = create_draft(r["contact_email"], r["subject"], r["draft"])
        update_lead(r["id"], gmail_draft_id=draft_id, status="gmail_drafted")
        created += 1
        console.print(f"Created Gmail draft for {r['company']} ({draft_id})")
    console.print(f"Created {created} Gmail drafts. Review and send them manually in Gmail.")


@app.command("mark-sent")
def mark_sent_cmd(lead_id: int):
    sent_at = datetime.now(timezone.utc)
    follow = sent_at + timedelta(days=settings.followup_days)
    update_lead(
        lead_id,
        status="sent",
        last_contact_at=sent_at.isoformat(),
        followup_due_at=follow.isoformat(),
    )
    console.print(f"Marked {lead_id} sent; follow-up due {follow.date()}")


@app.command("due-followups")
def due_followups_cmd():
    rows = due_followups(now_iso())
    print_leads(rows)


@app.command("followup-draft")
def followup_draft_cmd(lead_id: int):
    r = get_lead(lead_id)
    if not r or r["status"] != "sent":
        raise typer.BadParameter("Lead must be in sent status")
    subject, body = draft_followup(r["company"], r["draft"] or "")
    console.print(f"[bold]{subject}[/bold]\n\n{body}")


@app.command("export")
def export_cmd(path: Path = Path("leads.csv"), status: str = ""):
    rows = list_leads(status=status or None, min_score=0, limit=5000)
    if not rows:
        console.print("No rows")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(dict(r) for r in rows)
    console.print(f"Exported {len(rows)} leads to {path}")


if __name__ == "__main__":
    app()

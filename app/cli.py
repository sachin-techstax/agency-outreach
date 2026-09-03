from __future__ import annotations

import csv
import logging
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .auth import validate_auth_config
from .config import settings
from .db import (
    SUPPRESSED_STATUSES,
    due_followups,
    get_lead,
    init_db,
    list_leads,
    now_iso,
    update_lead,
)
from .gmail_client import create_draft
from .llm import draft_followup
from .logging_config import configure_logging, get_logger
from .pipeline import discover_only, run as run_pipeline

PRODUCT_NAME = "PactSignal"
PRODUCT_DESCRIPTOR = "Partner intelligence & outreach"
VERSION = "0.1.0"

app = typer.Typer(
    name="pactsignal",
    help=(
        "PactSignal — partner intelligence and human-approved outreach. "
        "Discover, qualify, review and manage agency prospects from the terminal."
    ),
    no_args_is_help=True,
)
console = Console()
logger = get_logger("cli")


@app.callback()
def main_callback() -> None:
    """Configure logging before any subcommand runs."""
    configure_logging()


def _startup_banner(effective_limit: int, effective_log_level: str) -> None:
    openai_enabled = (
        "enabled"
        if settings.openai_api_key
        else "disabled (deterministic fallback)"
    )
    serper_configured = "configured" if settings.serper_api_key else "MISSING"
    lines = [
        PRODUCT_NAME,
        PRODUCT_DESCRIPTOR,
        "-" * len(PRODUCT_DESCRIPTOR),
        f"Limit:            {effective_limit}",
        f"Minimum score:    {settings.min_score}",
        f"OpenAI:           {openai_enabled}",
        f"Serper:           {serper_configured}",
        f"Database:         {settings.db_path}",
        f"Log level:        {effective_log_level}",
    ]
    print("\n".join(lines))


def _frontend_dist() -> Path:
    return Path(__file__).resolve().parents[1] / "frontend" / "dist"


def _require_private_mode(action: str) -> None:
    if settings.pactsignal_demo_mode:
        # This is an execution-policy block, not an argument-parse failure.
        # Print a stable operator-facing reason before exiting so terminal users
        # and CliRunner/automation receive the same message on stdout.
        console.print(
            f"[red]Blocked:[/red] {action} is disabled in PactSignal demo mode."
        )
        raise typer.Exit(code=2)


def _status_counts() -> tuple[list, Counter]:
    init_db()
    rows = list(list_leads(status=None, min_score=0, limit=5000))
    return rows, Counter(str(row["status"]) for row in rows)


def print_leads(rows) -> None:
    table = Table(show_lines=False)
    for col in ["ID", "Score", "Status", "Company", "Proof", "Email"]:
        table.add_column(col)
    for r in rows:
        table.add_row(
            str(r["id"]),
            str(r["score"]),
            r["status"],
            r["company"],
            r["proof_project"] or "",
            r["contact_email"] or "",
        )
    console.print(table)


@app.command("version")
def version_cmd() -> None:
    """Print the PactSignal CLI version."""
    console.print(f"{PRODUCT_NAME} {VERSION}")


@app.command("status")
def status_cmd() -> None:
    """Show pipeline, lead-state and integration status without exposing secrets."""
    rows, counts = _status_counts()
    retryable = sum(
        1 for row in rows if str(row["status"]) not in SUPPRESSED_STATUSES
    )
    followups = len(due_followups(now_iso()))

    console.print(f"[bold]{PRODUCT_NAME}[/bold] — {PRODUCT_DESCRIPTOR}")
    console.print()

    state = Table(title="Lead state", show_lines=False)
    state.add_column("Metric")
    state.add_column("Count", justify="right")
    state_rows = [
        ("Total leads", len(rows)),
        ("Fresh / retryable", retryable),
        ("Drafted", counts.get("drafted", 0)),
        ("Approved", counts.get("approved", 0)),
        ("Gmail drafted", counts.get("gmail_drafted", 0)),
        ("Sent", counts.get("sent", 0)),
        ("Do not contact", counts.get("do_not_contact", 0)),
        ("Follow-ups due", followups),
    ]
    for label, value in state_rows:
        state.add_row(label, str(value))
    console.print(state)

    console.print()
    integrations = Table(title="Runtime", show_lines=False)
    integrations.add_column("Component")
    integrations.add_column("State")
    integrations.add_row("Mode", "demo / read-only" if settings.pactsignal_demo_mode else "private")
    integrations.add_row("Serper", "configured" if settings.serper_api_key else "missing")
    integrations.add_row(
        "OpenAI",
        "configured" if settings.openai_api_key else "deterministic fallback",
    )
    integrations.add_row(
        "Gmail OAuth client",
        "present" if settings.gmail_client_secret.exists() else "not configured",
    )
    integrations.add_row(
        "Gmail token",
        "present" if settings.gmail_token_file.exists() else "not authorized",
    )
    integrations.add_row("Database", str(settings.db_path))
    integrations.add_row("Minimum score", str(settings.min_score))
    console.print(integrations)


@app.command("doctor")
def doctor_cmd(
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Exit non-zero when discovery prerequisites are missing.",
    ),
) -> None:
    """Check PactSignal runtime readiness without making external API calls."""
    checks: list[tuple[str, bool, str, bool]] = []

    try:
        init_db()
        checks.append(("SQLite", True, f"ready at {settings.db_path}", True))
    except Exception as exc:
        checks.append(("SQLite", False, f"{type(exc).__name__}: {exc}", True))

    checks.append(
        (
            "Serper",
            bool(settings.serper_api_key),
            "configured" if settings.serper_api_key else "SERPER_API_KEY missing",
            True,
        )
    )
    checks.append(
        (
            "OpenAI",
            True,
            "configured" if settings.openai_api_key else "optional fallback active",
            False,
        )
    )
    checks.append(
        (
            "Gmail OAuth client",
            settings.gmail_client_secret.exists(),
            "present" if settings.gmail_client_secret.exists() else "optional / not configured",
            False,
        )
    )
    checks.append(
        (
            "Gmail token",
            settings.gmail_token_file.exists(),
            "present" if settings.gmail_token_file.exists() else "optional / not authorized",
            False,
        )
    )
    if settings.pactsignal_demo_mode:
        auth_ok = True
        auth_detail = "not required in demo mode"
        auth_required = False
    elif not settings.pactsignal_auth_enabled:
        auth_ok = False
        auth_detail = "PACTSIGNAL_AUTH_ENABLED is false"
        auth_required = True
    else:
        try:
            validate_auth_config()
            auth_ok = True
            auth_detail = f"enabled for {settings.pactsignal_admin_username}"
        except RuntimeError as exc:
            auth_ok = False
            auth_detail = str(exc)
        auth_required = True

    checks.append(
        (
            "Operator JWT auth",
            auth_ok,
            auth_detail,
            auth_required,
        )
    )
    checks.append(
        (
            "React build",
            _frontend_dist().exists(),
            str(_frontend_dist()) if _frontend_dist().exists() else "not built in this runtime",
            False,
        )
    )

    table = Table(title=f"{PRODUCT_NAME} doctor", show_lines=False)
    table.add_column("Check")
    table.add_column("State")
    table.add_column("Detail")
    for name, ok, detail, required in checks:
        if ok:
            state = "[green]OK[/green]"
        elif required:
            state = "[red]MISSING[/red]"
        else:
            state = "[yellow]OPTIONAL[/yellow]"
        table.add_row(name, state, detail)
    console.print(table)

    required_failures = [name for name, ok, _, required in checks if required and not ok]
    if required_failures:
        console.print()
        console.print(
            "[yellow]PactSignal can still inspect stored leads, but discovery is not ready.[/yellow]"
        )
        if strict:
            raise typer.Exit(code=1)
    else:
        console.print()
        console.print("[green]PactSignal is ready for discovery.[/green]")


@app.command("serve")
def serve_cmd(
    host: str = typer.Option("127.0.0.1", help="Host interface for the operator API/UI."),
    port: int = typer.Option(8080, min=1, max=65535, help="Port for PactSignal."),
    demo: bool = typer.Option(
        False,
        "--demo",
        help="Force read-only fictional demo mode for portfolio/screenshare use.",
    ),
    reload: bool = typer.Option(
        False,
        "--reload",
        help="Enable Uvicorn auto-reload for local development.",
    ),
) -> None:
    """Serve the PactSignal FastAPI operator API and compiled React UI."""
    if demo:
        os.environ["PACTSIGNAL_DEMO_MODE"] = "true"
        object.__setattr__(settings, "pactsignal_demo_mode", True)

    effective_demo = settings.pactsignal_demo_mode
    dist = _frontend_dist()

    console.print(f"[bold]{PRODUCT_NAME}[/bold] — {PRODUCT_DESCRIPTOR}")
    console.print(f"Mode:  {'demo / read-only' if effective_demo else 'private'}")
    console.print(f"URL:   http://{host}:{port}")
    console.print(
        f"UI:    {'compiled React build found' if dist.exists() else 'API only; frontend/dist not found'}"
    )

    if not effective_demo and not settings.pactsignal_auth_enabled:
        console.print()
        console.print(
            "[yellow]Warning: private mode application authentication is disabled. "
            "Do not expose this listener beyond a trusted proxy or localhost.[/yellow]"
        )

    if effective_demo:
        console.print(
            "[green]Demo safety is active: pipeline runs, Gmail actions and persistent mutations are blocked.[/green]"
        )

    import uvicorn

    uvicorn.run(
        "app.api:app",
        host=host,
        port=port,
        reload=reload,
    )


@app.command("init-db")
def init_db_cmd() -> None:
    """Initialize or migrate the PactSignal SQLite database."""
    init_db()
    console.print(f"PactSignal database ready: {settings.db_path}")


@app.command("run")
def run_cmd(
    limit: int | None = typer.Option(None, help="Max agency sites to process"),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        help="Enable DEBUG logging for this run",
    ),
) -> None:
    """Run discovery, qualification, contact discovery and outreach drafting."""
    _require_private_mode("Pipeline execution")
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
    verbose: bool = typer.Option(
        False,
        "--verbose",
        help="Enable DEBUG logging for this run",
    ),
) -> None:
    """Run read-only Serper discovery, filtering, dedupe and priority ranking."""
    _require_private_mode("External discovery")
    effective_log_level = "DEBUG" if verbose else settings.log_level
    if verbose:
        configure_logging(level=logging.DEBUG)
    if not settings.serper_api_key:
        raise typer.BadParameter(
            "SERPER_API_KEY is missing. Add it to .env before running discovery."
        )
    console.print(f"{PRODUCT_NAME} — discovery-only")
    console.print(f"Pool limit: {limit}")
    console.print(f"Log level:  {effective_log_level}")
    result = discover_only(limit)

    console.print()
    console.print("Discovery summary")
    console.print("-----------------")
    console.print(f"Queries executed:            {result['query_count']}")
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
    for col in [
        "Category",
        "Query",
        "Results",
        "Unique",
        "Accepted",
        "Rejected",
        "Selected",
    ]:
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
def list_cmd(
    status: str = "drafted",
    min_score: int = 0,
    limit: int = 50,
) -> None:
    """List stored leads."""
    init_db()
    print_leads(list_leads(status=status or None, min_score=min_score, limit=limit))


@app.command("show")
def show_cmd(lead_id: int) -> None:
    """Show every stored field for a lead."""
    init_db()
    r = get_lead(lead_id)
    if not r:
        raise typer.BadParameter("Lead not found")
    for k in r.keys():
        console.print(f"[bold]{k}[/bold]: {r[k]}")


@app.command("approve")
def approve_cmd(lead_id: int) -> None:
    """Approve an existing outreach draft."""
    _require_private_mode("Lead mutation")
    init_db()
    r = get_lead(lead_id)
    if not r or r["status"] not in {"drafted", "rejected"} or not r["draft"]:
        raise typer.BadParameter("Lead must exist and have a draft")
    update_lead(lead_id, status="approved")
    console.print(f"Approved lead {lead_id}")


@app.command("reject")
def reject_cmd(lead_id: int) -> None:
    """Reject a lead while keeping it retryable for future discovery."""
    _require_private_mode("Lead mutation")
    init_db()
    if not get_lead(lead_id):
        raise typer.BadParameter("Lead not found")
    update_lead(lead_id, status="rejected")
    console.print(f"Rejected lead {lead_id}")


@app.command("do-not-contact")
def do_not_contact_cmd(lead_id: int) -> None:
    """Permanently suppress a lead from normal discovery runs."""
    _require_private_mode("Lead mutation")
    init_db()
    r = get_lead(lead_id)
    if not r:
        raise typer.BadParameter("Lead not found")
    update_lead(lead_id, status="do_not_contact")
    console.print(f"Marked lead {lead_id} ({r['company']}) as do_not_contact")


@app.command("allow-contact")
def allow_contact_cmd(lead_id: int) -> None:
    """Reverse a do_not_contact flag and restore the lead to retryable."""
    _require_private_mode("Lead mutation")
    init_db()
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
def gmail_drafts_cmd(limit: int = 10) -> None:
    """Create unsent Gmail drafts for approved leads."""
    _require_private_mode("Gmail actions")
    init_db()
    rows = list_leads(
        status="approved",
        min_score=settings.min_score,
        limit=limit,
    )
    created = 0
    for r in rows:
        if not r["contact_email"]:
            console.print(f"Skipping {r['company']}: no public email found")
            continue
        draft_id = create_draft(r["contact_email"], r["subject"], r["draft"])
        update_lead(r["id"], gmail_draft_id=draft_id, status="gmail_drafted")
        created += 1
        console.print(f"Created Gmail draft for {r['company']} ({draft_id})")
    console.print(
        f"Created {created} Gmail drafts. Review and send them manually in Gmail."
    )


@app.command("mark-sent")
def mark_sent_cmd(lead_id: int) -> None:
    """Mark a manually sent outreach email and schedule its follow-up."""
    _require_private_mode("Sent-state mutation")
    init_db()
    if not get_lead(lead_id):
        raise typer.BadParameter("Lead not found")
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
def due_followups_cmd() -> None:
    """List sent leads whose follow-up date is due."""
    init_db()
    rows = due_followups(now_iso())
    print_leads(rows)


@app.command("followup-draft")
def followup_draft_cmd(lead_id: int) -> None:
    """Generate a concise follow-up for a sent lead."""
    _require_private_mode("Follow-up generation")
    init_db()
    r = get_lead(lead_id)
    if not r or r["status"] != "sent":
        raise typer.BadParameter("Lead must be in sent status")
    subject, body = draft_followup(r["company"], r["draft"] or "")
    console.print(f"[bold]{subject}[/bold]\n\n{body}")


@app.command("export")
def export_cmd(
    path: Path = Path("leads.csv"),
    status: str = "",
) -> None:
    """Export stored leads to CSV."""
    init_db()
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

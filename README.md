# Agency Outreach V1

A small, human-approved pipeline for finding AI consultancies/agencies that may need overflow or white-label engineering capacity.

The system intentionally **does not auto-send cold email**. It discovers agencies, scores them, drafts short personalized outreach, lets you approve/reject, and can create Gmail drafts for approved leads. You review and send from Gmail.

## What it does

1. Searches the web for AI/automation/LLM agencies using Serper.
2. Deduplicates candidates by domain.
3. Reads a small set of public pages on each agency website.
4. Scores fit using transparent rules.
5. Uses an LLM to summarize the agency, choose the most relevant portfolio proof, and identify a grounded outreach angle.
6. Looks for public company-domain email addresses on the site.
7. Generates a **3-sentence** outreach message: hook → proof → direct question.
8. Stores everything in SQLite.
9. Requires manual approval before Gmail draft creation.
10. Tracks sent dates and follow-up due dates.

## Portfolio mapping baked in

- **WingerX** → AI automation/orchestration, integrations, agents, workflows
- **GradeWise** → full AI product/backend/MVP work
- **Aegis** → agentic code review, deterministic validation, human approval
- **Forge Crew** → multi-agent engineering/orchestration

Edit `PORTFOLIO` in `app/llm.py` if you want different wording.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env`:

```env
SERPER_API_KEY=...
OPENAI_API_KEY=...
PORTFOLIO_URL=https://contra.com/your-profile
MIN_SCORE=70
DISCOVERY_LIMIT=15
```

Initialize:

```bash
python -m app.cli init-db
```

Run a daily batch:

```bash
python -m app.cli run --limit 15
```

Review the generated leads:

```bash
python -m app.cli list --status drafted --min-score 70
python -m app.cli show 12
```

Approve or reject:

```bash
python -m app.cli approve 12
python -m app.cli reject 13
```

## Gmail drafts

The Gmail integration only needs the `gmail.compose` OAuth scope and creates unsent drafts.

1. Create a Google Cloud project.
2. Enable Gmail API.
3. Create an OAuth client of type **Desktop app**.
4. Download the JSON as `client_secret.json` into this project folder.
5. Run:

```bash
python -m app.cli gmail-drafts --limit 10
```

The first run opens Google OAuth in your browser and saves `token.json`. Approved leads with a public email become Gmail drafts. Review them manually before sending.

After you send one in Gmail:

```bash
python -m app.cli mark-sent 12
```

Then check follow-ups:

```bash
python -m app.cli due-followups
python -m app.cli followup-draft 12
```

## Suggested daily workflow

```text
08:00  run discovery/qualification
08:05  review 5-10 drafted leads
08:10  approve the good ones
08:12  create Gmail drafts
        you review + send manually
4 days later
        due-followups → draft a concise follow-up
```

A cron job can run only the **discovery/drafting** stage automatically:

```cron
0 8 * * 1-5 cd /path/to/agency-outreach-v1 && .venv/bin/python -m app.cli run --limit 15
```

Do not schedule `gmail-drafts` or sending unless you intentionally want that behavior.

## Search costs / dependencies

- **Serper**: web search API. Replace `app/search.py` if you prefer a different search provider.
- **OpenAI API**: optional. If no key is present, the app falls back to deterministic/template analysis and outreach.
- **Gmail API**: optional. You can simply export leads to CSV instead.

## Export

```bash
python -m app.cli export --path leads.csv
```

## Tests

```bash
pytest -q
```

## V1 limitations

- Website-only contact discovery is intentionally conservative. It will often find `hello@`/`contact@`, but not always the founder/CTO email.
- It does not scrape LinkedIn.
- It does not auto-send email.
- It does not verify email deliverability.
- It does not detect replies in Gmail yet.

Those are good V2 candidates once V1 is actually producing qualified leads.

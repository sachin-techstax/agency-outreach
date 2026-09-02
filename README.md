# Agency Outreach V1

A human-approved pipeline for finding AI consultancies and agencies that may need overflow or white-label AI engineering capacity.

The system intentionally **does not auto-send cold email**. It discovers agencies, scores them, drafts short personalized outreach, lets you approve or reject leads, and can create Gmail drafts for approved leads.

## What it does

1. Searches the web for AI, automation, and LLM agencies using Serper.
2. Deduplicates candidates by domain.
3. Reads a small set of public pages on each agency website.
4. Scores fit using transparent rules.
5. Uses an LLM to summarize the agency, select relevant portfolio proof, and identify a grounded outreach angle.
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

# Docker quick start

Docker Compose is the recommended runtime.

## 1. Clone and configure

```bash
git clone https://github.com/sachin-techstax/agency-outreach.git
cd agency-outreach

cp .env.example .env
mkdir -p data secrets
```

Fill in at least:

```env
SERPER_API_KEY=...
OPENAI_API_KEY=...
PORTFOLIO_URL=https://contra.com/your-profile
MIN_SCORE=70
DISCOVERY_LIMIT=15
```

On Linux, verify your UID and GID:

```bash
id -u
id -g
```

If they are not `1000`, set `LOCAL_UID` and `LOCAL_GID` in `.env` so files written into `data/` and `secrets/` stay owned by your host user.

## 2. Build

```bash
docker compose build
```

Or:

```bash
make build
```

## 3. Initialize the database

```bash
docker compose run --rm outreach init-db
```

The SQLite database is persisted at:

```text
./data/agency_outreach.db
```

## 4. Run discovery and qualification

```bash
docker compose run --rm outreach run --limit 15
```

Or:

```bash
make run LIMIT=15
```

## 5. Review leads

```bash
docker compose run --rm outreach list --status drafted --min-score 70
docker compose run --rm outreach show 12
```

Approve or reject:

```bash
docker compose run --rm outreach approve 12
docker compose run --rm outreach reject 13
```

## Gmail drafts with Docker

The Gmail integration uses the `gmail.compose` OAuth scope and only creates unsent drafts.

1. Create a Google Cloud project.
2. Enable Gmail API.
3. Create an OAuth client of type **Desktop app**.
4. Download the OAuth client JSON.
5. Save it as:

```text
./secrets/client_secret.json
```

Then run:

```bash
docker compose run --rm outreach gmail-drafts --limit 10
```

The Compose service uses host networking because Google desktop OAuth starts a temporary localhost callback server. On Linux, copy the authorization URL printed in the terminal into your normal browser if the container cannot open it automatically.

The resulting token is persisted at:

```text
./secrets/token.json
```

Approved leads with public email addresses become Gmail drafts. Review and send them manually in Gmail.

After sending a draft:

```bash
docker compose run --rm outreach mark-sent 12
```

Check follow-ups:

```bash
docker compose run --rm outreach due-followups
docker compose run --rm outreach followup-draft 12
```

## Export

Persist exports in `data/`:

```bash
docker compose run --rm outreach export --path /data/leads.csv
```

The host file will be available at:

```text
./data/leads.csv
```

## Tests

Run the test suite inside the same Docker image:

```bash
docker compose run --rm --entrypoint pytest outreach -q
```

Or:

```bash
make test
```

## Useful Make commands

```bash
make setup
make build
make init
make run LIMIT=15
make list MIN_SCORE=70
make test
make help
```

## Suggested daily workflow

```text
08:00  run discovery/qualification
08:05  review 5-10 drafted leads
08:10  approve the good ones
08:12  create Gmail drafts
        review + send manually
4 days later
        due-followups → draft a concise follow-up
```

A cron job can automate only the safe discovery/drafting stage:

```cron
0 8 * * 1-5 cd /path/to/agency-outreach && docker compose run --rm outreach run --limit 15
```

Do not schedule Gmail draft creation or sending unless you intentionally want that behavior.

# Local Python development

Docker is recommended, but local execution still works:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

python -m app.cli init-db
python -m app.cli run --limit 15
```

## Search costs and dependencies

- **Serper**: web search API. Replace `app/search.py` if you prefer another provider.
- **OpenAI API**: optional. Without a key, the app falls back to deterministic/template analysis and outreach.
- **Gmail API**: optional. You can export leads to CSV instead.

## V1 limitations

- Website-only contact discovery is intentionally conservative. It may find `hello@` or `contact@`, but not always a founder or CTO email.
- It does not scrape LinkedIn.
- It does not auto-send email.
- It does not verify email deliverability.
- It does not detect replies in Gmail yet.

Those are good V2 candidates once V1 is producing qualified leads.

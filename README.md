# Nuntago

**Partner intelligence & outreach.**

Nuntago is a human-approved partner discovery and outreach platform for finding AI consultancies and agencies that may need overflow or white-label AI engineering capacity.

It combines structured discovery, deterministic commercial-fit qualification, website research, AI-assisted personalization, persistent lead state, and a React operator console. Nuntago intentionally **does not auto-send cold email**. Human approval remains the boundary before Gmail draft creation and sending remains manual.

The repository, runtime, deployment paths and operator tooling all use the Nuntago identity.

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

# Nuntago CLI

The existing pipeline is also available as the Nuntago operator CLI. Existing commands remain compatible, but the CLI is now product-branded and includes runtime inspection helpers.

Local entry points:

```bash
./nuntago --help
python -m app --help
```

Docker entry point:

```bash
docker compose run --rm nuntago-cli --help
```

Useful operator commands:

```bash
# Safe local inspection
./nuntago status
./nuntago doctor

# Discovery-only ranking, no crawl/LLM/Gmail/DB writes
./nuntago discover --limit 20

# Full discovery + qualification + outreach drafting
./nuntago run --limit 10

# Human review workflow
./nuntago list --status drafted --min-score 70
./nuntago show 12
./nuntago approve 12
./nuntago reject 13
./nuntago do-not-contact 14

# Gmail remains draft-only
./nuntago gmail-drafts --limit 10
./nuntago mark-sent 12

# Start API/UI from the CLI
./nuntago serve
./nuntago serve --demo --port 8080
```

`nuntago doctor --strict` exits non-zero when required discovery prerequisites are missing, which is useful for shell scripts and deployment checks. It reports only configuration presence and never prints API keys or OAuth tokens.

The `serve --demo` path still forces Nuntago's fictional, read-only local portfolio mode. Production now separates the public showcase (`demo.nuntago.ergorum.com`) from the private operator workspace (`nuntago.ergorum.com`). The public browser uses the isolated `/api/public/*` demo API, while the operator API accepts validated Cloudflare Access identity (with a bearer-token fallback for tooling).

# Nuntago operator console

The React + FastAPI operator console is implemented as a separate web runtime. It uses the same SQLite database and existing pipeline primitives as the CLI.

Core screens:

- **Overview** — pipeline metrics, priority review queue, latest run, `Run discovery` and `Process prospects` actions with live run state
- **Leads** — dense searchable/filterable lead records with preview
- **Lead Review** — properties, agency intelligence, selected portfolio proof, outreach draft, human approval actions, and `Refresh contact & research`
- **Discovery** — latest read-only discovery result with ranked candidate rows, query counts, and eligibility metrics
- **Outreach runs** — persistent run history with per-run details (status, timing, qualified/drafted/failed counts, live progress)
- **Follow-ups** — sent leads with due follow-up dates

The current visual direction is a bright, record-first **Twenty × Attio** style rather than a generic admin dashboard.

## Operator workflow

The complete operator workflow is now drivable from the Nuntago UI:

```text
Run discovery
  → review eligible ranked candidates on the Discovery page
Process prospects (batch size 5 / 10 / 20)
  → watches run status instead of wondering whether the request is hanging
  → opens Leads, sees drafted and rejected-fit leads
  → opens a drafted lead, sees contact provenance (which page the email came from)
  → Refresh contact & research (re-crawls without changing protected workflow state)
  → Approve for Gmail
  → Create Gmail draft
  → review + send manually in Gmail
  → Mark as sent
  → Due follow-ups appear on the Follow-ups page
```

`Process prospects` runs the existing outreach-processing pipeline (discovery → suppression → crawl → commercial-fit scoring → analysis → contact discovery → drafting). It does **not** send email. The furthest automatic Gmail action is `Create Gmail draft`, which requires explicit human approval first.

Run state is persisted in SQLite (`runs` table) so the operator can see history and poll active runs without a manual browser refresh. Concurrent processing runs are rejected with `409`.

## Contact discovery

Nuntago crawls a company's homepage plus a bounded, deterministically ranked set of same-domain research pages. The page-priority strategy ranks contact pages above service/work/about/AI pages so a normal `/contact` is not displaced by four arbitrary service pages:

1. `contact`, `contact-us`, `contactus`, `get-in-touch`, `talk-to-us`, `connect` (highest)
2. `about`, `team`, `who-we-are`, `company`
3. `services`, `solutions`, `capabilities`
4. `work`, `case-stud`, `portfolio`, `projects`
5. `ai`, `agent`, `llm`, `automation`

Irrelevant paths (`/blog`, `/news`, `/legal`, `/jobs`, etc.) are never crawled. URLs are deduplicated by normalized form (fragments and trailing slashes stripped). The crawl is bounded to a small fixed number of extra pages.

Emails are extracted from both visible page text and `mailto:` hrefs. `mailto:hello@example.com?subject=Project` is normalized to `hello@example.com`. Only emails on the company's own domain are accepted; third-party addresses are ignored. Contact provenance is persisted as the actual page URL where the email was found (e.g. `https://launchpadlab.com/contact/`) rather than a generic `website` label.

Contact quality ranking: named/role addresses (`founder@`, `partnerships@`) > generic business addresses (`hello@`, `contact@`, `info@`, `sales@`) > no contact. Inappropriate targets (`privacy@`, `legal@`, `careers@`, `support@`, `noreply@`) are always rejected. Nuntago does not scrape LinkedIn or guess personal email addresses.

## Demo mode

For portfolio/screenshare use, enable:

```env
NUNTAGO_DEMO_MODE=true
```

Demo mode serves fictional `.demo` agencies and is intentionally read-only. It blocks:

- pipeline runs
- status mutations
- Gmail draft creation
- sent-state mutation
- any other persistent/external action exposed by the operator API

This makes it safe to show Nuntago publicly without exposing prospect data or triggering real outreach.

## Run the web UI

Build and start only the Nuntago web service:

```bash
docker compose build nuntago-web
docker compose up nuntago-web
```

The Compose service binds to localhost by default:

```text
http://localhost:8080
```

This is intentional because private mode does not yet have application authentication. Use a reverse proxy/authentication layer before exposing it beyond localhost.

Health check:

```bash
curl http://localhost:8080/api/health
```

Expected product field:

```json
{"ok":true,"product":"Nuntago"}
```

For frontend-only development:

```bash
# terminal 1
uvicorn app.api:app --host 127.0.0.1 --port 8080 --reload

# terminal 2
cd frontend
npm install
npm run dev
```

Vite runs on `http://localhost:5173` and proxies `/api` to FastAPI on port 8080.

> Production browser access uses two surfaces: `demo.nuntago.ergorum.com` is a read-only fictional showcase backed only by `/api/public/*`; `nuntago.ergorum.com` is the live operator workspace and should remain behind Cloudflare Access. The origin remains localhost-bound behind Cloudflare Tunnel.

# Docker quick start

Docker Compose is the recommended runtime.

## 1. Clone and configure

```bash
git clone https://github.com/sachin-techstax/nuntago.git
cd nuntago

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
docker compose run --rm nuntago-cli init-db
```

The SQLite database is persisted at:

```text
./data/nuntago.db
```

## 4. Run discovery and qualification

```bash
docker compose run --rm nuntago-cli run --limit 15
```

Or:

```bash
make run LIMIT=15
```

The pipeline prints a startup banner and live stage-by-stage progress so you can see exactly what it is doing:

```text
Nuntago
-------
Limit:            15
Minimum score:    70
OpenAI:           enabled
Serper:           configured
Database:         /data/nuntago.db
Log level:        INFO

2026-09-02 21:58:11 | INFO  | pipeline | Starting agency discovery. Target: 15
2026-09-02 21:58:11 | INFO  | search   | Query: "AI automation" agency
2026-09-02 21:58:12 | INFO  | search   | 10 results returned in 0.72s
2026-09-02 21:58:12 | INFO  | pipeline | Discovered 32 unique candidate domains
2026-09-02 21:58:12 | INFO  | pipeline | Processing agency 1/15: example.ai
2026-09-02 21:58:12 | INFO  | scrape   | Fetching homepage: https://example.ai
2026-09-02 21:58:14 | INFO  | scrape   | Crawled 4 pages, 18342 characters for example.ai
2026-09-02 21:58:14 | INFO  | scrape   | Crawled example.ai in 2.1s
2026-09-02 21:58:14 | INFO  | pipeline | example.ai score: 85
2026-09-02 21:58:14 | INFO  | contacts | Selected public contact hello@example.ai
2026-09-02 21:58:14 | INFO  | llm      | Analyzing agency Example with model gpt-5.6-luna
2026-09-02 21:58:16 | INFO  | llm      | Selected proof project: WingerX
2026-09-02 21:58:16 | INFO  | llm      | Agency analysis completed in 2.1s
2026-09-02 21:58:16 | INFO  | llm      | Generating outreach draft for Example
2026-09-02 21:58:17 | INFO  | llm      | Outreach draft generated in 1.0s
2026-09-02 21:58:17 | INFO  | pipeline | Draft created for example.ai
2026-09-02 21:58:17 | INFO  | pipeline | Finished example.ai in 5.2s
...
2026-09-02 21:59:42 | INFO  | pipeline | Batch complete: processed=15 drafted=8 skipped=5 failed=2

Batch complete
--------------
Candidates discovered: 41
Processed:             15
Qualified:             9
Drafted:               8
Below threshold:       4
No contact found:      3
Skipped:               2
Failed:                1
Duration:              94.3s

Failures:
- anotheragency.com: ConnectTimeout
- broken-ai.dev: HTTP 403
```

If `SERPER_API_KEY` is missing, the `run` command fails immediately with a clear message instead of entering the pipeline.

### Debug mode

To see DEBUG-level detail (every page fetched, HTTP status codes, response lengths, JSON recovery, DB inserts/updates) for a single run without editing `.env`:

```bash
docker compose run --rm nuntago-cli run --limit 1 --verbose
```

Alternatively, set `LOG_LEVEL=DEBUG` in `.env` or pass it explicitly to the container:

```bash
docker compose run --rm -e LOG_LEVEL=DEBUG outreach run --limit 1
```

### File logging

Set `LOG_FILE` in `.env` to mirror logs to a rotating file (5 MB per file, 3 backups):

```env
LOG_FILE=/data/nuntago.log
```

When enabled inside Docker, the log file persists on the host at:

```text
./data/nuntago.log
```

If `LOG_FILE` is blank, only console output is produced.

### Log levels

| Level    | Usage                                                        |
|----------|--------------------------------------------------------------|
| DEBUG    | Per-page fetches, HTTP statuses, response lengths, DB writes |
| INFO     | Stage progress, scores, contacts, durations, batch summary   |
| WARNING  | Timeouts, HTTP 403/429, empty content, recoverable skips     |
| ERROR    | Failed agencies, API failures, with traceback                |

Logs never print API keys, OAuth tokens, or full scraped website text.

## 5. Review leads

```bash
docker compose run --rm nuntago-cli list --status drafted --min-score 70
docker compose run --rm nuntago-cli show 12
```

Approve or reject:

```bash
docker compose run --rm nuntago-cli approve 12
docker compose run --rm nuntago-cli reject 13
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
docker compose run --rm nuntago-cli gmail-drafts --limit 10
```

The Compose service uses host networking because Google desktop OAuth starts a temporary localhost callback server. On Linux, copy the authorization URL printed in the terminal into your normal browser if the container cannot open it automatically.

The resulting token is persisted at:

```text
./secrets/token.json
```

Approved leads with public email addresses become Gmail drafts. Review and send them manually in Gmail.

After sending a draft:

```bash
docker compose run --rm nuntago-cli mark-sent 12
```

Check follow-ups:

```bash
docker compose run --rm nuntago-cli due-followups
docker compose run --rm nuntago-cli followup-draft 12
```

## Export

Persist exports in `data/`:

```bash
docker compose run --rm nuntago-cli export --path /data/leads.csv
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
0 8 * * 1-5 cd /path/to/nuntago && docker compose run --rm nuntago-cli run --limit 15
```

Do not schedule Gmail draft creation or sending unless you intentionally want that behavior.

# Local Python development

Docker is recommended, but local execution still works:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

python -m app init-db
python -m app run --limit 15
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


## CI/CD

GitHub Actions validates Nuntago on pull requests and automatically deploys successful `master` commits to Hetzner after the one-time production bootstrap.

See [`docs/hetzner-cicd.md`](docs/hetzner-cicd.md) for server layout, required Actions secrets, rollback behavior, and the private-network boundary.

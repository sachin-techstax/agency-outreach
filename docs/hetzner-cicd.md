# Nuntago CI/CD — GitHub Actions to Hetzner

Nuntago uses one GitHub Actions workflow for validation and deployment.

## Behavior

### Pull requests

Every pull request:

1. builds the CLI Docker image;
2. builds the React/FastAPI web Docker image;
3. runs the full pytest suite;
4. starts Nuntago in read-only demo mode;
5. verifies `/api/health` returns Nuntago with `demo_mode=true`;
6. verifies a mutation endpoint is blocked with HTTP 403.

No deployment occurs for pull requests.

### Merges / pushes to `master`

A successful `master` validation unlocks the deployment job.

The deploy job:

1. packages the exact Git commit being built;
2. uploads that exact archive over SSH;
3. expands it to `/srv/agency-outreach/releases/<commit-sha>`;
4. links shared production state:
   - `/srv/agency-outreach/shared/.env`
   - `/srv/agency-outreach/shared/data`
   - `/srv/agency-outreach/shared/secrets`
5. builds SHA-tagged Docker images;
6. runs `nuntago doctor --strict`;
7. starts the private Nuntago web runtime;
8. checks `http://127.0.0.1:8080/api/health`;
9. switches `/srv/agency-outreach/current` only after the new release is healthy;
10. rolls back to the previous release if the new runtime fails its health check.

The Hetzner server does **not** need GitHub repository credentials.

## One-time server bootstrap

The production host must already have:

- Docker Engine
- Docker Compose plugin
- curl
- an SSH user permitted to run Docker

Create the runtime directories:

```bash
sudo mkdir -p /srv/agency-outreach/bootstrap
```

Copy `infra/bootstrap-hetzner.sh` to the server once, then run:

```bash
chmod +x bootstrap-hetzner.sh
./bootstrap-hetzner.sh
```

It creates:

```text
/srv/agency-outreach/
├── current -> releases/<active-sha>
├── releases/
└── shared/
    ├── .env
    ├── data/
    └── secrets/
```

Edit the production environment:

```bash
nano /srv/agency-outreach/shared/.env
chmod 600 /srv/agency-outreach/shared/.env
```

At minimum, real discovery requires:

```env
SERPER_API_KEY=...
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.6-luna
YOUR_NAME="Sachin Rajan"
MIN_SCORE=70
DISCOVERY_LIMIT=15
FOLLOWUP_DAYS=4
NUNTAGO_DEMO_MODE=false
```

OpenAI is optional for the existing deterministic fallback, but the production `doctor --strict` requires Serper because discovery cannot operate without it.

> Do not validate the file by sourcing it directly in Bash. Values may legitimately contain spaces. Docker Compose reads the env file directly; use `nuntago doctor` or a non-evaluating parser/check instead.

When Gmail drafting is enabled, copy the OAuth files to:

```text
/srv/agency-outreach/shared/secrets/client_secret.json
/srv/agency-outreach/shared/secrets/token.json
```

## GitHub Actions secrets

In **GitHub → repository Settings → Secrets and variables → Actions**, create:

| Secret | Required | Value |
| --- | --- | --- |
| `HETZNER_HOST` | yes | server hostname or IP |
| `HETZNER_USER` | yes | SSH deployment user |
| `HETZNER_SSH_PRIVATE_KEY` | yes | private key matching the server user's authorized key |
| `HETZNER_KNOWN_HOSTS` | yes | pinned SSH known-hosts entry for the server |
| `HETZNER_SSH_PORT` | no | SSH port; defaults to 22 |

Generate the known-hosts value from a trusted machine:

```bash
ssh-keyscan -H YOUR_SERVER_HOST
```

Verify the fingerprint out of band before saving it as `HETZNER_KNOWN_HOSTS`.

The workflow uses strict host-key checking and will not silently trust a new host key.

## First deployment

After the bootstrap and GitHub secrets are configured, merge the CI/CD pull request to `master`.

The resulting `master` workflow will validate the exact merge SHA and automatically deploy it if validation passes.

On the server:

```bash
readlink -f /srv/agency-outreach/current
cat /srv/agency-outreach/current/.release-sha
docker compose -p pactsignal -f /srv/agency-outreach/current/docker-compose.yml ps
curl -fsS http://127.0.0.1:8080/api/health
```

## Network boundary

The Compose service publishes Nuntago only on:

```text
127.0.0.1:8080
```

Private mode currently has no application authentication. Do not change that binding to `0.0.0.0` merely to make the UI reachable.

For remote browser access, put an authenticated reverse proxy or private network such as Tailscale in front of Nuntago.

## Rollback

Automated rollback occurs only when the newly started web runtime fails its health check.

The previous release directory and SHA-tagged Docker image remain available. Manual rollback is:

```bash
cd /srv/agency-outreach/releases/<previous-sha>
export DEPLOY_SHA=<first-12-chars-of-previous-sha>
export LOCAL_UID="$(id -u)"
export LOCAL_GID="$(id -g)"
export NUNTAGO_DEMO_MODE=false

docker compose -p pactsignal up -d pactsignal-web
ln -sfn /srv/agency-outreach/releases/<previous-sha> /srv/agency-outreach/current
```

## What CD does not do

This milestone deploys application code and the private web operator console.

It does **not** yet:

- expose Nuntago publicly;
- configure DNS/TLS;
- create an application login;
- schedule recurring discovery/outreach runs;
- automatically send email.

Those remain separate milestones.

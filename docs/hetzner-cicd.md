# Nuntago CI/CD — GitHub Actions to Hetzner

Nuntago is deployed independently from Gradewise. The runtime is localhost-bound on the host and is not attached to the Gradewise Caddy network.

## Runtime identity

- repository: `sachin-techstax/nuntago`
- Docker project: `nuntago`
- services: `nuntago-cli`, `nuntago-web`
- images: `nuntago-cli:<sha>`, `nuntago-web:<sha>`
- server root: `/srv/nuntago`
- SQLite: `/srv/nuntago/shared/data/nuntago.db`
- web listener: `127.0.0.1:8080`
- application config: `NUNTAGO_*`

There is no external Nuntago proxy network in this repository. A future umbrella-domain proxy should be introduced as a separate deployment boundary.

## Pull requests

Every pull request builds both images, runs the complete Python suite and starts Nuntago in read-only demo mode. The smoke test verifies:

- `/api/health` reports `product=Nuntago`;
- demo mode is active;
- persistent mutations are blocked.

Pull requests never deploy.

## One-time identity migration

The live installation predates the Nuntago name. Before merging the first full-identity release, run `infra/migrate-production-identity.sh` on the Hetzner host.

The migration:

1. creates `/srv/nuntago`;
2. copies shared secrets and non-database artifacts;
3. converts the old application environment keys to `NUNTAGO_*`;
4. copies the live SQLite database using Python's SQLite backup API;
5. writes it as `/srv/nuntago/shared/data/nuntago.db`;
6. leaves the old runtime untouched.

It deliberately does **not** delete the old server directory.

After staging, verify:

```bash
test -f /srv/nuntago/shared/.env
test -f /srv/nuntago/shared/data/nuntago.db
grep '^NUNTAGO_' /srv/nuntago/shared/.env
```

The first Nuntago deploy builds and validates the new runtime before pausing the old web container for the localhost port handoff. If Nuntago fails health checks, the legacy web runtime is restarted.

## Normal deployment

A successful push to `master`:

1. packages the exact commit;
2. uploads it to the server;
3. expands it under `/srv/nuntago/releases/<sha>`;
4. links `shared/.env`, `shared/data` and `shared/secrets`;
5. builds `nuntago-cli` and `nuntago-web`;
6. runs `nuntago doctor --strict`;
7. starts `nuntago-web`;
8. verifies `http://127.0.0.1:8080/api/health`;
9. updates `/srv/nuntago/current` only after health succeeds.

## Production environment

At minimum:

```env
SERPER_API_KEY=...
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.6-luna
YOUR_NAME="Sachin Rajan"
MIN_SCORE=70
DISCOVERY_LIMIT=15
FOLLOWUP_DAYS=4
NUNTAGO_DEMO_MODE=false
NUNTAGO_AUTH_ENABLED=true
NUNTAGO_API_TOKEN=<high-entropy-token>
```

Generate the operator token with:

```bash
openssl rand -hex 32
```

## Verification

```bash
readlink -f /srv/nuntago/current
cat /srv/nuntago/current/.release-sha
docker compose -p nuntago -f /srv/nuntago/current/docker-compose.yml ps
curl -fsS http://127.0.0.1:8080/api/health
```

## Gradewise separation

Gradewise must not route a Nuntago hostname and its Caddy container must not join any Nuntago/PactSignal network. The Gradewise detachment is maintained in its own repository and PR.

Until an umbrella domain is chosen and configured, Nuntago remains private on `127.0.0.1:8080`.

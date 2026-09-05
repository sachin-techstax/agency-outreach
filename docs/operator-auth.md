# Nuntago operator API token

`NUNTAGO_*` is the preferred configuration contract. Existing `PACTSIGNAL_AUTH_ENABLED` and `PACTSIGNAL_API_TOKEN` values remain accepted during the compatibility window.

Nuntago private mode uses a single static bearer token for the operator API.

This is intentionally simpler than user accounts or JWT session issuance because
Nuntago currently has one operator.

```text
browser UI
  -> operator enters token once per browser tab/session
  -> token stored in sessionStorage
  -> frontend sends Authorization: Bearer <token>
  -> FastAPI compares it to NUNTAGO_API_TOKEN
  -> protected /api/* request proceeds
```

## Production configuration

```dotenv
NUNTAGO_AUTH_ENABLED=true
NUNTAGO_API_TOKEN=<high-entropy-secret>
```

Generate the token on the server:

```bash
openssl rand -hex 32
```

Do not commit the token and do not compile it into the Vite frontend. A token
embedded in frontend JavaScript is public to anyone who can load the site and
would provide no meaningful API protection.

## API contract

All `/api/*` endpoints require:

```http
Authorization: Bearer <NUNTAGO_API_TOKEN>
```

except:

```text
GET /api/health
```

Health remains public so deployment and reverse-proxy health checks can work.

## Browser behavior

The operator enters the token in the Nuntago unlock screen. The frontend
stores it in `sessionStorage`, so it survives page reloads in the same tab but
is not persisted as a long-lived local token. Signing out removes it.

## Deployment safety

`doctor --strict` treats API-token authentication as required in private mode.
A production deploy therefore fails readiness if authentication is disabled or
the configured token is shorter than 32 characters.

Keep Caddy Basic Auth enabled during rollout. Once the token-authenticated
Nuntago release is deployed and verified, remove Caddy Basic Auth so the
normal Nuntago unlock screen is the only access prompt.

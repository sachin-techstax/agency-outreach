# PactSignal operator authentication

PactSignal uses application-level authentication for the private operator UI.

## Model

The browser never receives the JWT signing secret and does not store the JWT in
`localStorage` or `sessionStorage`.

```text
browser
  -> POST /api/auth/login (username + password)
  -> FastAPI verifies bcrypt password hash
  -> FastAPI issues short-lived HS256 JWT
  -> JWT stored in HttpOnly + Secure + SameSite=Strict cookie
  -> all operator /api/* requests require that cookie
```

`/api/health` and `/api/auth/login` remain public. The SPA shell and static
assets are also public so the login screen can render. Operator data and
actions are protected by the backend.

## Required private-production settings

```dotenv
PACTSIGNAL_AUTH_ENABLED=true
PACTSIGNAL_ADMIN_USERNAME=sachin
PACTSIGNAL_ADMIN_PASSWORD_HASH='<bcrypt hash>'
PACTSIGNAL_JWT_SECRET=<at-least-32-character-random-secret>
PACTSIGNAL_JWT_TTL_MINUTES=480
PACTSIGNAL_COOKIE_SECURE=true
```

Generate a JWT secret on the host without committing it:

```bash
openssl rand -hex 32
```

The password hash may be generated with bcrypt. Only the hash belongs in the
runtime environment. Never store the plaintext password in the repository.

## Security properties

- JWT algorithm is fixed to HS256 during verification.
- JWT issuer and audience are both validated.
- Tokens require `sub`, `iss`, `aud`, `iat`, `exp`, and `jti`.
- Session cookies are HttpOnly, Secure, SameSite=Strict, and scoped to `/`.
- Login failures are rate-limited per client IP + username.
- Invalid or expired JWTs return HTTP 401.
- Demo mode may run without authentication for local portfolio use.
- `doctor --strict` treats application authentication as required in private
  mode, preventing a production deploy from silently running unprotected.

## Caddy transition

During rollout, existing Caddy Basic Auth should remain enabled until the
application-authenticated PactSignal release is deployed and verified.

After verification, Caddy Basic Auth can be removed so
`https://pactsignal.gradewise.quest` renders the PactSignal login screen
instead of a browser-native Basic Auth prompt. Caddy continues to provide TLS
and reverse proxying.

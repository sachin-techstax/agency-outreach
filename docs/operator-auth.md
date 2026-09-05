# Nuntago public showcase and operator authentication

Nuntago exposes two browser surfaces from the same localhost-bound runtime while
keeping their API boundaries separate.

```text
nuntago-demo.ergorum.com
  -> public browser showcase
  -> /api/public/*
  -> fictional demo data only
  -> no mutation routes

nuntago.ergorum.com
  -> Cloudflare Access
  -> /api/*
  -> live Nuntago database and operator actions
```

The public host is intentionally useful as portfolio proof: visitors can explore
the real Nuntago workflow and interface, but the API only serves fictional
showcase records. Public requests never read the live SQLite database.

## Cloudflare Access

The operator console uses the Access application token injected by Cloudflare in
the `Cf-Access-Jwt-Assertion` header. Nuntago validates the JWT signature,
issuer and application audience against Cloudflare's rotating JWK set before
allowing the operator API request.

Production configuration:

```dotenv
NUNTAGO_AUTH_ENABLED=true

NUNTAGO_PUBLIC_HOST=nuntago-demo.ergorum.com
NUNTAGO_OPERATOR_HOST=nuntago.ergorum.com

NUNTAGO_ACCESS_TEAM_DOMAIN=https://<team>.cloudflareaccess.com
NUNTAGO_ACCESS_AUD=<application-audience-tag>
NUNTAGO_OPERATOR_EMAIL=<operator-email>
```

`NUNTAGO_OPERATOR_EMAIL` is an additional origin-side allowlist. Cloudflare
Access should also have an Allow policy restricted to the operator identity.

The Access signing keys are retrieved from:

```text
<NUNTAGO_ACCESS_TEAM_DOMAIN>/cdn-cgi/access/certs
```

PyJWT caches the remote JWK client. Access key rotation therefore does not
require hard-coding certificates in the repository.

## Bearer-token fallback

`NUNTAGO_API_TOKEN` remains supported for local tooling and non-browser
automation. It is no longer part of the browser UI.

```dotenv
NUNTAGO_API_TOKEN=<high-entropy-secret>
```

When `NUNTAGO_AUTH_ENABLED=true`, at least one operator authentication method
must be configured:

- Cloudflare Access team domain + application AUD, or
- a bearer token containing at least 32 characters.

The production browser console should use Cloudflare Access. The bearer token is
a fallback, not a user-facing login mechanism.

## Public API contract

These routes are intentionally unauthenticated and demo-only:

```text
GET /api/site
GET /api/public/meta
GET /api/public/dashboard
GET /api/public/leads
GET /api/public/leads/{id}
GET /api/public/runs
GET /api/public/runs/{id}
GET /api/public/followups
GET /api/health
```

There are no public mutation endpoints. A frontend bug cannot turn the showcase
into a write-capable client because the public API surface itself has no POST
routes and never touches the live database.

## Operator API contract

All normal `/api/*` routes except health/site/public showcase endpoints are
operator routes. In production they require a valid Cloudflare Access JWT.
Bearer-token authentication remains accepted as the tooling fallback.

## Deployment safety

`doctor --strict` requires operator authentication in live mode. A production
deploy fails readiness if authentication is disabled or neither Cloudflare
Access nor a valid bearer fallback is configured.

The runtime remains bound to `127.0.0.1:8080`. Both public and operator
hostnames should reach it only through Cloudflare Tunnel.

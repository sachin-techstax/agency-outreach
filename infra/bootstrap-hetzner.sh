#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="${APP_ROOT:-/srv/agency-outreach}"
TARGET_USER="${SUDO_USER:-$USER}"

command -v docker >/dev/null 2>&1 || {
  echo "Docker is not installed. Install Docker Engine and the Compose plugin first." >&2
  exit 1
}
docker compose version >/dev/null 2>&1 || {
  echo "Docker Compose plugin is unavailable." >&2
  exit 1
}
command -v curl >/dev/null 2>&1 || {
  echo "curl is required." >&2
  exit 1
}

docker network inspect pactsignal_proxy >/dev/null 2>&1 || docker network create pactsignal_proxy >/dev/null

sudo mkdir -p   "$APP_ROOT/releases"   "$APP_ROOT/shared/data"   "$APP_ROOT/shared/secrets"

sudo chown -R "$TARGET_USER":"$TARGET_USER" "$APP_ROOT"

if [ ! -f "$APP_ROOT/shared/.env" ]; then
  cat > "$APP_ROOT/shared/.env" <<'ENV'
SERPER_API_KEY=
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.6-luna
YOUR_NAME="Sachin Rajan"
PORTFOLIO_URL=
CALENDLY_URL=
MIN_SCORE=70
DISCOVERY_LIMIT=15
FOLLOWUP_DAYS=4
LOG_LEVEL=INFO
LOG_FILE=
PACTSIGNAL_DEMO_MODE=false
ENV
  chmod 600 "$APP_ROOT/shared/.env"
  echo "Created $APP_ROOT/shared/.env"
fi

cat <<EOF

PactSignal server directories are ready.

Next:
1. Edit:
   $APP_ROOT/shared/.env

2. Put Gmail OAuth files here when Gmail drafting is enabled:
   $APP_ROOT/shared/secrets/client_secret.json
   $APP_ROOT/shared/secrets/token.json

3. Ensure the deployment SSH user can run Docker without sudo:
   docker ps
   docker compose version

4. Configure the GitHub Actions secrets documented in:
   docs/hetzner-cicd.md
EOF

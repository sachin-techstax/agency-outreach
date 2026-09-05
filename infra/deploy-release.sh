#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="${APP_ROOT:-/srv/nuntago}"
LEGACY_ROOT="${LEGACY_ROOT:-/srv/agency-outreach}"
RELEASE_SHA="${RELEASE_SHA:?RELEASE_SHA is required}"
RELEASE_DIR="$APP_ROOT/releases/$RELEASE_SHA"
SHARED_DIR="$APP_ROOT/shared"
CURRENT_LINK="$APP_ROOT/current"
PROJECT_NAME="nuntago"
HEALTH_URL="http://127.0.0.1:8080/api/health"
LEGACY_PROJECT_NAME="pactsignal"
LEGACY_WEB_SERVICE="pactsignal-web"
LEGACY_COMPOSE="$LEGACY_ROOT/current/docker-compose.yml"

fail() {
  echo "Nuntago deploy failed: $*" >&2
  exit 1
}

command -v docker >/dev/null 2>&1 || fail "docker is not installed"
docker compose version >/dev/null 2>&1 || fail "docker compose plugin is unavailable"
command -v curl >/dev/null 2>&1 || fail "curl is not installed"

test -d "$RELEASE_DIR" || fail "release directory missing: $RELEASE_DIR"
test -f "$SHARED_DIR/.env" || fail "shared runtime env missing: $SHARED_DIR/.env; run infra/migrate-production-identity.sh before first cutover"
mkdir -p "$SHARED_DIR/data" "$SHARED_DIR/secrets"

ln -sfn "$SHARED_DIR/.env" "$RELEASE_DIR/.env"
ln -sfn "$SHARED_DIR/data" "$RELEASE_DIR/data"
ln -sfn "$SHARED_DIR/secrets" "$RELEASE_DIR/secrets"
printf '%s\n' "$RELEASE_SHA" > "$RELEASE_DIR/.release-sha"

previous_dir=""
previous_sha=""
if [ -L "$CURRENT_LINK" ]; then
  previous_dir="$(readlink -f "$CURRENT_LINK" || true)"
  if [ -n "$previous_dir" ] && [ -f "$previous_dir/.release-sha" ]; then
    previous_sha="$(cat "$previous_dir/.release-sha")"
  fi
fi

legacy_was_running=false
if [ -f "$LEGACY_COMPOSE" ]; then
  if [ -n "$(docker compose -p "$LEGACY_PROJECT_NAME" -f "$LEGACY_COMPOSE" ps --status running -q "$LEGACY_WEB_SERVICE" 2>/dev/null || true)" ]; then
    legacy_was_running=true
  fi
fi

export DEPLOY_SHA="${RELEASE_SHA:0:12}"
export LOCAL_UID="$(id -u)"
export LOCAL_GID="$(id -g)"
export NUNTAGO_DEMO_MODE=false

cd "$RELEASE_DIR"

echo "==> Building Nuntago release $RELEASE_SHA"
docker compose -p "$PROJECT_NAME" build nuntago-cli nuntago-web

echo "==> Checking production prerequisites"
docker compose -p "$PROJECT_NAME" run --rm nuntago-cli doctor --strict

# The legacy runtime owns 127.0.0.1:8080. Stop only its web service at the
# last responsible moment, after the new images and readiness checks pass.
if [ "$legacy_was_running" = "true" ]; then
  echo "==> Pausing legacy web runtime for port handoff"
  docker compose -p "$LEGACY_PROJECT_NAME" -f "$LEGACY_COMPOSE" stop "$LEGACY_WEB_SERVICE"
fi

echo "==> Starting Nuntago web runtime"
docker compose -p "$PROJECT_NAME" up -d nuntago-web

healthy=false
body=""
for attempt in $(seq 1 30); do
  if body="$(curl -fsS "$HEALTH_URL" 2>/dev/null)"; then
    if echo "$body" | grep -Eq '"product"[[:space:]]*:[[:space:]]*"Nuntago"' &&
       echo "$body" | grep -Eq '"demo_mode"[[:space:]]*:[[:space:]]*false'; then
      healthy=true
      break
    fi
  fi
  sleep 2
done

if [ "$healthy" != "true" ]; then
  echo "New release failed health check. Response: $body" >&2
  docker compose -p "$PROJECT_NAME" down --remove-orphans || true

  if [ -n "$previous_dir" ] && [ -n "$previous_sha" ] && [ -d "$previous_dir" ]; then
    echo "==> Rolling back to previous Nuntago release $previous_sha"
    cd "$previous_dir"
    export DEPLOY_SHA="${previous_sha:0:12}"
    docker compose -p "$PROJECT_NAME" up -d nuntago-web
  elif [ "$legacy_was_running" = "true" ]; then
    echo "==> Restoring legacy runtime"
    docker compose -p "$LEGACY_PROJECT_NAME" -f "$LEGACY_COMPOSE" up -d "$LEGACY_WEB_SERVICE"
  fi

  fail "new release did not become healthy"
fi

ln -sfn "$RELEASE_DIR" "$CURRENT_LINK"

if [ -f "$LEGACY_COMPOSE" ]; then
  echo "==> Retiring legacy Docker project"
  docker compose -p "$LEGACY_PROJECT_NAME" -f "$LEGACY_COMPOSE" down --remove-orphans || true
  docker network rm pactsignal_proxy >/dev/null 2>&1 || true
fi

echo "==> Nuntago release active: $RELEASE_SHA"
echo "$body"

mapfile -t stale < <(find "$APP_ROOT/releases" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' \
  | sort -nr \
  | awk 'NR > 5 {print $2}')
for path in "${stale[@]:-}"; do
  [ -n "$path" ] || continue
  [ "$path" = "$RELEASE_DIR" ] && continue
  [ -n "$previous_dir" ] && [ "$path" = "$previous_dir" ] && continue
  rm -rf "$path"
done

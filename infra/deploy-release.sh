#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="${APP_ROOT:-/srv/agency-outreach}"
RELEASE_SHA="${RELEASE_SHA:?RELEASE_SHA is required}"
RELEASE_DIR="$APP_ROOT/releases/$RELEASE_SHA"
SHARED_DIR="$APP_ROOT/shared"
CURRENT_LINK="$APP_ROOT/current"
PROJECT_NAME="pactsignal"
HEALTH_URL="http://127.0.0.1:8080/api/health"

fail() {
  echo "PactSignal deploy failed: $*" >&2
  exit 1
}

command -v docker >/dev/null 2>&1 || fail "docker is not installed"
docker compose version >/dev/null 2>&1 || fail "docker compose plugin is unavailable"
command -v curl >/dev/null 2>&1 || fail "curl is not installed"
docker network inspect pactsignal_proxy >/dev/null 2>&1 || docker network create pactsignal_proxy >/dev/null

test -d "$RELEASE_DIR" || fail "release directory missing: $RELEASE_DIR"
test -f "$SHARED_DIR/.env" || fail "shared runtime env missing: $SHARED_DIR/.env"
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

export DEPLOY_SHA="${RELEASE_SHA:0:12}"
export LOCAL_UID="$(id -u)"
export LOCAL_GID="$(id -g)"
export PACTSIGNAL_DEMO_MODE=false

cd "$RELEASE_DIR"

echo "==> Building PactSignal release $RELEASE_SHA"
docker compose -p "$PROJECT_NAME" build outreach pactsignal-web

echo "==> Checking production prerequisites"
docker compose -p "$PROJECT_NAME" run --rm outreach doctor --strict

echo "==> Starting PactSignal web runtime"
docker compose -p "$PROJECT_NAME" up -d pactsignal-web

healthy=false
body=""
for attempt in $(seq 1 30); do
  if body="$(curl -fsS "$HEALTH_URL" 2>/dev/null)"; then
    if echo "$body" | grep -Eq '"product"[[:space:]]*:[[:space:]]*"PactSignal"' &&
       echo "$body" | grep -Eq '"demo_mode"[[:space:]]*:[[:space:]]*false'; then
      healthy=true
      break
    fi
  fi
  sleep 2
done

if [ "$healthy" != "true" ]; then
  echo "New release failed health check. Response: $body" >&2

  if [ -n "$previous_dir" ] && [ -n "$previous_sha" ] && [ -d "$previous_dir" ]; then
    echo "==> Rolling back to $previous_sha"
    cd "$previous_dir"
    export DEPLOY_SHA="${previous_sha:0:12}"
    docker compose -p "$PROJECT_NAME" up -d pactsignal-web

    rollback_ok=false
    for attempt in $(seq 1 20); do
      if body="$(curl -fsS "$HEALTH_URL" 2>/dev/null)" &&
         echo "$body" | grep -Eq '"product"[[:space:]]*:[[:space:]]*"PactSignal"'; then
        rollback_ok=true
        break
      fi
      sleep 2
    done

    if [ "$rollback_ok" != "true" ]; then
      fail "new release failed and rollback health check also failed"
    fi
  fi

  fail "new release did not become healthy"
fi

ln -sfn "$RELEASE_DIR" "$CURRENT_LINK"

echo "==> PactSignal release active: $RELEASE_SHA"
echo "$body"

# Keep the newest five source releases. Container images are intentionally not
# pruned here so the most recent previous image remains available for rollback.
mapfile -t stale < <(find "$APP_ROOT/releases" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' \
  | sort -nr \
  | awk 'NR > 5 {print $2}')
for path in "${stale[@]:-}"; do
  [ -n "$path" ] || continue
  [ "$path" = "$RELEASE_DIR" ] && continue
  [ -n "$previous_dir" ] && [ "$path" = "$previous_dir" ] && continue
  rm -rf "$path"
done

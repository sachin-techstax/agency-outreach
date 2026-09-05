#!/usr/bin/env bash
set -Eeuo pipefail

LEGACY_ROOT="${LEGACY_ROOT:-/srv/agency-outreach}"
APP_ROOT="${APP_ROOT:-/srv/nuntago}"
TARGET_USER="${SUDO_USER:-$USER}"

fail() {
  echo "Nuntago identity migration failed: $*" >&2
  exit 1
}

if [ -L "$APP_ROOT/current" ]; then
  fail "$APP_ROOT already has an active release; refusing to overwrite staged/live Nuntago state"
fi

command -v python3 >/dev/null 2>&1 || fail "python3 is required"
test -d "$LEGACY_ROOT/shared" || fail "legacy shared state not found: $LEGACY_ROOT/shared"
test -f "$LEGACY_ROOT/shared/.env" || fail "legacy environment not found: $LEGACY_ROOT/shared/.env"

sudo mkdir -p "$APP_ROOT/releases" "$APP_ROOT/shared/data" "$APP_ROOT/shared/secrets"
sudo chown -R "$TARGET_USER":"$TARGET_USER" "$APP_ROOT"

# Copy non-database shared artifacts first. The live SQLite database is copied
# separately with sqlite3.Connection.backup() for a transaction-consistent snapshot.
if [ -d "$LEGACY_ROOT/shared/data" ]; then
  cp -a "$LEGACY_ROOT/shared/data/." "$APP_ROOT/shared/data/"
fi
rm -f "$APP_ROOT/shared/data/agency_outreach.db"       "$APP_ROOT/shared/data/agency_outreach.db-wal"       "$APP_ROOT/shared/data/agency_outreach.db-shm"

if [ -d "$LEGACY_ROOT/shared/secrets" ]; then
  cp -a "$LEGACY_ROOT/shared/secrets/." "$APP_ROOT/shared/secrets/"
fi

python3 - "$LEGACY_ROOT/shared/.env" "$APP_ROOT/shared/.env" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1])
target = Path(sys.argv[2])
mapping = {
    "PACTSIGNAL_DEMO_MODE": "NUNTAGO_DEMO_MODE",
    "PACTSIGNAL_AUTH_ENABLED": "NUNTAGO_AUTH_ENABLED",
    "PACTSIGNAL_API_TOKEN": "NUNTAGO_API_TOKEN",
}
out = []
seen = set()
for raw in source.read_text(encoding="utf-8").splitlines():
    if "=" not in raw or raw.lstrip().startswith("#"):
        out.append(raw)
        continue
    key, value = raw.split("=", 1)
    new_key = mapping.get(key, key)
    if new_key == "DB_PATH":
        if value in {"agency_outreach.db", "/data/agency_outreach.db"}:
            value = value.replace("agency_outreach.db", "nuntago.db")
    if new_key == "LOG_FILE" and value:
        value = value.replace("agency-outreach.log", "nuntago.log")
        value = value.replace("agency_outreach.log", "nuntago.log")
    if new_key in seen:
        continue
    seen.add(new_key)
    out.append(f"{new_key}={value}")

defaults = {
    "NUNTAGO_DEMO_MODE": "false",
    "NUNTAGO_AUTH_ENABLED": "true",
    "NUNTAGO_API_TOKEN": "",
}
for key, value in defaults.items():
    if key not in seen:
        out.append(f"{key}={value}")

target.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
chmod 600 "$APP_ROOT/shared/.env"

legacy_db="$LEGACY_ROOT/shared/data/agency_outreach.db"
new_db="$APP_ROOT/shared/data/nuntago.db"
if [ -f "$legacy_db" ]; then
  rm -f "$new_db" "$new_db-wal" "$new_db-shm"
  python3 - "$legacy_db" "$new_db" <<'PY'
import sqlite3
import sys

source = sqlite3.connect(sys.argv[1])
target = sqlite3.connect(sys.argv[2])
try:
    source.backup(target)
finally:
    target.close()
    source.close()
PY
fi

echo "Nuntago shared state staged at $APP_ROOT."
echo "The legacy runtime remains untouched and can keep serving until cutover."
echo "Do not delete $LEGACY_ROOT until the Nuntago production deploy is verified."

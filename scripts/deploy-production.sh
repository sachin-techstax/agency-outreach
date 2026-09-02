#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="/srv/agency-outreach"
DEPLOY_SHA="${1:-}"

if [[ ! "${DEPLOY_SHA}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "ERROR: DEPLOY_SHA must be a full 40-character lowercase Git SHA." >&2
  exit 2
fi

if [[ ! -d "${APP_DIR}/.git" ]]; then
  echo "ERROR: ${APP_DIR} is not a Git checkout." >&2
  exit 3
fi

cd "${APP_DIR}"

if [[ ! -f .env ]]; then
  echo "ERROR: production .env is missing from ${APP_DIR}." >&2
  exit 4
fi

if [[ ! -d data || ! -d secrets ]]; then
  echo "ERROR: production data/ and secrets/ directories must already exist." >&2
  exit 5
fi

PREVIOUS_SHA="$(git rev-parse HEAD)"
CHECKOUT_CHANGED=0

rollback() {
  local rc=$?
  trap - ERR
  set +e

  if [[ "${CHECKOUT_CHANGED}" -eq 1 ]]; then
    echo "Deployment failed for ${DEPLOY_SHA}; rolling back to ${PREVIOUS_SHA}."

    git reset --hard "${PREVIOUS_SHA}"
    local reset_rc=$?

    if [[ "${reset_rc}" -eq 0 ]]       && docker compose build       && docker compose run --rm -T outreach --help >/dev/null; then
      printf '%s\n' "${PREVIOUS_SHA}" > data/deployed_sha
      echo "Rollback succeeded. Restored ${PREVIOUS_SHA}."
    else
      echo "ERROR: rollback failed; manual intervention is required." >&2
      exit 70
    fi
  fi

  exit "${rc}"
}

trap rollback ERR

echo "Preparing exact-SHA deployment."
echo "Previous SHA: ${PREVIOUS_SHA}"
echo "Deploy SHA:   ${DEPLOY_SHA}"

git fetch --prune origin

# The workflow may deploy an older queued master commit after a newer push has
# already landed. Require the requested commit to exist and belong to the
# current origin/master history, while still deploying the exact triggering SHA.
git cat-file -e "${DEPLOY_SHA}^{commit}"
git merge-base --is-ancestor "${DEPLOY_SHA}" origin/master

CHECKOUT_CHANGED=1
git checkout master
git reset --hard "${DEPLOY_SHA}"

ACTUAL_SHA="$(git rev-parse HEAD)"
if [[ "${ACTUAL_SHA}" != "${DEPLOY_SHA}" ]]; then
  echo "ERROR: checkout mismatch: expected ${DEPLOY_SHA}, got ${ACTUAL_SHA}." >&2
  false
fi

docker compose build

# Smoke test only. This must not execute discovery, Gmail drafting, or sending.
docker compose run --rm -T outreach --help >/dev/null

printf '%s\n' "${DEPLOY_SHA}" > data/deployed_sha

if [[ "$(cat data/deployed_sha)" != "${DEPLOY_SHA}" ]]; then
  echo "ERROR: deployment marker does not match deployed SHA." >&2
  false
fi

trap - ERR

echo "Deployment succeeded."
echo "Deployed SHA: ${DEPLOY_SHA}"

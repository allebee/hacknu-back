#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ENV_FILE:-${REPO_DIR}/.env}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "[ERROR] .env not found at ${ENV_FILE}"
  echo "        Create it from ${REPO_DIR}/.env.example"
  exit 1
fi

# shellcheck disable=SC1090
source "${ENV_FILE}"

: "${DO_HOST:?Missing DO_HOST in ${ENV_FILE}}"
: "${DO_USER:?Missing DO_USER in ${ENV_FILE}}"
: "${SSH_KEY:?Missing SSH_KEY in ${ENV_FILE}}"

resolve_path() {
  local path="$1"
  case "${path}" in
    "~") printf '%s\n' "${HOME}" ;;
    "~/"*) printf '%s/%s\n' "${HOME}" "${path#~/}" ;;
    /*) printf '%s\n' "${path}" ;;
    *) printf '%s/%s\n' "${REPO_DIR}" "${path}" ;;
  esac
}

SSH_KEY="$(resolve_path "${SSH_KEY}")"
SSH_PUBLIC_KEY="${SSH_PUBLIC_KEY:-}"
if [[ -n "${SSH_PUBLIC_KEY}" ]]; then
  SSH_PUBLIC_KEY="$(resolve_path "${SSH_PUBLIC_KEY}")"
fi

if [[ ! -f "${SSH_KEY}" ]]; then
  echo "[ERROR] Private key not found: ${SSH_KEY}"
  echo "        SSH_KEY must point to a private key file."
  exit 1
fi

APP_PORT="${APP_PORT:-9000}"
REMOTE_DIR="${REMOTE_DIR:-/root/hacknu-back}"
REMOTE="${DO_USER}@${DO_HOST}"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

ssh_run() {
  ssh -i "${SSH_KEY}" -o StrictHostKeyChecking=no "${REMOTE}" "$@"
}

rsync_to_remote() {
  rsync -az --delete \
    -e "ssh -i ${SSH_KEY} -o StrictHostKeyChecking=no" \
    "$@"
}

check_ssh_access() {
  if ssh -i "${SSH_KEY}" -o StrictHostKeyChecking=no -o BatchMode=yes "${REMOTE}" "exit 0" >/dev/null 2>&1; then
    return 0
  fi

  echo "[ERROR] SSH authentication failed for ${REMOTE}"
  echo "        SSH_KEY is set to: ${SSH_KEY}"
  if [[ -n "${SSH_PUBLIC_KEY}" && -f "${SSH_PUBLIC_KEY}" ]]; then
    echo "        Public key reference: ${SSH_PUBLIC_KEY}"
    echo "        This public key must be present in ${DO_USER}'s ~/.ssh/authorized_keys on the server,"
    echo "        and SSH_KEY must point to the matching private key."
  else
    echo "        Install the matching public key on the server and retry."
  fi
  exit 1
}

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

# Keep deploy-only SSH settings local; sync only app env plus APP_PORT.
grep -Ev '^(DO_HOST|DO_USER|SSH_KEY|REMOTE_DIR)=' "${ENV_FILE}" > "${TMP_DIR}/.env" || true
if ! grep -q '^APP_PORT=' "${TMP_DIR}/.env"; then
  printf '\nAPP_PORT=%s\n' "${APP_PORT}" >> "${TMP_DIR}/.env"
fi

check_ssh_access

log "Preparing remote directory ${REMOTE_DIR}..."
ssh_run "mkdir -p '${REMOTE_DIR}/infra'"

log "Syncing deploy files..."
rsync_to_remote \
  "${SCRIPT_DIR}" \
  "${REMOTE}:${REMOTE_DIR}/"
rsync_to_remote \
  "${TMP_DIR}/.env" \
  "${REMOTE}:${REMOTE_DIR}/"

log "Syncing backend source..."
rsync_to_remote \
  "${REPO_DIR}/hacknu-back/" \
  "${REMOTE}:${REMOTE_DIR}/hacknu-back/"

log "Starting / updating services..."
ssh_run bash -s -- "${REMOTE_DIR}" <<'EOF'
set -euo pipefail
cd "$1"
if ! command -v docker >/dev/null 2>&1; then
  echo "[ERROR] docker not found on remote host."
  echo "        Install Docker first, then re-run deploy."
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "[ERROR] docker compose plugin not found on remote host."
  exit 1
fi
docker compose -f infra/docker-compose.yml up -d --build --remove-orphans
EOF

log "Health check..."
sleep 2
if command -v curl >/dev/null 2>&1 && curl -sf --max-time 10 "http://${DO_HOST}:${APP_PORT}/health" -o /dev/null; then
  echo "[OK] Backend -> http://${DO_HOST}:${APP_PORT}/health"
else
  echo "[WARN] Health check failed."
  echo "       Debug: ssh -i ${SSH_KEY} ${REMOTE} 'docker compose -f ${REMOTE_DIR}/infra/docker-compose.yml logs -n 200 -f backend'"
fi

log "Deploy done."

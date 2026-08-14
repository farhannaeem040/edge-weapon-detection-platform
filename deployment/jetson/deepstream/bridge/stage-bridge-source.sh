#!/usr/bin/env bash
#
# DeepStream Bridge — source staging (IP-07 T-89, FS-05 §4.6).
#
# Copies the Bridge's SOURCE ONLY (app/, run.sh, deploy-bridge.sh, requirements.lock, README.md)
# from <src-dir> to <dest-dir>. Never touches, creates, or deletes <dest-dir>/venv/ — that directory
# does not exist under <src-dir> at all (a build artifact, .gitignored, never committed), so no
# command here can reference it even by accident. Idempotent: safe to re-run on every
# install.sh/update.sh pass; a pre-existing <dest-dir>/venv/ (built separately by deploy-bridge.sh)
# survives byte-for-byte, including its own file ownership/modes, across any number of re-runs.
#
# Called by install.sh with the real deployment paths and the service account, so staged files end
# up weapon-detection-owned; callable directly (e.g. by tests) with just the two required
# arguments to exercise the same staging logic without root/chown.
#
# Usage: stage-bridge-source.sh <src-dir> <dest-dir> [service-user] [service-group]

set -Eeuo pipefail

SRC_DIR="${1:?usage: stage-bridge-source.sh <src-dir> <dest-dir> [service-user] [service-group]}"
DEST_DIR="${2:?usage: stage-bridge-source.sh <src-dir> <dest-dir> [service-user] [service-group]}"
SERVICE_USER="${3:-}"
SERVICE_GROUP="${4:-}"

log()  { printf '[stage-bridge-source] %s\n' "$*"; }
die()  { printf '[stage-bridge-source] ERROR: %s\n' "$*" >&2; exit 1; }

[[ -d "${SRC_DIR}" ]] || die "source directory not found: ${SRC_DIR}"
[[ -f "${SRC_DIR}/run.sh" ]] || die "not a Bridge source tree (missing run.sh): ${SRC_DIR}"

install -d -m 0750 "${DEST_DIR}"
install -d -m 0755 "${DEST_DIR}/app"

# --exclude='venv/' is defense-in-depth (belt-and-suspenders): the source tree never contains a
# venv/ to begin with, but the exclusion is explicit so a future edit to this rsync invocation
# (e.g. adding --delete) can never regress into touching one at the destination either.
rsync -a --exclude='venv/' --exclude='__pycache__/' --exclude='*.pyc' --exclude='.pytest_cache/' \
    --exclude='.mypy_cache/' --exclude='.ruff_cache/' \
    "${SRC_DIR}/app/" "${DEST_DIR}/app/"
install -m 0750 "${SRC_DIR}/run.sh" "${DEST_DIR}/run.sh"
install -m 0750 "${SRC_DIR}/deploy-bridge.sh" "${DEST_DIR}/deploy-bridge.sh"
install -m 0640 "${SRC_DIR}/requirements.lock" "${DEST_DIR}/requirements.lock"
install -m 0640 "${SRC_DIR}/README.md" "${DEST_DIR}/README.md"

# Ownership is applied ONLY to the files/directories this step just staged — never a recursive
# chown of the whole DEST_DIR tree, so an existing venv/'s ownership (set by deploy-bridge.sh) is
# never touched, re-asserted, or raced by this script.
if [[ -n "${SERVICE_USER}" && -n "${SERVICE_GROUP}" ]]; then
    chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${DEST_DIR}/app"
    chown "${SERVICE_USER}:${SERVICE_GROUP}" \
        "${DEST_DIR}/run.sh" "${DEST_DIR}/deploy-bridge.sh" \
        "${DEST_DIR}/requirements.lock" "${DEST_DIR}/README.md" "${DEST_DIR}"
fi
chmod 0750 "${DEST_DIR}" "${DEST_DIR}/run.sh" "${DEST_DIR}/deploy-bridge.sh"

log "staged DeepStream Bridge source to ${DEST_DIR} (venv/ untouched)"

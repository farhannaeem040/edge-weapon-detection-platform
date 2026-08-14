#!/usr/bin/env bash
#
# Weapon Detection Jetson Agent — rollback restore (IP-07 T-91 hardening, Part B).
#
# Restores agent.env, the Agent source tree, the systemd unit (only if it differs), and the
# DeepStream config from a backup created by create-rollback-backup.sh. Uses `cp -a`/`rsync -a`
# throughout, which reproduces each backed-up file's own recorded owner/group/mode — this script
# performs no chmod/chown pass of its own. Restoration was always correct; the IP-07 T-91 incident
# was caused by the BACKUP holding the wrong ownership in the first place (fixed in
# create-rollback-backup.sh) — this script needed no behavioural change beyond documenting that.
#
# Never restarts the service, never edits the systemd unit unless it actually differs from the
# backup, never prints secret file contents.
#
# Usage (production):
#     sudo restore-rollback-backup.sh <backup-dir>
#
# All destination paths are overridable (mainly for tests):
#     restore-rollback-backup.sh <backup-dir> --env-file <path> --agent-source-dir <path> \
#         --unit-file <path> --deepstream-config-dir <path>

set -Eeuo pipefail

log() { printf '[restore-rollback-backup] %s\n' "$*"; }
die() { printf '[restore-rollback-backup] ERROR: %s\n' "$*" >&2; exit 1; }

BACKUP_DIR="${1:?usage: restore-rollback-backup.sh <backup-dir> [--env-file ...] [--agent-source-dir ...] [--unit-file ...] [--deepstream-config-dir ...]}"
shift
[[ -d "${BACKUP_DIR}" ]] || die "backup directory not found: ${BACKUP_DIR}"

ENV_FILE="/etc/weapon-detection-agent/agent.env"
AGENT_SOURCE_DIR="/opt/weapon-detection/agent"
UNIT_FILE="/etc/systemd/system/weapon-detection-agent.service"
DEEPSTREAM_CONFIG_DIR="/opt/weapon-detection/config/deepstream"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --env-file) ENV_FILE="${2:?--env-file requires a value}"; shift 2 ;;
        --agent-source-dir) AGENT_SOURCE_DIR="${2:?--agent-source-dir requires a value}"; shift 2 ;;
        --unit-file) UNIT_FILE="${2:?--unit-file requires a value}"; shift 2 ;;
        --deepstream-config-dir) DEEPSTREAM_CONFIG_DIR="${2:?--deepstream-config-dir requires a value}"; shift 2 ;;
        *) die "unknown argument: $1" ;;
    esac
done

if [[ -f "${BACKUP_DIR}/agent.env" ]]; then
    cp -a "${BACKUP_DIR}/agent.env" "${ENV_FILE}"
    log "restored agent.env"
fi

if [[ -d "${BACKUP_DIR}/agent-source" ]]; then
    rsync -a --delete "${BACKUP_DIR}/agent-source/" "${AGENT_SOURCE_DIR}/"
    log "restored Agent source"
fi

if [[ -f "${BACKUP_DIR}/weapon-detection-agent.service" ]]; then
    if ! diff -q "${BACKUP_DIR}/weapon-detection-agent.service" "${UNIT_FILE}" >/dev/null 2>&1; then
        cp -a "${BACKUP_DIR}/weapon-detection-agent.service" "${UNIT_FILE}"
        command -v systemctl >/dev/null 2>&1 && [[ "${EUID}" -eq 0 ]] && systemctl daemon-reload
        log "restored systemd unit (it differed)"
    else
        log "systemd unit unchanged — no restore needed"
    fi
fi

if [[ -f "${BACKUP_DIR}/deepstream-config/deepstream-app.txt" ]]; then
    cp -a "${BACKUP_DIR}/deepstream-config/deepstream-app.txt" "${DEEPSTREAM_CONFIG_DIR}/deepstream-app.txt"
    log "restored deepstream-app.txt"
fi
if [[ -d "${BACKUP_DIR}/deepstream-config/profiles" ]]; then
    rsync -a --delete "${BACKUP_DIR}/deepstream-config/profiles/" "${DEEPSTREAM_CONFIG_DIR}/profiles/"
    log "restored DeepStream profile configs"
fi

log "restore complete. Next steps (not performed automatically):"
log "  1. python -m pip install --quiet --upgrade ${AGENT_SOURCE_DIR}  (into the Agent venv)"
log "  2. sudo systemctl start weapon-detection-agent   (this script never starts/restarts it)"

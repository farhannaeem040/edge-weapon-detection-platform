#!/usr/bin/env bash
#
# Weapon Detection Jetson Agent — rollback backup (IP-07 T-91 hardening, Part B).
#
# Creates a timestamped, mode 0700 backup of everything needed to roll back a Bridge cutover:
# agent.env, the deployed Agent source, the systemd unit, and the DeepStream config
# (deepstream-app.txt + profiles/). Every copy uses `cp -a`, which preserves the ORIGINAL
# owner/group/mode/timestamps/symlink-identity of each backed-up file — this script applies
# chown/chmod ONLY to the backup root directory itself, never recursively into its contents and
# never to the live source files it copies from.
#
# IP-07 T-91 incident: an earlier ad hoc version of this backup applied a blanket
# `chmod -R go-rwx`/`chown -R root:root` across the whole backup tree. That altered the backed-up
# COPY of deepstream-app.txt to root:root/0600; a later `cp -a` restore then carried that wrong
# ownership back onto the LIVE path, making the file unreadable by the weapon-detection service
# account and crashing the reference deepstream-app binary. The fix is structural: never touch
# anything inside the backup after `cp -a` places it there — only the backup root directory itself
# gets an explicit mode/owner.
#
# Never prints secret file CONTENTS — only paths, checksums (which do not reveal content), and
# non-secret facts (PIDs, restart counts).
#
# Usage (production):
#     sudo create-rollback-backup.sh
#
# All source paths and the output location are overridable (mainly for tests exercising this exact
# logic without touching real system paths or requiring root):
#     create-rollback-backup.sh --output-dir <dir> --env-file <path> --agent-source-dir <path> \
#         --unit-file <path> --deepstream-config-dir <path> --service-name <name>
#
# `chown root:root` on the backup directory is attempted only when running as root — a non-root
# invocation (e.g. an offline test) still produces a byte-correct backup with mode 0700, just
# without the ownership change a real production run (always via sudo) applies.

set -Eeuo pipefail

log() { printf '[create-rollback-backup] %s\n' "$*"; }
die() { printf '[create-rollback-backup] ERROR: %s\n' "$*" >&2; exit 1; }

ENV_FILE="/etc/weapon-detection-agent/agent.env"
AGENT_SOURCE_DIR="/opt/weapon-detection/agent"
UNIT_FILE="/etc/systemd/system/weapon-detection-agent.service"
DEEPSTREAM_CONFIG_DIR="/opt/weapon-detection/config/deepstream"
SERVICE_NAME="weapon-detection-agent"
OUTPUT_DIR="/root/wda-t91-backup-$(date -u +%Y%m%dT%H%M%SZ)"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --output-dir) OUTPUT_DIR="${2:?--output-dir requires a value}"; shift 2 ;;
        --env-file) ENV_FILE="${2:?--env-file requires a value}"; shift 2 ;;
        --agent-source-dir) AGENT_SOURCE_DIR="${2:?--agent-source-dir requires a value}"; shift 2 ;;
        --unit-file) UNIT_FILE="${2:?--unit-file requires a value}"; shift 2 ;;
        --deepstream-config-dir) DEEPSTREAM_CONFIG_DIR="${2:?--deepstream-config-dir requires a value}"; shift 2 ;;
        --service-name) SERVICE_NAME="${2:?--service-name requires a value}"; shift 2 ;;
        *) die "unknown argument: $1" ;;
    esac
done

# --- Backup directory: mode 0700 always; root-owned when actually running as root — applied ONLY
# to this one directory, never recursively into its contents. ------------------------------------
install -d -m 0700 "${OUTPUT_DIR}/agent-source" "${OUTPUT_DIR}/deepstream-config"
if [[ "${EUID}" -eq 0 ]]; then
    chown root:root "${OUTPUT_DIR}"
fi
chmod 0700 "${OUTPUT_DIR}"

# --- Copies: cp -a preserves each source file's own owner/group/mode/timestamps. No chmod/chown
# pass follows any of these — that is the entire point of this rewrite. ---------------------------
[[ -f "${ENV_FILE}" ]] && cp -a "${ENV_FILE}" "${OUTPUT_DIR}/agent.env"
[[ -d "${AGENT_SOURCE_DIR}" ]] && cp -a "${AGENT_SOURCE_DIR}/." "${OUTPUT_DIR}/agent-source/"
[[ -f "${UNIT_FILE}" ]] && cp -a "${UNIT_FILE}" "${OUTPUT_DIR}/weapon-detection-agent.service"
if [[ -f "${DEEPSTREAM_CONFIG_DIR}/deepstream-app.txt" ]]; then
    cp -a "${DEEPSTREAM_CONFIG_DIR}/deepstream-app.txt" "${OUTPUT_DIR}/deepstream-config/"
fi
if [[ -d "${DEEPSTREAM_CONFIG_DIR}/profiles" ]]; then
    cp -a "${DEEPSTREAM_CONFIG_DIR}/profiles" "${OUTPUT_DIR}/deepstream-config/"
fi

log "backed up agent.env, Agent source, systemd unit, and DeepStream config to ${OUTPUT_DIR}"

# --- Checksums/permissions/baseline facts: paths and hashes only, never file contents -------------
{
    [[ -f "${OUTPUT_DIR}/agent.env" ]] && sha256sum "${OUTPUT_DIR}/agent.env"
    [[ -f "${OUTPUT_DIR}/weapon-detection-agent.service" ]] \
        && sha256sum "${OUTPUT_DIR}/weapon-detection-agent.service"
    find "${OUTPUT_DIR}/agent-source" "${OUTPUT_DIR}/deepstream-config" -type f \
        -exec sha256sum {} \; 2>/dev/null
    true
} > "${OUTPUT_DIR}/CHECKSUMS.txt"

{
    [[ -f "${ENV_FILE}" ]] && stat -c '%n %a %U:%G' "${ENV_FILE}"
    [[ -f "${DEEPSTREAM_CONFIG_DIR}/deepstream-app.txt" ]] \
        && stat -c '%n %a %U:%G' "${DEEPSTREAM_CONFIG_DIR}/deepstream-app.txt"
    true
} > "${OUTPUT_DIR}/PERMISSIONS.txt"

{
    echo "UTC_timestamp: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "hostname: $(hostname 2>/dev/null || echo unknown)"
    if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
        echo "agent_service_active: active"
        echo "agent_main_pid: $(systemctl show "${SERVICE_NAME}" -p MainPID --value 2>/dev/null || echo unknown)"
        echo "nrestarts_before: $(systemctl show "${SERVICE_NAME}" -p NRestarts --value 2>/dev/null || echo unknown)"
    else
        echo "agent_service_active: unknown-or-inactive"
    fi
    ds_pid="$(pgrep -f '^/usr/bin/deepstream-app' 2>/dev/null || true)"
    echo "deepstream_child_pid: ${ds_pid:-none}"
} > "${OUTPUT_DIR}/BASELINE.txt"

chmod 0600 "${OUTPUT_DIR}"/*.txt 2>/dev/null || true

log "backup complete: ${OUTPUT_DIR}"
echo "BACKUP_DIR=${OUTPUT_DIR}"

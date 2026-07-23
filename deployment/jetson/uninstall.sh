#!/usr/bin/env bash
#
# Weapon Detection Jetson Agent — uninstaller (IP-02 T-41, §12).
#
# Stops and disables the service and removes the unit and the installed application/venv. By
# default it PRESERVES all mutable Agent data (config, database, logs, env file) so the Device
# Identity is never silently destroyed. Deleting that data requires an explicit, separate
# destructive confirmation.
#
# Usage:
#     sudo bash uninstall.sh              # remove service + app/venv; keep all data
#     sudo bash uninstall.sh --purge      # ALSO delete config/database/logs, env file, service user
#     sudo bash uninstall.sh --yes        # skip the first (non-destructive) confirmation prompt

set -Eeuo pipefail

readonly SERVICE_NAME="weapon-detection-agent"
readonly SERVICE_USER="weapon-detection"
readonly SERVICE_GROUP="weapon-detection"
readonly ROOT_DIR="/opt/weapon-detection"
readonly APP_DIR="${ROOT_DIR}/agent"
readonly VENV_DIR="${ROOT_DIR}/venv"
readonly CONFIG_DIR="${ROOT_DIR}/config"
readonly DATABASE_DIR="${ROOT_DIR}/database"
readonly LOGS_DIR="${ROOT_DIR}/logs"
readonly ENV_DIR="/etc/weapon-detection-agent"
readonly UNIT_DEST="/etc/systemd/system/${SERVICE_NAME}.service"

log() { printf '[uninstall] %s\n' "$*"; }
die() { printf '[uninstall] ERROR: %s\n' "$*" >&2; exit 1; }

PURGE=0
ASSUME_YES=0
for arg in "$@"; do
    case "${arg}" in
        --purge) PURGE=1 ;;
        --yes|-y) ASSUME_YES=1 ;;
        -h|--help) grep -E '^# ' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "unknown argument: ${arg}" ;;
    esac
done

[[ "${EUID}" -eq 0 ]] || die "must run as root (use: sudo bash uninstall.sh)"

if [[ "${ASSUME_YES}" -ne 1 ]]; then
    printf '[uninstall] Remove the %s service and installed app/venv? Type "yes" to continue: ' "${SERVICE_NAME}" >&2
    read -r c
    [[ "${c}" == "yes" ]] || die "aborted"
fi

# Stop and disable the service (ignore if already gone).
if systemctl list-unit-files "${SERVICE_NAME}.service" >/dev/null 2>&1; then
    systemctl stop "${SERVICE_NAME}" 2>/dev/null || true
    systemctl disable "${SERVICE_NAME}" 2>/dev/null || true
    log "stopped and disabled ${SERVICE_NAME}"
fi

# Remove the unit and the installed application + venv (code only — no data).
[[ -f "${UNIT_DEST}" ]] && rm -f "${UNIT_DEST}" && log "removed ${UNIT_DEST}"
systemctl daemon-reload
[[ -d "${APP_DIR}" ]]  && rm -rf "${APP_DIR}"  && log "removed ${APP_DIR}"
[[ -d "${VENV_DIR}" ]] && rm -rf "${VENV_DIR}" && log "removed ${VENV_DIR}"

if [[ "${PURGE}" -ne 1 ]]; then
    log "PRESERVED mutable data: ${CONFIG_DIR}, ${DATABASE_DIR}, ${LOGS_DIR}, ${ENV_DIR}"
    log "Device Identity is intact. To delete everything, re-run with --purge."
    exit 0
fi

# --- Destructive purge: separate, explicit confirmation ------------------------------------------
printf '[uninstall] --purge will PERMANENTLY DELETE the Device Identity, database, logs, config,\n' >&2
printf '[uninstall] and env file. This cannot be undone. Type "DELETE" to confirm: ' >&2
read -r d
[[ "${d}" == "DELETE" ]] || die "purge aborted; data left intact"

rm -rf "${CONFIG_DIR}" "${DATABASE_DIR}" "${LOGS_DIR}"
rm -rf "${ENV_DIR}"
# Remove the now-empty root if nothing else remains.
rmdir "${ROOT_DIR}" 2>/dev/null || rm -rf "${ROOT_DIR}"
log "deleted all Agent data under ${ROOT_DIR} and ${ENV_DIR}"

# Remove the service user/group last.
if getent passwd "${SERVICE_USER}" >/dev/null 2>&1; then
    userdel "${SERVICE_USER}" 2>/dev/null || true
    log "removed service user ${SERVICE_USER}"
fi
if getent group "${SERVICE_GROUP}" >/dev/null 2>&1; then
    groupdel "${SERVICE_GROUP}" 2>/dev/null || true
    log "removed service group ${SERVICE_GROUP}"
fi
log "purge complete."

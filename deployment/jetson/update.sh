#!/usr/bin/env bash
#
# Weapon Detection Jetson Agent — updater (IP-02 T-41, §12).
#
# Updates the installed Agent code and venv package in place, preserving all mutable state — the
# env file, Device Identity, ConfigCache, SQLite database, logs, and any activation-key file. It
# never triggers reactivation (it does not add or remove a key), and restarts the service only if it
# was running before the update.
#
# Run as root (via sudo). Re-runs install.sh, which is idempotent and does NOT overwrite an existing
# env file or Activation Key, nor delete Device Identity / database / logs.

set -Eeuo pipefail

readonly SERVICE_NAME="weapon-detection-agent"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
readonly SCRIPT_DIR

log() { printf '[update] %s\n' "$*"; }
die() { printf '[update] ERROR: %s\n' "$*" >&2; exit 1; }

[[ "${EUID}" -eq 0 ]] || die "must run as root (use: sudo bash update.sh)"
[[ -f "${SCRIPT_DIR}/install.sh" ]] || die "install.sh not found next to update.sh"

# Record whether the service was running, so we can restore that exact state afterwards.
was_active=0
if systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
    was_active=1
    log "service is active — stopping it for the update"
    systemctl stop "${SERVICE_NAME}"
else
    log "service is not active — it will remain stopped after the update"
fi

# install.sh preserves the env file, Activation Key, database, ConfigCache, and logs (it only
# rsyncs the app dir with --delete, and refuses to overwrite the env file / key). It also
# daemon-reloads and re-enables. It does NOT start the service.
log "applying update via install.sh"
bash "${SCRIPT_DIR}/install.sh"

if [[ "${was_active}" -eq 1 ]]; then
    log "restarting the service (it was running before the update)"
    systemctl start "${SERVICE_NAME}"
    systemctl is-active --quiet "${SERVICE_NAME}" \
        && log "service is active again" \
        || die "service failed to start after the update — check: journalctl -u ${SERVICE_NAME} -n 100"
else
    log "update complete; service left stopped (it was not running before)."
fi

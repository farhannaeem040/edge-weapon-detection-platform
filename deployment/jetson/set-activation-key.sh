#!/usr/bin/env bash
#
# Weapon Detection Jetson Agent — Activation Key provisioning helper (IP-02 T-41, §9; D-1/§6.1).
#
# Writes the one-time Activation Key to the 0600 config/activation-key file owned by the service
# user, out-of-band (ASM-006). The key is a credential:
#   - it is NEVER taken as an ordinary command-line argument (would leak into the process table and
#     shell history) — it is read from a silent prompt, or from stdin;
#   - it is NEVER echoed, logged, or placed in an error message;
#   - it is written atomically (temp file in the same dir, then mv), 0600, owned by the service user;
#   - an existing key file is NEVER silently overwritten — explicit confirmation is required.
#
# The Agent deletes the key file after a successful activation (single-use, BR-003). Stop the service
# before (re)provisioning:
#     sudo systemctl stop weapon-detection-agent
#     sudo /opt/weapon-detection/agent/deployment/jetson/set-activation-key.sh
#     sudo systemctl start weapon-detection-agent
#
# Usage:
#     sudo set-activation-key.sh            # prompt silently
#     printf '%s' "<key>" | sudo set-activation-key.sh --stdin
#     sudo set-activation-key.sh --force    # replace an existing key without the interactive confirm

set -Eeuo pipefail

readonly SERVICE_USER="weapon-detection"
readonly SERVICE_GROUP="weapon-detection"
readonly CONFIG_DIR="/opt/weapon-detection/config"
readonly KEY_FILE="${CONFIG_DIR}/activation-key"
readonly SERVICE_NAME="weapon-detection-agent"

log() { printf '[set-key] %s\n' "$*"; }
die() { printf '[set-key] ERROR: %s\n' "$*" >&2; exit 1; }

FROM_STDIN=0
FORCE=0
for arg in "$@"; do
    case "${arg}" in
        --stdin) FROM_STDIN=1 ;;
        --force) FORCE=1 ;;
        -h|--help)
            grep -E '^# ' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) die "unknown argument: ${arg} (the key is never passed as an argument)" ;;
    esac
done

[[ "${EUID}" -eq 0 ]] || die "must run as root (use: sudo set-activation-key.sh)"
[[ -d "${CONFIG_DIR}" ]] || die "config directory ${CONFIG_DIR} not found — run install.sh first"
getent passwd "${SERVICE_USER}" >/dev/null 2>&1 || die "service user ${SERVICE_USER} not found — run install.sh first"

# Warn (never fail) if the service is running — provisioning while active has no effect until restart.
if systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
    log "NOTE: ${SERVICE_NAME} is currently active. Stop it before (re)activation:"
    log "      sudo systemctl stop ${SERVICE_NAME}   (then re-run this helper, then start)"
fi

# Refuse to silently overwrite an existing key.
if [[ -e "${KEY_FILE}" && "${FORCE}" -ne 1 ]]; then
    if [[ "${FROM_STDIN}" -eq 1 ]]; then
        die "an Activation Key already exists at ${KEY_FILE}; re-run with --force to replace it (stop the service first)"
    fi
    printf '[set-key] An Activation Key already exists at %s.\n' "${KEY_FILE}" >&2
    printf '[set-key] Replace it? Stop the service first. Type "replace" to continue: ' >&2
    read -r confirm
    [[ "${confirm}" == "replace" ]] || die "aborted; existing key left unchanged"
fi

# --- Read the key without echoing ----------------------------------------------------------------
KEY=""
if [[ "${FROM_STDIN}" -eq 1 ]]; then
    IFS= read -r KEY || true          # first line of stdin; trailing newline stripped by read
else
    [[ -t 0 ]] || die "no terminal for a silent prompt; pipe the key and pass --stdin instead"
    printf '[set-key] Paste the Activation Key (input hidden): ' >&2
    IFS= read -rs KEY
    printf '\n' >&2
fi

# Trim surrounding whitespace; reject blank.
KEY="${KEY#"${KEY%%[![:space:]]*}"}"
KEY="${KEY%"${KEY##*[![:space:]]}"}"
[[ -n "${KEY}" ]] || die "empty key rejected — nothing written"

# --- Write atomically, 0600, owned by the service user (never echo the key) ----------------------
umask 077
tmp="$(mktemp "${CONFIG_DIR}/.activation-key.XXXXXX")"
# Ensure the temp file is cleaned up on any failure path.
trap 'rm -f "${tmp}"' EXIT
printf '%s' "${KEY}" > "${tmp}"
KEY=""                                # drop the plaintext from memory promptly
chmod 0600 "${tmp}"
chown "${SERVICE_USER}:${SERVICE_GROUP}" "${tmp}"
mv -f "${tmp}" "${KEY_FILE}"
trap - EXIT

log "Activation Key written to ${KEY_FILE} (0600, ${SERVICE_USER}:${SERVICE_GROUP})."
log "Start the Agent to consume it:  sudo systemctl start ${SERVICE_NAME}"

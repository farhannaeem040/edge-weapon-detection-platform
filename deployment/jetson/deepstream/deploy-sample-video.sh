#!/usr/bin/env bash
#
# Weapon Detection Jetson Agent — local sample-video staging (IP-06 T-77).
#
# Stages a local test video for the Agent-managed DeepStream lifecycle verification
# (deepstream-app.txt's [source0] uri=). Never committed to Git (see .gitignore: *.mp4/*.mkv) — this
# script is the only supported way a video reaches the managed layout.
#
# Usage:
#     sudo deploy-sample-video.sh --source <local-video-file> [--force]

set -Eeuo pipefail

readonly SERVICE_USER="weapon-detection"
readonly SERVICE_GROUP="weapon-detection"
readonly DEST_DIR="/opt/weapon-detection/samples/deepstream"
readonly DEST_FILE="${DEST_DIR}/input.mp4"

log()  { printf '[deploy-sample-video] %s\n' "$*"; }
die()  { printf '[deploy-sample-video] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
    grep -E '^# ' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

SOURCE=""
FORCE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --source) SOURCE="${2:-}"; shift 2 ;;
        --force) FORCE=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

[[ "${EUID}" -eq 0 ]] || die "must run as root (use: sudo deploy-sample-video.sh ...)"
[[ -n "${SOURCE}" ]] || die "--source is required"

# --- The source must be a regular file, never a symlink/directory/device -------------------------
[[ -e "${SOURCE}" ]] || die "source video not found: ${SOURCE}"
[[ ! -L "${SOURCE}" ]] || die "source video must not be a symlink: ${SOURCE}"
[[ -f "${SOURCE}" ]] || die "source video must be a regular file: ${SOURCE}"

install -d -m 0750 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" "${DEST_DIR}"

if [[ -e "${DEST_FILE}" && "${FORCE}" -ne 1 ]]; then
    die "a sample video already exists at ${DEST_FILE}; re-run with --force to replace it"
fi

# --- Install atomically, never print the video's contents -----------------------------------------
umask 077
tmp="$(mktemp "${DEST_DIR}/.input.mp4.XXXXXX")"
trap 'rm -f "${tmp}"' EXIT
cp "${SOURCE}" "${tmp}"
chmod 0640 "${tmp}"
chown "${SERVICE_USER}:${SERVICE_GROUP}" "${tmp}"
mv -f "${tmp}" "${DEST_FILE}"
trap - EXIT

video_size="$(stat -c '%s' "${DEST_FILE}")"
log "installed sample video to ${DEST_FILE} (${video_size} bytes, 0640, ${SERVICE_USER}:${SERVICE_GROUP})"

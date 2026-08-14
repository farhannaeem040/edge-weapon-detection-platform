#!/usr/bin/env bash
#
# DeepStream config staging — preserve-by-default (IP-07 T-91 incident follow-up, §12.4).
#
# deepstream-app.txt and each profile's infer-config.txt/labels.txt/manifest.env are
# OPERATOR-MANAGED once deployed once: an operator hand-tunes the real camera URL, RTSP
# reconnect/latency settings, tracker sizing, and encoder properties for the specific device. A
# prior unconditional `rsync -a` in install.sh silently replaced a live, customized
# deepstream-app.txt with the repo's generic template mid-deployment, breaking production RTSP.
#
# Policy:
#   - destination absent (first deploy): install the repo's template;
#   - destination present, --replace not given: leave it untouched entirely;
#   - destination present, --replace given: back it up (timestamped, root-owned, mode 0700), name
#     what is about to change (never file contents — no credential can appear in a path), replace
#     it, then re-apply the correct file mode (0644, matching every other deployed config file).
#
# Never touches models/<profile>/model.engine (deploy-engine.sh's job alone, never invoked here).
#
# Usage:
#   stage-deepstream-config.sh <src-deepstream-dir> <dest-config-dir> <dest-profiles-dir> \
#       [--replace] [--backup-root <dir>]
#
# --backup-root overrides the default timestamped backup location (mainly for tests); omit it in
# production use, where the default (/root/wda-deepstream-config-backup-<UTC timestamp>) applies.

set -Eeuo pipefail

log()  { printf '[stage-deepstream-config] %s\n' "$*"; }
die()  { printf '[stage-deepstream-config] ERROR: %s\n' "$*" >&2; exit 1; }

SRC_DEEPSTREAM_DIR="${1:?usage: stage-deepstream-config.sh <src-deepstream-dir> <dest-config-dir> <dest-profiles-dir> [--replace] [--backup-root <dir>]}"
DEST_CONFIG_DIR="${2:?usage: stage-deepstream-config.sh <src-deepstream-dir> <dest-config-dir> <dest-profiles-dir> [--replace] [--backup-root <dir>]}"
DEST_PROFILES_DIR="${3:?usage: stage-deepstream-config.sh <src-deepstream-dir> <dest-config-dir> <dest-profiles-dir> [--replace] [--backup-root <dir>]}"
shift 3

REPLACE=0
BACKUP_ROOT="/root/wda-deepstream-config-backup-$(date -u +%Y%m%dT%H%M%SZ)"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --replace) REPLACE=1; shift ;;
        --backup-root) BACKUP_ROOT="${2:?--backup-root requires a value}"; shift 2 ;;
        *) die "unknown argument: $1" ;;
    esac
done

[[ -d "${SRC_DEEPSTREAM_DIR}" ]] || die "source directory not found: ${SRC_DEEPSTREAM_DIR}"

_backup_operator_managed_path() {
    local dest="$1" label="$2"
    install -d -m 0700 "${BACKUP_ROOT}"
    local backup_dest="${BACKUP_ROOT}/$(basename "${dest}")"
    cp -a "${dest}" "${backup_dest}"
    log "backed up existing ${label} to ${backup_dest} before replacing it"
    log "replacing: ${dest}"
}

install -d -m 0750 "${DEST_CONFIG_DIR}"
install -d -m 0750 "${DEST_PROFILES_DIR}"

if [[ ! -e "${DEST_CONFIG_DIR}/deepstream-app.txt" ]]; then
    install -m 0644 "${SRC_DEEPSTREAM_DIR}/deepstream-app.txt" "${DEST_CONFIG_DIR}/deepstream-app.txt"
    log "installed deepstream-app.txt template (first deploy)"
elif [[ "${REPLACE}" -eq 1 ]]; then
    _backup_operator_managed_path "${DEST_CONFIG_DIR}/deepstream-app.txt" "deepstream-app.txt"
    install -m 0644 "${SRC_DEEPSTREAM_DIR}/deepstream-app.txt" "${DEST_CONFIG_DIR}/deepstream-app.txt"
    log "replaced deepstream-app.txt (--replace)"
else
    log "deepstream-app.txt already exists — left unchanged (pass --replace to overwrite)"
fi

if [[ -d "${SRC_DEEPSTREAM_DIR}/profiles" ]]; then
    for profile_src in "${SRC_DEEPSTREAM_DIR}"/profiles/*/; do
        [[ -d "${profile_src}" ]] || continue
        profile_name="$(basename "${profile_src}")"
        profile_dest="${DEST_PROFILES_DIR}/${profile_name}"
        if [[ ! -d "${profile_dest}" ]]; then
            install -d -m 0750 "${profile_dest}"
            rsync -a --exclude='*.engine' --exclude='*.onnx' --exclude='*.mp4' --exclude='*.mkv' \
                "${profile_src}" "${profile_dest}/"
            find "${profile_dest}" -type f -exec chmod 0644 {} \;
            log "installed profile '${profile_name}' config template (first deploy)"
        elif [[ "${REPLACE}" -eq 1 ]]; then
            _backup_operator_managed_path "${profile_dest}" "profile '${profile_name}' config"
            rsync -a --delete --exclude='*.engine' --exclude='*.onnx' --exclude='*.mp4' --exclude='*.mkv' \
                "${profile_src}" "${profile_dest}/"
            find "${profile_dest}" -type f -exec chmod 0644 {} \;
            log "replaced profile '${profile_name}' config (--replace)"
        else
            log "profile '${profile_name}' config already exists — left unchanged (pass --replace to overwrite)"
        fi
    done
fi

log "DeepStream config handling complete under ${DEST_CONFIG_DIR}"

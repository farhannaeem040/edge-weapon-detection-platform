#!/usr/bin/env bash
#
# Weapon Detection Jetson Agent — generic TensorRT engine deployment (IP-06 T-70, FS-04 §8.2).
#
# Installs a validated TensorRT engine as a named model profile under
# /opt/weapon-detection/models/<profile>/model.engine. Deliberately generic: this script has no
# knowledge of YOLOv4, or of any other model architecture — every model-specific fact (dimensions,
# precision, parser, class count) lives only in the profile's manifest.env and infer-config.txt,
# never here.
#
# Compatibility is established ONLY through the manifest's checksum and fields — never through the
# source engine's filename. Nothing is installed unless every check below passes.
#
# Usage:
#     sudo deploy-engine.sh --profile <profile-name> --engine <source.engine> --manifest <manifest-file> [--force]
#
# Run as root (via sudo). Safe to re-run; refuses to overwrite an existing model.engine without
# --force.

set -Eeuo pipefail

readonly SERVICE_USER="weapon-detection"
readonly SERVICE_GROUP="weapon-detection"
readonly ROOT_DIR="/opt/weapon-detection"
readonly MODELS_DIR="${ROOT_DIR}/models"
readonly PROFILES_DIR="${ROOT_DIR}/config/deepstream/profiles"

# Lowercase alphanumeric, optionally hyphenated, never starting with a hyphen — matches
# AgentSettings' own WDA_DEEPSTREAM_MODEL_PROFILE validation exactly (config/settings.py).
readonly PROFILE_NAME_PATTERN='^[a-z0-9][a-z0-9-]*$'

log()  { printf '[deploy-engine] %s\n' "$*"; }
warn() { printf '[deploy-engine] WARNING: %s\n' "$*" >&2; }
die()  { printf '[deploy-engine] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
    grep -E '^# ' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

# --- 1. Parse arguments (the engine is never accepted as a bare positional — explicit flags only) -
PROFILE=""
ENGINE=""
MANIFEST=""
FORCE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --profile) PROFILE="${2:-}"; shift 2 ;;
        --engine) ENGINE="${2:-}"; shift 2 ;;
        --manifest) MANIFEST="${2:-}"; shift 2 ;;
        --force) FORCE=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

[[ "${EUID}" -eq 0 ]] || die "must run as root (use: sudo deploy-engine.sh ...)"
[[ -n "${PROFILE}" ]] || die "--profile is required"
[[ -n "${ENGINE}" ]] || die "--engine is required"
[[ -n "${MANIFEST}" ]] || die "--manifest is required"

# --- 2. Validate the profile name BEFORE it is used in any path -----------------------------------
[[ "${PROFILE}" =~ ${PROFILE_NAME_PATTERN} ]] \
    || die "unsafe profile name '${PROFILE}' (must match ${PROFILE_NAME_PATTERN})"

# --- 3. The engine must be a regular file, never a symlink/directory/device, named *.engine --------
[[ -e "${ENGINE}" ]] || die "engine not found: ${ENGINE}"
[[ ! -L "${ENGINE}" ]] || die "engine must not be a symlink: ${ENGINE}"
[[ -f "${ENGINE}" ]] || die "engine must be a regular file: ${ENGINE}"
[[ "${ENGINE}" == *.engine ]] || die "engine file must end in .engine: ${ENGINE}"

# --- 4. The manifest must exist and be a regular file ----------------------------------------------
[[ -f "${MANIFEST}" && ! -L "${MANIFEST}" ]] || die "manifest not found or not a regular file: ${MANIFEST}"

manifest_field() {
    # Flat KEY=VALUE, first match, trailing CR stripped (a manifest edited on Windows is still safe).
    sed -n "s/^$1=//p" "${MANIFEST}" | head -n1 | tr -d '\r'
}

# --- 5. The manifest must be complete: every required field present and non-empty ------------------
readonly REQUIRED_FIELDS=(
    PROFILE_NAME ENGINE_SHA256 ENGINE_PRECISION INPUT_DIMENSIONS CLASS_COUNT LABELS_FILE
    OUTPUT_BLOB_NAMES DEEPSTREAM_VERSION TENSORRT_VERSION TARGET_DEVICE VALIDATED_DATE
    VALIDATED_STATUS
)
declare -A MANIFEST_VALUES
for field in "${REQUIRED_FIELDS[@]}"; do
    value="$(manifest_field "${field}")"
    [[ -n "${value}" ]] || die "manifest is incomplete: missing or empty ${field}"
    MANIFEST_VALUES["${field}"]="${value}"
done
# PARSER_LIB/PARSER_FUNC are required together only when a custom parser is used (some DeepStream
# primary-gie configs use a built-in parser and set neither) — but if either is present, both must be.
PARSER_LIB="$(manifest_field PARSER_LIB)"
PARSER_FUNC="$(manifest_field PARSER_FUNC)"
if [[ -n "${PARSER_LIB}" || -n "${PARSER_FUNC}" ]]; then
    [[ -n "${PARSER_LIB}" && -n "${PARSER_FUNC}" ]] \
        || die "manifest sets only one of PARSER_LIB/PARSER_FUNC — both or neither"
fi

# --- 6. The manifest's own PROFILE_NAME must match the requested --profile -------------------------
[[ "${MANIFEST_VALUES[PROFILE_NAME]}" == "${PROFILE}" ]] \
    || die "manifest PROFILE_NAME (${MANIFEST_VALUES[PROFILE_NAME]}) does not match --profile (${PROFILE})"

# --- 7. Checksum verification — never the filename, only the manifest + the real file bytes --------
log "verifying engine checksum (this reads the whole file; may take a moment)"
computed_sha256="$(sha256sum "${ENGINE}" | awk '{print $1}')"
expected_sha256="${MANIFEST_VALUES[ENGINE_SHA256]}"
[[ "${computed_sha256}" == "${expected_sha256}" ]] \
    || die "checksum mismatch: computed ${computed_sha256}, manifest expects ${expected_sha256} — refusing to install"
log "checksum verified (sha256:${computed_sha256})"

# --- 8. The profile's sibling config files must already be installed (by install.sh) ---------------
PROFILE_CONFIG_DIR="${PROFILES_DIR}/${PROFILE}"
[[ -d "${PROFILE_CONFIG_DIR}" ]] \
    || die "profile config directory missing: ${PROFILE_CONFIG_DIR} (run install.sh first)"
[[ -f "${PROFILE_CONFIG_DIR}/infer-config.txt" ]] \
    || die "profile is missing infer-config.txt: ${PROFILE_CONFIG_DIR}/infer-config.txt"
labels_file="${MANIFEST_VALUES[LABELS_FILE]}"
[[ -f "${PROFILE_CONFIG_DIR}/${labels_file}" ]] \
    || die "profile is missing its labels file: ${PROFILE_CONFIG_DIR}/${labels_file}"

# --- 9. A referenced parser library must exist on this device --------------------------------------
if [[ -n "${PARSER_LIB}" ]]; then
    [[ -f "${PARSER_LIB}" ]] || die "manifest PARSER_LIB does not exist on this device: ${PARSER_LIB}"
fi

# --- 10. Install atomically as the canonical model.engine name -------------------------------------
DEST_DIR="${MODELS_DIR}/${PROFILE}"
DEST_FILE="${DEST_DIR}/model.engine"
install -d -m 0750 "${MODELS_DIR}"
install -d -m 0750 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" "${DEST_DIR}"

if [[ -e "${DEST_FILE}" && "${FORCE}" -ne 1 ]]; then
    die "an engine already exists at ${DEST_FILE}; re-run with --force to replace it"
fi

umask 077
tmp="$(mktemp "${DEST_DIR}/.model.engine.XXXXXX")"
trap 'rm -f "${tmp}"' EXIT
cp "${ENGINE}" "${tmp}"
chmod 0640 "${tmp}"
chown "${SERVICE_USER}:${SERVICE_GROUP}" "${tmp}"
mv -f "${tmp}" "${DEST_FILE}"
trap - EXIT

# --- 11. Report only safe, non-binary facts ---------------------------------------------------------
engine_size="$(stat -c '%s' "${DEST_FILE}")"
log "installed profile '${PROFILE}' engine to ${DEST_FILE} (${engine_size} bytes, sha256:${computed_sha256})"
log "precision=${MANIFEST_VALUES[ENGINE_PRECISION]} classes=${MANIFEST_VALUES[CLASS_COUNT]} deepstream=${MANIFEST_VALUES[DEEPSTREAM_VERSION]} tensorrt=${MANIFEST_VALUES[TENSORRT_VERSION]}"
log "validated ${MANIFEST_VALUES[VALIDATED_DATE]} (${MANIFEST_VALUES[VALIDATED_STATUS]}) on: ${MANIFEST_VALUES[TARGET_DEVICE]}"
log "note: this profile is not yet active until deepstream-app.txt's [primary-gie] config-file= points at ${PROFILE_CONFIG_DIR}/infer-config.txt"

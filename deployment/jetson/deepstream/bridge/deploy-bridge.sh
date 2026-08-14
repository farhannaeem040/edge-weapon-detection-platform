#!/usr/bin/env bash
#
# DeepStream Bridge — venv build/verify (IP-07 T-89, FS-05 §4.6).
#
# Operator-run, idempotent. Builds (or verifies) the Bridge's own Python 3.8 virtual environment at
# /opt/weapon-detection/deepstream-bridge/venv, installs its pinned dependencies from
# requirements.lock, and verifies the result actually imports. Mirrors deploy-engine.sh's posture:
# manual, never invoked by install.sh, refuses to silently clobber a working install.
#
# The venv holds DEPENDENCIES ONLY — this script never installs the deepstream_bridge application
# package into it (not even editably). The application is made importable purely through run.sh's
# process-local PYTHONPATH at run time (FS-05 §4.6) — so re-running this script after a source
# update (staged separately by install.sh) is unnecessary; only a dependency change needs a rebuild.
#
# Usage:
#     sudo deploy-bridge.sh [--force]
#
# --force: rebuild the venv from scratch even if a working one already exists (the existing venv/
# directory is removed first). Without --force, a working existing venv is left untouched and this
# script exits 0 having only re-run the verification. Run as root (via sudo).

set -Eeuo pipefail

readonly SERVICE_USER="weapon-detection"
readonly SERVICE_GROUP="weapon-detection"
readonly BRIDGE_ROOT="/opt/weapon-detection/deepstream-bridge"
readonly VENV_DIR="${BRIDGE_ROOT}/venv"
readonly APP_DIR="${BRIDGE_ROOT}/app"
readonly REQUIREMENTS_LOCK="${BRIDGE_ROOT}/requirements.lock"

log()  { printf '[deploy-bridge] %s\n' "$*"; }
warn() { printf '[deploy-bridge] WARNING: %s\n' "$*" >&2; }
die()  { printf '[deploy-bridge] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
    grep -E '^# ' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

# --- 1. Parse arguments -----------------------------------------------------------------------------
FORCE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --force) FORCE=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

# --- 2. Preconditions: root, aarch64, Python 3.8, requirements.lock present -------------------------
[[ "${EUID}" -eq 0 ]] || die "must run as root (use: sudo deploy-bridge.sh [--force])"

arch="$(uname -m)"
[[ "${arch}" == "aarch64" ]] || die "this script targets aarch64 Jetson devices; got ${arch}"

command -v python3.8 >/dev/null 2>&1 || die "python3.8 not found on PATH — install it before continuing"
py_version="$(python3.8 --version 2>&1)"
[[ "${py_version}" == "Python 3.8."* ]] || die "python3.8 reports an unexpected version: ${py_version}"
log "using ${py_version}"

[[ -f "${REQUIREMENTS_LOCK}" ]] \
    || die "requirements.lock not found at ${REQUIREMENTS_LOCK} — stage the Bridge source first (install.sh)"

# --- 3. Verify a venv actually works: real gi/Gst/pyds/NvDs*Meta imports, not just file presence ----
verify_venv() {
    local python_bin="$1"
    [[ -x "${python_bin}" ]] || return 1
    "${python_bin}" -c "
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst
Gst.init(None)
import pyds
assert hasattr(pyds, 'NvDsObjectMeta'), 'pyds.NvDsObjectMeta missing'
assert hasattr(pyds, 'NvDsFrameMeta'), 'pyds.NvDsFrameMeta missing'
" >/dev/null 2>&1
}

# --- 4. Decide: leave an existing working venv alone unless --force ---------------------------------
if [[ -d "${VENV_DIR}" ]]; then
    if verify_venv "${VENV_DIR}/bin/python"; then
        if [[ "${FORCE}" -ne 1 ]]; then
            log "existing venv at ${VENV_DIR} is present and working — leaving it untouched (use --force to rebuild)"
            SKIP_BUILD=1
        else
            log "existing venv at ${VENV_DIR} is working, but --force was given — rebuilding from scratch"
            rm -rf "${VENV_DIR}"
            SKIP_BUILD=0
        fi
    else
        log "existing venv at ${VENV_DIR} is present but does not import gi/Gst/pyds correctly — rebuilding"
        rm -rf "${VENV_DIR}"
        SKIP_BUILD=0
    fi
else
    SKIP_BUILD=0
fi

# --- 5. Build the venv (--system-site-packages: the venv sees system gi/PyGObject/GStreamer without
# reinstalling them; pyds/pgi are installed inside the venv itself, never system-wide) --------------
if [[ "${SKIP_BUILD}" -ne 1 ]]; then
    python3.8 -m venv --system-site-packages "${VENV_DIR}" \
        || die "failed to create venv at ${VENV_DIR} (is python3.8-venv installed?)"
    log "created venv at ${VENV_DIR} (--system-site-packages)"

    # Dependencies only, hash-verified, no resolver-driven surprises (--no-deps: requirements.lock is
    # the complete, reviewed dependency list; --require-hashes: refuse anything that doesn't match).
    "${VENV_DIR}/bin/pip" install --no-deps --require-hashes -r "${REQUIREMENTS_LOCK}" \
        || die "failed to install requirements.lock into ${VENV_DIR}"
    log "installed pinned dependencies from ${REQUIREMENTS_LOCK}"
fi

# --- 6. Verify the (possibly just-built, possibly pre-existing) venv works --------------------------
verify_venv "${VENV_DIR}/bin/python" \
    || die "venv verification failed: gi/Gst/pyds/NvDsObjectMeta/NvDsFrameMeta did not all import cleanly"
log "verified: gi, Gst, pyds, pyds.NvDsObjectMeta, pyds.NvDsFrameMeta all import correctly"

# --- 7. The venv holds dependencies only — confirm the app package was never installed into it -----
if "${VENV_DIR}/bin/pip" show deepstream_bridge >/dev/null 2>&1; then
    die "deepstream_bridge is installed inside the venv's site-packages — this script never does " \
        "this; remove it manually (pip uninstall) and rebuild, since a stale non-editable copy " \
        "could shadow newly staged source (FS-05 §4.6)"
fi

# --- 8. Verify the deployed app/ is importable via a PROCESS-LOCAL PYTHONPATH only (never a global
# shell profile / /etc/environment) — matching exactly how run.sh will invoke it -----------------
if [[ -d "${APP_DIR}" ]]; then
    PYTHONPATH="${APP_DIR}" "${VENV_DIR}/bin/python" -c "
import deepstream_bridge
print('deepstream_bridge importable from', deepstream_bridge.__file__)
" || die "deepstream_bridge is not importable via PYTHONPATH=${APP_DIR} through the venv interpreter"
    log "verified: deepstream_bridge importable via process-local PYTHONPATH=${APP_DIR}"
else
    warn "app/ not found at ${APP_DIR} yet — skipping the app-importability check (stage the Bridge source first)"
fi

# --- 9. Ownership: the unprivileged service account, never root-run at runtime ----------------------
chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${BRIDGE_ROOT}"
log "set ownership of ${BRIDGE_ROOT} to ${SERVICE_USER}:${SERVICE_GROUP}"

log "deploy-bridge.sh complete. The Bridge venv at ${VENV_DIR} is ready."
log "Next: point WDA_DEEPSTREAM_EXECUTABLE_PATH at ${BRIDGE_ROOT}/run.sh only when the operator is"
log "ready to enable detection events (T-91) — this script does not change any Agent configuration."

#!/usr/bin/env bash
#
# Weapon Detection Jetson Agent — installer (IP-02 T-41, §11, §17).
#
# Idempotent native install under systemd. Creates the unprivileged service user, the
# /opt/weapon-detection layout with ADR-008 modes, a Python venv (Python >=3.10; on this JetPack 5 /
# Ubuntu 20.04 device system Python is 3.8, so this installer BUILDS CPython 3.11 FROM SOURCE via
# 'make altinstall' to /usr/local — deadsnakes publishes no arm64 packages, so it is not used),
# installs the Agent runtime package, the non-secret env file (only if absent), and the systemd
# unit; then daemon-reload + enable. It does NOT start an unactivated Agent, and never prints a
# secret.
#
# Run as root (via sudo). Safe to re-run.

set -Eeuo pipefail

# --- Constants (the approved layout, §5/§8) ------------------------------------------------------
readonly SERVICE_NAME="weapon-detection-agent"
readonly SERVICE_USER="weapon-detection"
readonly SERVICE_GROUP="weapon-detection"
readonly ROOT_DIR="/opt/weapon-detection"
readonly APP_DIR="${ROOT_DIR}/agent"
readonly VENV_DIR="${ROOT_DIR}/venv"
readonly CONFIG_DIR="${ROOT_DIR}/config"
readonly DATABASE_DIR="${ROOT_DIR}/database"
readonly LOGS_DIR="${ROOT_DIR}/logs"
readonly KEY_FILE="${CONFIG_DIR}/activation-key"
# DeepStream layout (IP-06 T-70, FS-04 §8) — generic, profile-based. install.sh provisions the
# directories and syncs the committed config templates only; it never writes models/<profile>/
# model.engine (that is deploy-engine.sh's job alone, and it is never run automatically here).
readonly MODELS_DIR="${ROOT_DIR}/models"
readonly DEEPSTREAM_CONFIG_DIR="${CONFIG_DIR}/deepstream"
readonly DEEPSTREAM_PROFILES_DIR="${DEEPSTREAM_CONFIG_DIR}/profiles"
readonly DEEPSTREAM_LOGS_DIR="${LOGS_DIR}/deepstream"
# Local test-video staging (T-77 local-video lifecycle verification). Never populated by install.sh
# itself — only deploy-sample-video.sh writes input.mp4 here, and it is never run automatically.
readonly SAMPLES_DIR="${ROOT_DIR}/samples"
readonly DEEPSTREAM_SAMPLES_DIR="${SAMPLES_DIR}/deepstream"
readonly ENV_DIR="/etc/weapon-detection-agent"
readonly ENV_FILE="${ENV_DIR}/agent.env"
readonly UNIT_DEST="/etc/systemd/system/${SERVICE_NAME}.service"
readonly MIN_PY_MINOR=10          # Agent requires-python = ">=3.10"
readonly PY_SRC_VERSION="3.11.9"  # built from source on aarch64 (deadsnakes has no arm64 build)
readonly PY_LOCAL_BIN="/usr/local/bin/python3.11"

# --- Resolve paths from the script location (works from any CWD) ---------------------------------
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
readonly SCRIPT_DIR
# Staged/committed layout: deployment/jetson/ (this dir) is a sibling of agent/ under a common root.
readonly REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." >/dev/null 2>&1 && pwd -P)"
readonly SRC_AGENT_DIR="${REPO_ROOT}/agent"
readonly SRC_JETSON_DIR="${SCRIPT_DIR}"
readonly SRC_DEEPSTREAM_DIR="${SCRIPT_DIR}/deepstream"

log()  { printf '[install] %s\n' "$*"; }
warn() { printf '[install] WARNING: %s\n' "$*" >&2; }
die()  { printf '[install] ERROR: %s\n' "$*" >&2; exit 1; }

# --- 1. Require root -----------------------------------------------------------------------------
[[ "${EUID}" -eq 0 ]] || die "must run as root (use: sudo bash install.sh)"

# --- 2. Verify Linux + systemd -------------------------------------------------------------------
[[ "$(uname -s)" == "Linux" ]] || die "this installer targets Linux (the Jetson); got $(uname -s)"
command -v systemctl >/dev/null 2>&1 || die "systemctl not found — systemd is required (ARCH-CON-002)"

# --- 3/4. Ensure a compatible Python (>=3.10); build CPython 3.11 from source if none is found ----
find_compatible_python() {
    local cand
    for cand in python3.13 python3.12 python3.11 python3.10; do
        if command -v "${cand}" >/dev/null 2>&1; then
            printf '%s' "${cand}"; return 0
        fi
    done
    # Fall back to the default python3 only if it is already >=3.10.
    if command -v python3 >/dev/null 2>&1 \
        && python3 -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 10) else 1)'; then
        printf '%s' "python3"; return 0
    fi
    return 1
}

venv_works() {
    # A usable interpreter must have the venv module WITH ensurepip (needed to bootstrap pip), plus
    # the ssl and sqlite3 modules the Agent depends on.
    "$1" -c 'import venv, ensurepip, ssl, sqlite3' >/dev/null 2>&1
}

# Build CPython from source and 'make altinstall' to /usr/local. deadsnakes publishes no arm64
# packages, and Ubuntu 20.04 (Focal) ships only 3.8/3.9, so on this aarch64 Jetson a source build is
# the way to a >=3.10 interpreter. altinstall installs python3.11 WITHOUT touching system python3.8.
build_python_from_source() {
    command -v apt-get >/dev/null 2>&1 || die "apt-get not found; install Python >=3.10 (with venv/ssl/sqlite3) manually and re-run."
    export DEBIAN_FRONTEND=noninteractive

    # Remove a stray deadsnakes PPA a prior attempt may have added (it has no arm64 packages).
    local dead
    for dead in /etc/apt/sources.list.d/*deadsnakes*.list; do
        [[ -e "${dead}" ]] && { rm -f "${dead}"; log "removed stray deadsnakes apt source ${dead}"; }
    done

    log "installing build dependencies"
    apt-get update -qq || warn "apt-get update reported issues; continuing"
    apt-get install -y -qq build-essential wget ca-certificates \
        libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev \
        libffi-dev liblzma-dev uuid-dev libncurses5-dev libgdbm-dev \
        || die "failed to install Python build dependencies"

    local tgz="Python-${PY_SRC_VERSION}.tgz"
    local url="https://www.python.org/ftp/python/${PY_SRC_VERSION}/${tgz}"
    local build_root; build_root="$(mktemp -d /tmp/py-build-XXXXXX)"
    log "downloading ${url}"
    # HTTPS to python.org provides transport integrity; no third-party mirror is used.
    wget -q -O "${build_root}/${tgz}" "${url}" || die "failed to download CPython source from ${url}"
    tar -xzf "${build_root}/${tgz}" -C "${build_root}" || die "failed to extract CPython source"

    log "configuring and building CPython ${PY_SRC_VERSION} (many minutes on the Jetson)"
    (
        cd "${build_root}/Python-${PY_SRC_VERSION}"
        # PGO (--enable-optimizations) is deliberately omitted: it multiplies build time and this
        # control-plane Agent does not need it. ensurepip is installed so venv can bootstrap pip.
        ./configure --prefix=/usr/local --with-ensurepip=install >/dev/null || exit 1
        make -j"$(nproc)" >/dev/null || exit 1
        make altinstall >/dev/null || exit 1
    ) || die "CPython build failed (check build dependencies)"
    rm -rf "${build_root}"

    # Verify the modules the Agent actually needs were built (missing dev headers fail silently).
    "${PY_LOCAL_BIN}" -c "import ssl, sqlite3, ctypes, venv, ensurepip" \
        || die "built Python is missing a required module (ssl/sqlite3/ctypes/venv) — check build deps"
}

PY=""
if PY="$(find_compatible_python)" && venv_works "${PY}"; then
    log "using existing compatible interpreter: ${PY} ($("${PY}" --version 2>&1))"
else
    log "no usable Python >=3.10 (with venv/ssl/sqlite3) found; building CPython ${PY_SRC_VERSION} from source"
    build_python_from_source
    PY="${PY_LOCAL_BIN}"
    venv_works "${PY}" || die "built ${PY} but venv/ssl/sqlite3 is unavailable"
    log "built ${PY} ($("${PY}" --version 2>&1))"
fi

# Best-effort: the sqlite3 CLI is used by the manual §21 verification (verify.sh itself does not
# depend on it — it queries via the venv Python). Non-fatal if it cannot be installed.
if ! command -v sqlite3 >/dev/null 2>&1 && command -v apt-get >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq sqlite3 >/dev/null 2>&1 \
        && log "installed the sqlite3 CLI (for manual verification)" \
        || warn "could not install the sqlite3 CLI; verify.sh does not need it"
fi

# --- 5. Service user/group (system account; no login, no password, no sudo) ----------------------
if getent group "${SERVICE_GROUP}" >/dev/null 2>&1; then
    log "group ${SERVICE_GROUP} already exists"
else
    groupadd --system "${SERVICE_GROUP}"
    log "created system group ${SERVICE_GROUP}"
fi
if getent passwd "${SERVICE_USER}" >/dev/null 2>&1; then
    log "user ${SERVICE_USER} already exists"
else
    useradd --system --gid "${SERVICE_GROUP}" --no-create-home \
        --home-dir "${ROOT_DIR}" --shell /usr/sbin/nologin \
        --comment "Weapon Detection Agent" "${SERVICE_USER}"
    log "created system user ${SERVICE_USER} (no login shell, no password)"
fi
# NOTE: the service user is deliberately NOT added to 'video'/'render' here. This milestone's Agent
# uses no camera or GPU (DeepStream is OI-1/excluded). The future DeepStream milestone adds that
# membership when device access is actually justified (§6, least privilege).

# --- 6. Directory layout with ADR-008 modes (§8) -------------------------------------------------
install -d -m 0750 "${ROOT_DIR}"
install -d -m 0700 "${CONFIG_DIR}"
install -d -m 0700 "${DATABASE_DIR}"
install -d -m 0750 "${LOGS_DIR}"
log "provisioned ${ROOT_DIR} layout (root 0750, config 0700, database 0700, logs 0750)"

# --- 6a. DeepStream layout (IP-06 T-70, FS-04 §8) — parent directories only; deploy-engine.sh
# creates a specific models/<profile>/ subdirectory, never install.sh.
install -d -m 0750 "${MODELS_DIR}"
install -d -m 0750 "${DEEPSTREAM_CONFIG_DIR}"
install -d -m 0750 "${DEEPSTREAM_PROFILES_DIR}"
install -d -m 0750 "${DEEPSTREAM_LOGS_DIR}"
install -d -m 0750 "${SAMPLES_DIR}"
install -d -m 0750 "${DEEPSTREAM_SAMPLES_DIR}"
log "provisioned DeepStream layout (models/, config/deepstream/, logs/deepstream/, samples/deepstream/, all 0750)"

# Sync the committed, profile-agnostic application config and every committed profile's
# infer-config.txt/labels.txt/manifest.env. Never touches models/<profile>/model.engine (not synced
# from here — only deploy-engine.sh writes an engine, and it is never invoked automatically).
if [[ -d "${SRC_DEEPSTREAM_DIR}" ]]; then
    rsync -a --exclude='*.engine' --exclude='*.onnx' --exclude='*.mp4' --exclude='*.mkv' \
        "${SRC_DEEPSTREAM_DIR}/deepstream-app.txt" "${DEEPSTREAM_CONFIG_DIR}/deepstream-app.txt"
    rsync -a --exclude='*.engine' --exclude='*.onnx' --exclude='*.mp4' --exclude='*.mkv' \
        "${SRC_DEEPSTREAM_DIR}/profiles/" "${DEEPSTREAM_PROFILES_DIR}/"
    log "synced DeepStream config templates to ${DEEPSTREAM_CONFIG_DIR}"
else
    warn "no ${SRC_DEEPSTREAM_DIR} found; skipping DeepStream config sync"
fi

# --- 8. Copy Agent source to the installation directory ------------------------------------------
[[ -f "${SRC_AGENT_DIR}/pyproject.toml" ]] || die "Agent source not found at ${SRC_AGENT_DIR} (expected pyproject.toml)"
install -d -m 0755 "${APP_DIR}"
# Mirror the source, excluding dev/test cruft and anything secret. --delete keeps updates clean while
# only touching the app dir (never config/database/logs).
rsync -a --delete \
    --exclude='.git/' \
    --exclude='.venv/' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='.pytest_cache/' \
    --exclude='.mypy_cache/' \
    --exclude='.ruff_cache/' \
    --exclude='*.db' \
    --exclude='*.log' \
    --exclude='.env' \
    "${SRC_AGENT_DIR}/" "${APP_DIR}/"
# Ship the Jetson deployment helpers alongside the app so the documented on-device operator paths
# exist (e.g. ${APP_DIR}/deployment/jetson/set-activation-key.sh).
install -d -m 0755 "${APP_DIR}/deployment/jetson"
rsync -a --exclude='__pycache__/' "${SRC_JETSON_DIR}/" "${APP_DIR}/deployment/jetson/"
log "installed Agent source to ${APP_DIR}"

# --- 9/10. Create/update the venv and install the Agent runtime package ---------------------------
if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    "${PY}" -m venv "${VENV_DIR}"
    log "created virtual environment at ${VENV_DIR} (${PY})"
else
    log "virtual environment already present at ${VENV_DIR}"
fi
"${VENV_DIR}/bin/python" -m pip install --quiet --upgrade pip
# Runtime package only — NOT the [dev] extras (§7). Reinstall so a code update takes effect.
"${VENV_DIR}/bin/python" -m pip install --quiet --upgrade "${APP_DIR}"
log "installed the Agent runtime package into the venv"

# --- 11. Non-secret env file (only when absent — never overwrite a deployed one) ------------------
install -d -m 0755 "${ENV_DIR}"
if [[ -e "${ENV_FILE}" ]]; then
    log "env file ${ENV_FILE} already exists — left unchanged"
else
    install -m 0600 "${SRC_JETSON_DIR}/agent.env.example" "${ENV_FILE}"
    chown root:root "${ENV_FILE}"
    log "installed env template to ${ENV_FILE} (0600 root:root) — review WDA_BACKEND_BASE_URL"
fi

# --- 7. Ownership + final modes ------------------------------------------------------------------
# Everything under the Agent root is owned by the unprivileged service user (no chmod 777, never
# root-run). Re-apply the sensitive directory modes after chown (self-healing).
chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${ROOT_DIR}"
chmod 0750 "${ROOT_DIR}"
chmod 0700 "${CONFIG_DIR}" "${DATABASE_DIR}"
chmod 0750 "${LOGS_DIR}"
chmod 0750 "${MODELS_DIR}" "${DEEPSTREAM_CONFIG_DIR}" "${DEEPSTREAM_PROFILES_DIR}" "${DEEPSTREAM_LOGS_DIR}" "${SAMPLES_DIR}" "${DEEPSTREAM_SAMPLES_DIR}"
# Preserve a 0600 activation-key file if one is staged for a pending first activation.
[[ -e "${KEY_FILE}" ]] && chmod 0600 "${KEY_FILE}" && chown "${SERVICE_USER}:${SERVICE_GROUP}" "${KEY_FILE}"
# Re-assert the env file's mode/ownership on every run (self-healing), even though its CONTENT is
# only ever written once (never overwritten). It stays root:root — systemd reads it as root before
# dropping privileges (§8) — never the service user.
[[ -e "${ENV_FILE}" ]] && chmod 0600 "${ENV_FILE}" && chown root:root "${ENV_FILE}"

# --- 12/13. systemd unit -------------------------------------------------------------------------
install -m 0644 "${SRC_JETSON_DIR}/${SERVICE_NAME}.service" "${UNIT_DEST}"
chown root:root "${UNIT_DEST}"
log "installed systemd unit to ${UNIT_DEST}"
systemctl daemon-reload
log "ran systemctl daemon-reload"

# --- 14/15. Enable (start on boot); do NOT start an unactivated Agent -----------------------------
systemctl enable "${SERVICE_NAME}" >/dev/null 2>&1 || warn "could not enable ${SERVICE_NAME}"
log "enabled ${SERVICE_NAME} (starts on boot)"

# Decide whether the Agent can start: it needs either an existing identity (already activated) or a
# provisioned Activation Key. Starting without either would fail by design (§12.3).
has_identity=1
if [[ -f "${DATABASE_DIR}/agent.db" ]]; then
    if ! "${VENV_DIR}/bin/python" - "${DATABASE_DIR}/agent.db" <<'PY' >/dev/null 2>&1
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
try:
    n = c.execute("SELECT COUNT(*) FROM DeviceIdentity").fetchone()[0]
finally:
    c.close()
raise SystemExit(0 if n >= 1 else 1)
PY
    then has_identity=0; fi
else
    has_identity=0
fi

# --- 16. Next safe operator commands (never print secrets) ---------------------------------------
echo
log "install complete."
if [[ "${has_identity}" -eq 1 ]]; then
    echo "  The Agent already holds a Device identity. Start it with:"
    echo "      sudo systemctl start ${SERVICE_NAME}"
elif [[ -e "${KEY_FILE}" ]]; then
    echo "  An Activation Key file is present. Start the Agent to activate:"
    echo "      sudo systemctl start ${SERVICE_NAME}"
else
    echo "  The Agent is UNACTIVATED and has no Activation Key — it was NOT started."
    echo "  1) Confirm the Backend URL in ${ENV_FILE}"
    echo "  2) Provision an Activation Key (generated in the central Dashboard):"
    echo "         sudo ${APP_DIR}/deployment/jetson/set-activation-key.sh"
    echo "  3) Start the Agent:"
    echo "         sudo systemctl start ${SERVICE_NAME}"
fi
echo "  Then verify:   sudo ${APP_DIR}/deployment/jetson/verify.sh"
echo "  Logs:          journalctl -u ${SERVICE_NAME} -n 100 --no-pager"

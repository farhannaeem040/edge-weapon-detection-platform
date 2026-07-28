#!/usr/bin/env bash
#
# DeepStream Bridge — production cutover preflight (IP-07 T-90, task item 11).
#
# A read-only command T-91 runs BEFORE switching WDA_DEEPSTREAM_EXECUTABLE_PATH to the Bridge and
# WDA_DETECTION_EVENTS_ENABLED to true. Validates that the settings combination, the target
# executable, the active profile's files, the Bridge's imports, the detection-socket directory, the
# Agent<->Bridge wire-protocol constants, and the RTSP/multicast prerequisites all agree — without
# making a single persistent change.
#
# This script NEVER:
#   - writes /etc/weapon-detection-agent/agent.env;
#   - restarts weapon-detection-agent.service or any other systemd unit;
#   - starts the full DeepStream/Bridge video pipeline;
#   - binds RTSP port 8554 (production) or any other port;
#   - prints agent.env's contents, an Activation Key, a full RTSP URL, or any other secret.
#
# Usage:
#     sudo deployment/jetson/deepstream/bridge/preflight-cutover.sh
#
# Exit code 0 only when every check passes. Non-zero otherwise, with one PREFLIGHT_FAIL line per
# failed check on stderr (a precise, redacted reason — a field/check name, never a secret value).

set -Eeuo pipefail

readonly ROOT_DIR="/opt/weapon-detection"
readonly AGENT_DIR="${ROOT_DIR}/agent"
readonly AGENT_VENV="${ROOT_DIR}/venv"
readonly BRIDGE_ROOT="${ROOT_DIR}/deepstream-bridge"
readonly BRIDGE_VENV="${BRIDGE_ROOT}/venv"
readonly BRIDGE_APP="${BRIDGE_ROOT}/app"
readonly BRIDGE_RUN_SH="${BRIDGE_ROOT}/run.sh"
readonly ENV_FILE="/etc/weapon-detection-agent/agent.env"
readonly RUNTIME_DIR="${ROOT_DIR}/runtime"
readonly SERVICE_USER="weapon-detection"
readonly PRODUCTION_RTSP_PORT="8554"
readonly MULTICAST_GROUP="224.224.255.255"
readonly ROUTE_SERVICE="deepstream-rtsp-route.service"

FAILURES=0

ok()   { printf '[preflight] OK   %s\n' "$*"; }
warn() { printf '[preflight] WARN %s\n' "$*" >&2; }
fail() { printf '[preflight] FAIL %s\n' "$*" >&2; FAILURES=$((FAILURES + 1)); }

echo "=== DeepStream Bridge cutover preflight ==="

# --- 0. Root (needed to read agent.env's 0600 contents and sudo -u the service account) -----------
if [[ "${EUID}" -ne 0 ]]; then
    fail "must run as root (use: sudo preflight-cutover.sh)"
    echo "=== preflight FAILED (${FAILURES} check(s)) ==="
    exit 1
fi

# --- 1. Settings combination (delegates to the Agent's own load_settings() + T-90 preflight CLI) --
# Reads the deployed, non-secret agent.env as the base environment (never printed), then overrides
# only the three cutover-relevant variables to simulate the post-cutover configuration — this is
# what actually gets validated, not the currently-safe deployed state.
if [[ ! -x "${AGENT_VENV}/bin/python" ]]; then
    fail "settings: Agent venv interpreter not found: ${AGENT_VENV}/bin/python"
elif [[ ! -r "${ENV_FILE}" ]]; then
    fail "settings: cannot read ${ENV_FILE} (run as root)"
else
    set +e
    settings_output="$(
        set -a
        # shellcheck disable=SC1090
        source "${ENV_FILE}"
        set +a
        export WDA_DEEPSTREAM_ENABLED=true
        export WDA_DETECTION_EVENTS_ENABLED=true
        export WDA_DEEPSTREAM_EXECUTABLE_PATH="${BRIDGE_RUN_SH}"
        # weapon_detection_agent is pip-installed into AGENT_VENV by install.sh (not run from
        # source-on-PYTHONPATH), so no cd/PYTHONPATH is needed here — the venv's own python already
        # resolves the package.
        "${AGENT_VENV}/bin/python" -m weapon_detection_agent.detection.cutover_preflight 2>&1
    )"
    settings_exit=$?
    set -e
    if [[ "${settings_exit}" -eq 0 ]]; then
        ok "settings + profile: cutover configuration is valid"
        echo "${settings_output}" | sed 's/^/[preflight]   /'
    else
        fail "settings/profile: cutover_preflight exited ${settings_exit}"
        echo "${settings_output}" | sed 's/^/[preflight]   /' >&2
    fi
fi

# --- 2. Executable: the Bridge launcher itself must exist and be executable ------------------------
if [[ -x "${BRIDGE_RUN_SH}" ]]; then
    ok "executable: ${BRIDGE_RUN_SH} exists and is executable"
else
    fail "executable: ${BRIDGE_RUN_SH} missing or not executable"
fi

# --- 3. Bridge imports, as the service account, via the exact process-local PYTHONPATH run.sh uses -
if [[ ! -x "${BRIDGE_VENV}/bin/python" ]]; then
    fail "bridge-imports: Bridge venv interpreter not found: ${BRIDGE_VENV}/bin/python (run deploy-bridge.sh)"
else
    set +e
    import_output="$(
        sudo -u "${SERVICE_USER}" -H env PYTHONPATH="${BRIDGE_APP}" "${BRIDGE_VENV}/bin/python" -c "
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst
Gst.init(None)
import pyds
assert hasattr(pyds, 'NvDsObjectMeta')
assert hasattr(pyds, 'NvDsFrameMeta')
gi.require_version('GstRtspServer', '1.0')
from gi.repository import GstRtspServer
import deepstream_bridge
print('gi/Gst/pyds/GstRtspServer/deepstream_bridge all import correctly')
" 2>&1
    )"
    import_exit=$?
    set -e
    if [[ "${import_exit}" -eq 0 ]]; then
        ok "bridge-imports: ${import_output}"
    else
        fail "bridge-imports: import check failed as ${SERVICE_USER} (exit ${import_exit})"
        echo "${import_output}" | sed 's/^/[preflight]   /' >&2
    fi
fi

# --- 4. Config/profile files readable by the service account ---------------------------------------
for f in "${ROOT_DIR}/config/deepstream/deepstream-app.txt"; do
    if sudo -u "${SERVICE_USER}" test -r "${f}"; then
        ok "config: ${f} readable by ${SERVICE_USER}"
    else
        fail "config: ${f} missing or not readable by ${SERVICE_USER}"
    fi
done

# --- 5. Socket-directory access: mode 0700, and the service account can create/remove a TEMP marker
# in it (never the real detection.sock — production socket is never touched by this script) --------
if [[ -d "${RUNTIME_DIR}" ]]; then
    mode="$(stat -c '%a' "${RUNTIME_DIR}")"
    if [[ "${mode}" == "700" ]]; then
        ok "runtime-dir: ${RUNTIME_DIR} mode is 0700"
    else
        fail "runtime-dir: ${RUNTIME_DIR} mode is 0${mode}, expected 0700"
    fi

    marker="${RUNTIME_DIR}/.preflight-write-check-$$"
    if sudo -u "${SERVICE_USER}" sh -c "touch '${marker}' && rm -f '${marker}'"; then
        ok "runtime-dir: ${SERVICE_USER} can create and remove a file in ${RUNTIME_DIR}"
    else
        fail "runtime-dir: ${SERVICE_USER} could not create/remove a test file in ${RUNTIME_DIR}"
    fi
else
    fail "runtime-dir: ${RUNTIME_DIR} does not exist"
fi

# --- 6. Protocol compatibility: the wire-framing constants match between the two staged copies -----
agent_protocol="${AGENT_DIR}/src/weapon_detection_agent/detection/protocol.py"
bridge_protocol="${BRIDGE_APP}/deepstream_bridge/protocol.py"
if [[ -r "${agent_protocol}" && -r "${bridge_protocol}" ]]; then
    protocol_ok=1
    for const in FRAME_LENGTH_PREFIX_BYTES MAX_FRAME_BYTES; do
        agent_value="$(grep -E "^${const} = " "${agent_protocol}" | head -n1 | sed 's/.* = //')"
        bridge_value="$(grep -E "^${const} = " "${bridge_protocol}" | head -n1 | sed 's/.* = //')"
        if [[ -z "${agent_value}" || "${agent_value}" != "${bridge_value}" ]]; then
            fail "protocol: ${const} differs (agent=${agent_value:-<missing>} bridge=${bridge_value:-<missing>})"
            protocol_ok=0
        fi
    done
    agent_schema="$(grep -E '^SUPPORTED_SCHEMA_VERSION = ' "${agent_protocol}" | sed 's/.* = //')"
    bridge_schema="$(grep -E '^SCHEMA_VERSION = ' "${bridge_protocol}" | sed 's/.* = //')"
    if [[ -z "${agent_schema}" || "${agent_schema}" != "${bridge_schema}" ]]; then
        fail "protocol: schema version differs (agent=${agent_schema:-<missing>} bridge=${bridge_schema:-<missing>})"
        protocol_ok=0
    fi
    [[ "${protocol_ok}" -eq 1 ]] && ok "protocol: frame prefix/max-length/schema-version constants agree"
else
    fail "protocol: could not read one or both staged protocol.py files"
fi

# --- 7. RTSP port 8554 currently occupied by the existing deepstream-app (never bound here) --------
if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -q ":${PRODUCTION_RTSP_PORT} "; then
    ok "rtsp-port: production port ${PRODUCTION_RTSP_PORT} is currently occupied (by the existing deepstream-app)"
    warn "rtsp-port: T-91 must stop the existing DeepStream child before starting the Bridge — a second process cannot bind the same port concurrently"
else
    warn "rtsp-port: production port ${PRODUCTION_RTSP_PORT} does not appear occupied — confirm deepstream-app is actually running before cutover"
fi

# --- 8. Multicast loopback route -------------------------------------------------------------------
if ip route show 2>/dev/null | grep -q "${MULTICAST_GROUP}.*dev lo"; then
    ok "multicast-route: ${MULTICAST_GROUP} routed via dev lo"
else
    fail "multicast-route: ${MULTICAST_GROUP} via dev lo not found (RTSP output will hang on DESCRIBE)"
fi

if command -v systemctl >/dev/null 2>&1; then
    if systemctl is-active --quiet "${ROUTE_SERVICE}"; then
        ok "route-service: ${ROUTE_SERVICE} is active"
    else
        fail "route-service: ${ROUTE_SERVICE} is not active"
    fi
fi

echo "==============================================="
if [[ "${FAILURES}" -eq 0 ]]; then
    echo "=== preflight PASSED — cutover prerequisites satisfied ==="
    exit 0
else
    echo "=== preflight FAILED (${FAILURES} check(s)) — do NOT cut over ===" >&2
    exit 1
fi

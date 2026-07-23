#!/usr/bin/env bash
#
# Weapon Detection Jetson Agent — verification helper (IP-02 T-41, §14).
#
# Safe, read-only checks against a deployed Agent. It NEVER prints the shared secret or a full
# SQLite row; it may report the public Device ID and activation timestamps only (IP-02 §15/§21
# log/query the Device ID). SQLite is queried through the venv Python, so the sqlite3 CLI is not
# required. Exits non-zero if any check fails.

set -Eeuo pipefail

readonly SERVICE_NAME="weapon-detection-agent"
readonly SERVICE_USER="weapon-detection"
readonly ROOT_DIR="/opt/weapon-detection"
readonly VENV_PY="${ROOT_DIR}/venv/bin/python"
readonly DB_FILE="${ROOT_DIR}/database/agent.db"
readonly KEY_FILE="${ROOT_DIR}/config/activation-key"
readonly ENV_FILE="/etc/weapon-detection-agent/agent.env"
readonly UNIT_DEST="/etc/systemd/system/${SERVICE_NAME}.service"

pass=0; fail=0
ok()   { printf '  [ OK ] %s\n' "$*"; pass=$((pass+1)); }
bad()  { printf '  [FAIL] %s\n' "$*"; fail=$((fail+1)); }
info() { printf '  [INFO] %s\n' "$*"; }

echo "== Weapon Detection Agent verification =="

# 1. Unit installed
[[ -f "${UNIT_DEST}" ]] && ok "systemd unit installed (${UNIT_DEST})" || bad "systemd unit missing"

# 2. Service enabled
if systemctl is-enabled --quiet "${SERVICE_NAME}" 2>/dev/null; then
    ok "service enabled (starts on boot)"
else
    bad "service not enabled ($(systemctl is-enabled "${SERVICE_NAME}" 2>&1 || true))"
fi

# 3. Service active
if systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
    ok "service active"
else
    bad "service not active ($(systemctl is-active "${SERVICE_NAME}" 2>&1 || true))"
fi

# 4/5. Exactly one main Uvicorn worker, running as the service user
mapfile -t uvpids < <(pgrep -f 'uvicorn weapon_detection_agent.main:app' || true)
if [[ "${#uvpids[@]}" -eq 1 ]]; then
    ok "exactly one Uvicorn process (pid ${uvpids[0]})"
    procuser="$(ps -o user= -p "${uvpids[0]}" | tr -d ' ')"
    [[ "${procuser}" == "${SERVICE_USER}" ]] \
        && ok "process runs as ${SERVICE_USER}" \
        || bad "process runs as '${procuser}', expected ${SERVICE_USER}"
elif [[ "${#uvpids[@]}" -eq 0 ]]; then
    bad "no Uvicorn process found"
else
    bad "expected exactly one Uvicorn process, found ${#uvpids[@]}"
fi

# 6. Env file permissions (0600) and no secret in it
if [[ -f "${ENV_FILE}" ]]; then
    mode="$(stat -c '%a' "${ENV_FILE}")"
    [[ "${mode}" == "600" ]] && ok "env file mode 0600" || bad "env file mode ${mode} (expected 600)"
    if grep -Eq '^\s*WDA_ACTIVATION_KEY' "${ENV_FILE}"; then
        bad "env file contains WDA_ACTIVATION_KEY (must never hold a secret)"
    else
        ok "env file holds no Activation Key"
    fi
else
    bad "env file ${ENV_FILE} missing"
fi

# 7. Agent directory permissions
for d in "${ROOT_DIR}:750" "${ROOT_DIR}/config:700" "${ROOT_DIR}/database:700" "${ROOT_DIR}/logs:750"; do
    path="${d%%:*}"; want="${d##*:}"
    if [[ -d "${path}" ]]; then
        got="$(stat -c '%a' "${path}")"
        [[ "${got}" == "${want}" ]] && ok "${path} mode ${want}" || bad "${path} mode ${got} (expected ${want})"
    else
        bad "${path} missing"
    fi
done

# 8. SQLite database exists (0600)
if [[ -f "${DB_FILE}" ]]; then
    dbmode="$(stat -c '%a' "${DB_FILE}")"
    [[ "${dbmode}" == "600" ]] && ok "agent.db mode 0600" || bad "agent.db mode ${dbmode} (expected 600)"
else
    info "agent.db not present yet (no activation has occurred)"
fi

# 9/10/11. DeviceIdentity: exactly one row; ConfigCache untouched (0 rows). Queried via venv Python.
#          Never selects or prints ProtectedSharedSecret.
if [[ -x "${VENV_PY}" && -f "${DB_FILE}" ]]; then
    if report="$("${VENV_PY}" - "${DB_FILE}" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1]); c.row_factory = sqlite3.Row
try:
    ident = c.execute(
        "SELECT DeviceId, ActivatedAt, LastActivatedAt FROM DeviceIdentity"
    ).fetchall()
    cfg = c.execute("SELECT COUNT(*) AS n FROM ConfigCache").fetchone()["n"]
finally:
    c.close()
print(f"IDENTITY_ROWS={len(ident)}")
print(f"CONFIGCACHE_ROWS={cfg}")
if len(ident) == 1:
    r = ident[0]
    print(f"DEVICE_ID={r['DeviceId']}")
    print(f"ACTIVATED_AT={r['ActivatedAt']}")
    print(f"LAST_ACTIVATED_AT={r['LastActivatedAt']}")
PY
)"; then
        idrows="$(sed -n 's/^IDENTITY_ROWS=//p' <<<"${report}")"
        cfgrows="$(sed -n 's/^CONFIGCACHE_ROWS=//p' <<<"${report}")"
        [[ "${idrows}" == "1" ]] && ok "exactly one DeviceIdentity row" || bad "DeviceIdentity has ${idrows} row(s) (expected 1)"
        [[ "${cfgrows}" == "0" ]] && ok "ConfigCache untouched (0 rows, OI-2)" || bad "ConfigCache has ${cfgrows} row(s) (expected 0)"
        did="$(sed -n 's/^DEVICE_ID=//p' <<<"${report}")"
        aat="$(sed -n 's/^ACTIVATED_AT=//p' <<<"${report}")"
        lat="$(sed -n 's/^LAST_ACTIVATED_AT=//p' <<<"${report}")"
        [[ -n "${did}" ]] && info "Device ID: ${did}"
        [[ -n "${aat}" ]] && info "ActivatedAt: ${aat}  LastActivatedAt: ${lat}"
    else
        bad "could not query the SQLite store"
    fi
else
    info "skipping DeviceIdentity check (no venv Python or no database yet)"
fi

# 12. Activation-key file absent after a successful file-based activation
if [[ -e "${KEY_FILE}" ]]; then
    info "activation-key file still present (activation pending, or an ambiguous/failed attempt)"
else
    ok "activation-key file absent (consumed after activation, or none provisioned)"
fi

# 13. Backend health reachable over Tailscale (URL read from the env file; no Agent endpoint exists)
if [[ -f "${ENV_FILE}" ]]; then
    base="$(sed -n 's/^\s*WDA_BACKEND_BASE_URL=//p' "${ENV_FILE}" | tail -n1 | tr -d '"' | tr -d "'")"
    if [[ -n "${base}" ]]; then
        if curl -fsS --max-time 8 "${base%/}/api/v1/health" >/dev/null 2>&1; then
            ok "Backend health reachable at ${base%/}/api/v1/health"
        else
            bad "Backend health NOT reachable at ${base%/}/api/v1/health"
        fi
    else
        info "WDA_BACKEND_BASE_URL not set in env file; skipping health check"
    fi
fi
# Note: the Agent exposes NO health/operational endpoint (OI-3) — readiness is proven by the checks
# above (unit active, one worker as the service user), never by probing an Agent HTTP route.

echo
echo "== ${pass} passed, ${fail} failed =="
[[ "${fail}" -eq 0 ]]

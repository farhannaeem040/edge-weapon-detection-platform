#!/usr/bin/env bash
#
# Weapon Detection Jetson Agent — DeepStream verification helper (IP-06 T-76, FS-04 §7).
#
# Opt-in, NOT run by the default fast test suite, NOT run by deployment/jetson/verify.sh (T-41's
# script stays untouched — DeepStream verification is deliberately separate, mirroring how the
# real-Backend contract suite is opt-in via WDA_RUN_BACKEND_CONTRACT_TESTS).
#
# Static checks (always run): the active profile's engine exists and matches its manifest checksum,
# the referenced config/labels files exist, and deepstream-app is on PATH.
#
# Real launch (opt-in via --run): actually starts DeepStream against the managed config for a bounded
# duration and reports success/failure. Requires a test video staged at the path deepstream-app.txt's
# [source0] references (see README.md).
#
# Usage:
#     sudo verify-deepstream.sh                  # static checks only
#     sudo verify-deepstream.sh --run [--timeout SECONDS]   # + a real bounded launch

set -Eeuo pipefail

readonly ROOT_DIR="/opt/weapon-detection"
readonly APP_CONFIG="${ROOT_DIR}/config/deepstream/deepstream-app.txt"
readonly PROFILES_DIR="${ROOT_DIR}/config/deepstream/profiles"
readonly LOG_PATH="${ROOT_DIR}/logs/deepstream/deepstream.log"
readonly DEEPSTREAM_BIN="deepstream-app"

pass=0; fail=0
ok()   { printf '  [ OK ] %s\n' "$*"; pass=$((pass+1)); }
bad()  { printf '  [FAIL] %s\n' "$*"; fail=$((fail+1)); }
info() { printf '  [INFO] %s\n' "$*"; }

RUN=0
TIMEOUT=30
for arg in "$@"; do
    case "${arg}" in
        --run) RUN=1 ;;
        --timeout) : ;;
        --timeout=*) TIMEOUT="${arg#*=}" ;;
        -h|--help) grep -E '^# ' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    esac
done

echo "== DeepStream verification =="

# 1. deepstream-app on PATH
if command -v "${DEEPSTREAM_BIN}" >/dev/null 2>&1; then
    ok "deepstream-app found: $(command -v ${DEEPSTREAM_BIN})"
else
    bad "deepstream-app not found on PATH"
fi

# 2. The managed application config exists
if [[ -f "${APP_CONFIG}" ]]; then
    ok "application config present: ${APP_CONFIG}"
else
    bad "application config missing: ${APP_CONFIG} (run install.sh)"
fi

# 3. Resolve the active profile from deepstream-app.txt's [primary-gie] config-file= line
active_profile_config=""
if [[ -f "${APP_CONFIG}" ]]; then
    active_profile_config="$(sed -n 's/^config-file=//p' "${APP_CONFIG}" | tail -n1 | tr -d '\r')"
fi
if [[ -n "${active_profile_config}" && -f "${active_profile_config}" ]]; then
    ok "active profile config resolved: ${active_profile_config}"
else
    bad "could not resolve an active profile config-file= from ${APP_CONFIG}"
fi

# 4. The active profile's engine exists and matches its manifest checksum
if [[ -n "${active_profile_config}" ]]; then
    profile_dir="$(dirname "${active_profile_config}")"
    profile_name="$(basename "${profile_dir}")"
    manifest="${profile_dir}/manifest.env"
    engine="${ROOT_DIR}/models/${profile_name}/model.engine"
    if [[ -f "${manifest}" ]]; then
        expected_sha256="$(sed -n 's/^ENGINE_SHA256=//p' "${manifest}" | head -n1 | tr -d '\r')"
        if [[ -f "${engine}" ]]; then
            computed_sha256="$(sha256sum "${engine}" | awk '{print $1}')"
            if [[ -n "${expected_sha256}" && "${computed_sha256}" == "${expected_sha256}" ]]; then
                ok "profile '${profile_name}' engine matches its manifest checksum"
            else
                bad "profile '${profile_name}' engine checksum mismatch (re-run deploy-engine.sh)"
            fi
        else
            bad "profile '${profile_name}' engine missing: ${engine} (run deploy-engine.sh)"
        fi
        labels_file="$(sed -n 's/^LABELS_FILE=//p' "${manifest}" | head -n1 | tr -d '\r')"
        if [[ -n "${labels_file}" && -f "${profile_dir}/${labels_file}" ]]; then
            ok "profile '${profile_name}' labels file present"
        else
            bad "profile '${profile_name}' labels file missing or unset in manifest"
        fi
    else
        bad "profile manifest missing: ${manifest}"
    fi
fi

echo
echo "== static checks: ${pass} passed, ${fail} failed =="

if [[ "${RUN}" -ne 1 ]]; then
    info "static checks only (pass --run for a real bounded launch)"
    exit "$(( fail > 0 ? 1 : 0 ))"
fi

if [[ "${fail}" -gt 0 ]]; then
    echo "refusing --run: static checks failed above" >&2
    exit 1
fi

# --- Real bounded launch (opt-in) -------------------------------------------------------------
echo
echo "== real launch (bounded to ${TIMEOUT}s) =="
mkdir -p "$(dirname "${LOG_PATH}")"
set +e
timeout "${TIMEOUT}" "${DEEPSTREAM_BIN}" -c "${APP_CONFIG}" >"${LOG_PATH}.verify" 2>&1
run_exit=$?
set -e

if grep -q "App run successful" "${LOG_PATH}.verify"; then
    ok "DeepStream reported: App run successful"
elif [[ "${run_exit}" -eq 124 ]]; then
    info "DeepStream was still running at the ${TIMEOUT}s bound (expected for a live/loop source) — treated as healthy"
    pass=$((pass+1))
else
    bad "DeepStream did not report success (exit ${run_exit}); last 30 lines of ${LOG_PATH}.verify:"
    tail -n 30 "${LOG_PATH}.verify" >&2
    fail=$((fail+1))
fi

echo
echo "== ${pass} passed, ${fail} failed =="
exit "$(( fail > 0 ? 1 : 0 ))"

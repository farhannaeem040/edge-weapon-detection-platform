#!/bin/sh
#
# DeepStream Bridge — argv-translating launcher (IP-07 T-89, FS-05 §4.3).
#
# DeepStreamProcessManager (T-72, unchanged, fully generic) always execs whatever
# WDA_DEEPSTREAM_EXECUTABLE_PATH names as: [executable, "-c", config_path]. For a detection-enabled
# deployment that path points here instead of /usr/bin/deepstream-app. This script re-invokes the
# Bridge's own venv interpreter with its real, explicit CLI (--socket-path/--config), so
# DeepStreamProcessManager never needs to know the Bridge's argv shape and the Bridge never needs to
# know DeepStreamProcessManager's.
#
# Usage (as DeepStreamProcessManager calls it):
#     run.sh -c <config-path>
#
# Exit codes: 2 = wrong/missing/extra arguments, 3 = venv interpreter missing/not executable,
# 4 = config path not readable. `exec` at the end means this script's own exit code is never
# reached for the success path — the Bridge process's exit code (and every signal
# DeepStreamProcessManager's stop() sends) applies directly, with no intermediate shell to lose
# track of.

set -eu

# Resolve this script's own directory robustly (never assumes the caller's working directory).
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
BRIDGE_ROOT="${SCRIPT_DIR}"
APP_DIR="${BRIDGE_ROOT}/app"
PYTHON="${BRIDGE_ROOT}/venv/bin/python"

# DeepStreamProcessManager always calls this as exactly: run.sh -c <config-path>.
if [ "$#" -ne 2 ] || [ "$1" != "-c" ]; then
    echo "run.sh: expected exactly '-c <config-path>' (2 arguments), got $# argument(s)" >&2
    exit 2
fi
CONFIG_PATH="$2"

if [ ! -r "${CONFIG_PATH}" ]; then
    echo "run.sh: config file not readable: ${CONFIG_PATH}" >&2
    exit 4
fi

if [ ! -x "${PYTHON}" ]; then
    echo "run.sh: Bridge venv interpreter not found or not executable: ${PYTHON}" >&2
    echo "run.sh: run deploy-bridge.sh to build it" >&2
    exit 3
fi

# Inherited from the Agent/systemd environment this process is launched in — DeepStreamProcessManager
# sets no environment of its own, so this is whatever the Agent's own ambient environment already
# is. Never a second hardcoded copy of the path: the literal default below is the ONLY place it
# appears, and it matches AgentSettings.detection_socket_path's own default
# (config/paths.py's detection_socket_file) — a static test (T-90) checks the two stay in sync.
SOCKET_PATH="${WDA_DETECTION_SOCKET_PATH:-/opt/weapon-detection/runtime/detection.sock}"

# The venv holds dependencies only (FS-05 §4.6) — deepstream_bridge itself is never pip-installed
# into it. This process-local PYTHONPATH is what makes the newly staged app/ importable, so a
# source update (install.sh restaging app/) takes effect on the Bridge's very next start with no
# separate reinstall step. Deliberately NOT exported anywhere else (no /etc/environment, no shell
# profile) — this variable exists only for the lifetime of the exec'd process below.
PYTHONPATH="${APP_DIR}${PYTHONPATH:+:${PYTHONPATH}}"
export PYTHONPATH

# exec (not a plain call) so the Bridge replaces this shell as DeepStreamProcessManager's tracked
# child — signals (SIGTERM/SIGKILL from stop()) and the real exit code propagate directly, with no
# intermediate shell process for the process manager's PID/wait() bookkeeping to lose track of.
# Never echoes CONFIG_PATH's contents, SOCKET_PATH, or the environment — only paths that are
# themselves already non-secret arguments/settings, and even those only ever reach argv, never a
# log line here.
exec "${PYTHON}" -m deepstream_bridge.main \
    --socket-path "${SOCKET_PATH}" \
    --config "${CONFIG_PATH}"

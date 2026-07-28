# DeepStream Bridge (IP-07 T-88/T-89, FS-05)

A fully separate application from the Jetson Agent (`weapon_detection_agent`) — never imported by
it, never importing it. See `specs/features/FS-05-detection-event-bridge.md` §4.6 for the binding
architectural rules this deployment layout implements.

## Architecture boundary

```text
DeepStream Bridge (this application)          Jetson Agent (weapon_detection_agent)
------------------------------------          --------------------------------------
GStreamer/DeepStream pipeline construction     Bridge PROCESS supervision only
TensorRT inference (profile's infer-config.txt) (DeepStreamProcessManager, unchanged)
pyds metadata extraction (pad probe)           Message validation, class resolution
OSD (on-screen bounding boxes)                 Cooldown/duplicate suppression
Processed RTSP output                          Event ID/device identity, SQLite persistence
Non-blocking Unix-socket publishing            --
(raw detection facts only)
```

Nothing on the left imports `weapon_detection_agent`, `fastapi`, or any Agent dependency. Nothing on
the right imports `pyds`, `pgi`, or `gi`/`Gst`. The Unix domain socket wire protocol
(`deepstream_bridge/protocol.py` mirrored by `weapon_detection_agent.detection.protocol`) is the
*entire* interface between the two — enforced structurally by separate deployable units and separate
Python environments, not merely by convention within one process.

- **Python version:** the venv runs **system Python 3.8** (`python3.8`, DeepStream 6.2 / Ubuntu 20.04
  / JetPack 5.1.2's native interpreter version) — never the Agent's own Python 3.11 venv.
- **`pyds` version:** pinned to **1.1.6** (`requirements.lock`) — the `deepstream_python_apps`
  release built against DeepStream SDK 6.2 / Python 3.8 aarch64. Do not upgrade without first
  confirming a matching DeepStream SDK version bump (see `../README.md`'s "Installing `pyds`"
  section for the full history of this pin).

## Deployment layout

```text
/opt/weapon-detection/deepstream-bridge/
    app/                  # the deepstream_bridge package source — staged by install.sh, never
                           # pip-installed into venv/
    venv/                 # Python 3.8 virtualenv, --system-site-packages, DEPENDENCIES ONLY
    run.sh                # argv-translating launcher DeepStreamProcessManager execs
    deploy-bridge.sh       # operator-run venv build/rebuild script (never run by install.sh)
    requirements.lock      # pinned, hash-verified dependency manifest (pyds, pgi)
    README.md              # this file
```

`app/`, `run.sh`, `deploy-bridge.sh`, `requirements.lock`, and this `README.md` are staged by
`install.sh` (the repository's committed source, rsynced with `venv/` explicitly excluded — restaging
source on every `update.sh` run never touches, deletes, or rebuilds an already-provisioned venv).
`venv/` is built **only** by `deploy-bridge.sh`, run manually by an operator — exactly like
`deploy-engine.sh` is never invoked automatically.

## Building/rebuilding the venv: `deploy-bridge.sh`

```bash
sudo /opt/weapon-detection/deepstream-bridge/deploy-bridge.sh
```

What it does, in order: verifies aarch64 + `python3.8`; if `venv/` already exists and *actually
imports* `gi`/`Gst`/`pyds`/`pyds.NvDsObjectMeta`/`pyds.NvDsFrameMeta` correctly, it leaves it
untouched and exits 0 (safe to re-run after every `install.sh` restage — it is a no-op unless
something is actually broken or missing); otherwise (or on `--force`) it removes any existing
`venv/`, creates a fresh one with `python3.8 -m venv --system-site-packages`, installs
`requirements.lock` with `pip install --no-deps --require-hashes` (hash-checked — nothing installs
if the pinned wheel/sdist doesn't match byte-for-byte), re-verifies the same five imports, confirms
`deepstream_bridge` itself was **not** installed into the venv's `site-packages`, confirms the staged
`app/` is importable through the exact process-local `PYTHONPATH` mechanism `run.sh` uses, then
`chown -R weapon-detection:weapon-detection` the whole `deepstream-bridge/` tree. It never prints an
environment variable's value or any credential (there are none in this application's configuration
surface to begin with).

### Rebuilding with `--force`

```bash
sudo /opt/weapon-detection/deepstream-bridge/deploy-bridge.sh --force
```

Without `--force`, a **working** existing venv is left completely alone — `deploy-bridge.sh` is safe
to run after every source update and will not silently rebuild a venv that already passes every
import check. `--force` unconditionally removes `venv/` and rebuilds from `requirements.lock` — use
it after a `requirements.lock` change, a suspected corrupted venv, or a system Python upgrade. A venv
that is present but *fails* its import checks is always rebuilt automatically (no flag needed) since
there is nothing working to preserve.

## How `run.sh` is invoked

`DeepStreamProcessManager` (Agent-side, unchanged by this feature) always execs whichever path
`WDA_DEEPSTREAM_EXECUTABLE_PATH` names as exactly `[executable, "-c", config_path]` — it has no
concept of the Bridge's real CLI. For a detection-enabled deployment, that path is
`/opt/weapon-detection/deepstream-bridge/run.sh`, which:

1. rejects anything other than exactly `-c <config-path>` (non-zero exit, no process spawned);
2. verifies the config path is readable and the venv interpreter exists/is executable;
3. resolves the detection socket path from `WDA_DETECTION_SOCKET_PATH` (inherited from the Agent's
   own ambient environment — the same one `systemd`/`agent.env` already populates), falling back to
   the one literal default `/opt/weapon-detection/runtime/detection.sock` — the same default
   `AgentSettings.detection_socket_path` uses;
4. exports a **process-local** `PYTHONPATH=<bridge-root>/app` so `deepstream_bridge` is importable
   without any install step — never a global shell profile or `/etc/environment`;
5. `exec`s the venv's `python -m deepstream_bridge.main --socket-path <resolved> --config
   <config-path>`, replacing the shell so DeepStreamProcessManager's PID/signal/exit-code tracking
   sees the real Bridge process directly.

## Environment variables

| Variable | Read by | Purpose |
|---|---|---|
| `WDA_DETECTION_SOCKET_PATH` | `run.sh` (inherited, not exported by it) | Overrides the detection socket path; falls back to `/opt/weapon-detection/runtime/detection.sock` if unset. |

`run.sh` reads no other environment variable and sets none beyond the process-local `PYTHONPATH`
described above. It never echoes an environment variable's value or the resolved RTSP/socket paths
to any log — only non-zero exit codes and short, path-naming (never value-echoing) error messages on
failure.

## Dependency verification

`deploy-bridge.sh` is itself the dependency-verification step (see above) — a broken venv is caught
at build time, not discovered later as a mysterious `DeepStreamProcessManager` launch failure. To
re-run the same check manually at any time without rebuilding anything:

```bash
sudo -u weapon-detection -H /opt/weapon-detection/deepstream-bridge/venv/bin/python -c "
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst
Gst.init(None)
import pyds
print('pyds:', pyds.__file__)
print('GStreamer:', Gst.version_string())
print('NvDsObjectMeta:', hasattr(pyds, 'NvDsObjectMeta'))
print('NvDsFrameMeta:', hasattr(pyds, 'NvDsFrameMeta'))
"
```

## Isolated testing

- **Offline unit tests** (no hardware, run from a dev machine or the Jetson with any Python 3):
  `pytest deployment/jetson/deepstream/bridge/tests` (see `pyproject.toml` in this directory — this
  package uses its own `pytest`/`ruff`/`mypy` invocation, never the Agent's `agent/pyproject.toml`
  gates, since `deepstream_bridge` is never imported by `agent/src/`).
- **Jetson isolated smoke tests** (IP-07 T-88 Stage A/B, T-89 Stage C): copy only this directory's
  source to a temporary location (e.g. `/tmp/wda-t88-validation`), never the live `/opt` tree, and
  run against a temporary Unix socket / non-production RTSP port (e.g. `8555`, mount `/t88-test`) —
  never production `detection.sock`, port `8554`, or mount `/ds-test`. Full procedure and results are
  recorded in the IP-07 task reports, not duplicated here.

## Rollback to `/usr/bin/deepstream-app`

Enabling the Bridge is exactly one operator-made settings change:
`WDA_DEEPSTREAM_EXECUTABLE_PATH=/opt/weapon-detection/deepstream-bridge/run.sh` alongside
`WDA_DETECTION_EVENTS_ENABLED=true` in `/etc/weapon-detection-agent/agent.env`. Reverting either line
alone (or both) returns the deployment to the IP-06 T-77-proven binary-only behavior — no redeploy,
no code change, no data loss. `/usr/bin/deepstream-app` is never removed, modified, or made unusable
by anything in this directory; it remains the default and stays fully launchable throughout.

```bash
# Edit /etc/weapon-detection-agent/agent.env:
#   WDA_DEEPSTREAM_EXECUTABLE_PATH=/usr/bin/deepstream-app
#   WDA_DETECTION_EVENTS_ENABLED=false
sudo systemctl restart weapon-detection-agent
```

## Uninstalling only the Bridge

The Bridge's entire footprint can be removed with no effect on the Agent, the DeepStream binary
path, or Device Identity — as long as `WDA_DEEPSTREAM_EXECUTABLE_PATH` has already been reverted (or
was never pointed here):

```bash
sudo systemctl stop weapon-detection-agent   # only if the Bridge is currently the active executable
sudo rm -rf /opt/weapon-detection/deepstream-bridge
```

Nothing under `/opt/weapon-detection/database`, `/opt/weapon-detection/config`, or
`/opt/weapon-detection/runtime` is touched by this — the Bridge owns nothing outside its own
directory (`app/`/`venv/`/`run.sh`/`deploy-bridge.sh`/`requirements.lock`/`README.md`).

## Why `runtime/` must never contain a dependency

`/opt/weapon-detection/runtime/` is reserved **exclusively** for transient runtime objects — today,
the detection Unix domain socket (`detection.sock`), the Agent's own first writer for that directory
(IP-07 T-82). It is not a package/dependency location for anything, including this application:
placing `pyds`/`pgi` there was a real, corrected mistake during this feature's original availability
proof (see `../README.md`'s "Installing `pyds`" → "Migration to the Bridge venv" section for the full
history) — a socket file and a Python package have entirely different lifecycles (the socket is
created and destroyed by `DetectionIngestHandler.start()`/`stop()` on every Agent run; a dependency
must survive across runs and restarts). Mixing the two would make `runtime/`'s cleanup/permission
model ambiguous and was reverted specifically because of that. The Bridge's own dependencies live
only in `venv/`, in this directory, built and owned exclusively by `deploy-bridge.sh`.

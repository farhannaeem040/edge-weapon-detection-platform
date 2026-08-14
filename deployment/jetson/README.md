# Jetson Agent — native systemd deployment (IP-02 T-41)

> In plain terms: these are the scripts used to install and run the [Agent](../../agent/README.md)
> on the physical Jetson device, so it starts automatically and keeps running as a background
> service.

This directory installs the **already-delivered** Weapon Detection Jetson Agent natively on the
Jetson under **systemd**. The Agent is **not** containerized (it needs NVIDIA/DeepStream/camera/
subprocess access in later milestones), and systemd manages **only** the Agent — never DeepStream
(ARCH-CON-002, ADR-006).

This milestone deploys the **activation foundation** only. There is **no** DeepStream, detection,
alerting, heartbeat, health endpoint, command API, siren, WebRTC, or configuration writing here
(all excluded — IP-02 §2.2, OI-1–OI-3).

## Contents

| File | Purpose |
|------|---------|
| `weapon-detection-agent.service` | systemd unit (one worker, loopback, unprivileged, no secrets) |
| `agent.env.example` | Non-secret env template installed to `/etc/weapon-detection-agent/agent.env` |
| `install.sh` | Idempotent installer (user, layout, venv, package, unit, enable) |
| `set-activation-key.sh` | Secure out-of-band Activation Key provisioning |
| `verify.sh` | Read-only post-install verification |
| `update.sh` | Update code + venv, preserving all data |
| `uninstall.sh` | Remove service/app; preserves data unless `--purge` |
| `deploy.ps1` | Windows one-command deploy over SSH |
| `deepstream/` | DeepStream process supervision (IP-06) — generic, profile-based model deployment (`deploy-engine.sh`), verification (`verify-deepstream.sh`), and config templates. See `deepstream/README.md`. |

## Prerequisites

- A Jetson reachable over Tailscale with SSH **key** auth configured, and a user with `sudo`.
- The central platform running and reachable from the Jetson:
  `curl http://100.77.146.5:8080/api/v1/health` returns `{"success":true,...}`.
- **Python ≥ 3.10.** The delivered Agent declares `requires-python = ">=3.10"`.

### Tailscale addresses (this deployment)

| Host | Tailscale IP |
|------|--------------|
| Central Windows server | `100.77.146.5` (platform `http://100.77.146.5:8080`) |
| Jetson (`jetson`, user `farhan`) | `100.98.226.80` |

### Python note — this Jetson runs JetPack 5 / Ubuntu 20.04

IP-02 assumed *JetPack 6 / Ubuntu 22.04 / Python 3.10*. The actual device is **JetPack 5.1.2 /
Ubuntu 20.04 / Python 3.8** (aarch64), which cannot satisfy the Agent's `>=3.10` floor. deadsnakes
publishes no arm64 packages, so `install.sh` **builds CPython 3.11 from source** (`make altinstall`
to `/usr/local`, leaving system `python3.8` intact) and creates the venv on it — a recorded
**environment deviation from IP-02 D-7** ("system Python"). No Agent code changes; `requires-python`
stays `>=3.10`. The source build takes many minutes on the Jetson. If a native ≥3.10 interpreter
(with venv/ssl/sqlite3) is already present, the installer uses it and skips the build.

## Windows deployment command (recommended)

From the repository root on the Windows server:

```powershell
powershell -ExecutionPolicy Bypass -File deployment/jetson/deploy.ps1
```

This packages `agent/` + `deployment/jetson/` (excluding `.git`, `.venv`, `*.db`, `*.log`, caches,
`.env`), copies them to a unique temp dir on the Jetson, and runs the installer through interactive
`sudo` (you enter the sudo password when prompted). Override the target with
`-JetsonHost <ip> -User <user> -IdentityFile <path>`; use `-StageOnly` to copy without installing.

**No Activation Key or secret is ever copied or printed by this script.**

## Manual installation (on the Jetson)

```bash
# from a copy of the repo on the Jetson:
sudo bash deployment/jetson/install.sh
```

The installer, idempotently:

1. requires root; verifies Linux + systemd; ensures Python ≥ 3.10 (on this aarch64 / Ubuntu 20.04
   Jetson it **builds CPython 3.11 from source** via `make altinstall`, since deadsnakes has no arm64
   build and system Python is 3.8) and its `venv`/`ssl`/`sqlite3` modules;
2. creates the unprivileged system user/group **`weapon-detection`** (no password, no login shell,
   no sudo, no Docker; **not** added to `video`/`render` — this milestone uses no device);
3. creates the `/opt/weapon-detection` layout with ADR-008 modes;
4. copies the Agent source to `/opt/weapon-detection/agent`;
5. creates `/opt/weapon-detection/venv` and installs the Agent **runtime** package (not `[dev]`);
6. installs `/etc/weapon-detection-agent/agent.env` **only if absent** (never overwrites a real one);
7. installs and `daemon-reload`s the unit, and **enables** it (starts on boot);
8. **does not start** an unactivated Agent with no key — it prints the next safe commands.

## Installation layout

| Path | Mode | Owner | Purpose |
|------|------|-------|---------|
| `/opt/weapon-detection/` | `0750` | `weapon-detection` | Mutable Agent root |
| `/opt/weapon-detection/agent` | `0755` | `weapon-detection` | Application source |
| `/opt/weapon-detection/venv` | `0755` | `weapon-detection` | Virtual environment |
| `/opt/weapon-detection/config/` | `0700` | `weapon-detection` | Activation-key file |
| `/opt/weapon-detection/config/activation-key` | `0600` | `weapon-detection` | The key (deleted on use) |
| `/opt/weapon-detection/database/` | `0700` | `weapon-detection` | `agent.db` (`0600`) |
| `/opt/weapon-detection/logs/` | `0750` | `weapon-detection` | `agent.log` |
| `/etc/weapon-detection-agent/agent.env` | `0600` | `root` | Non-secret env |
| `/etc/systemd/system/weapon-detection-agent.service` | `0644` | `root` | Unit |

## Environment configuration

Edit `/etc/weapon-detection-agent/agent.env` (root, `0600`). Non-secret only:

```ini
WDA_BACKEND_BASE_URL=http://100.77.146.5:8080
WDA_ROOT_PATH=/opt/weapon-detection
WDA_HTTP_TIMEOUT_SECONDS=10
WDA_LOG_LEVEL=INFO
WDA_CREDENTIAL_VALIDATION_INTERVAL_SECONDS=30

# DeepStream process supervision (IP-06) — defaults shown; only override if the layout differs.
# WDA_DEEPSTREAM_ENABLED defaults to false: DeepStream never launches until explicitly enabled here.
WDA_DEEPSTREAM_ENABLED=false
WDA_DEEPSTREAM_EXECUTABLE_PATH=/usr/bin/deepstream-app
WDA_DEEPSTREAM_CONFIG_PATH=/opt/weapon-detection/config/deepstream/deepstream-app.txt
WDA_DEEPSTREAM_WORKING_DIRECTORY=/opt/weapon-detection
WDA_DEEPSTREAM_STOP_TIMEOUT_SECONDS=10
WDA_DEEPSTREAM_RESTART_POLICY=none
WDA_DEEPSTREAM_LOG_PATH=/opt/weapon-detection/logs/deepstream/deepstream.log
WDA_DEEPSTREAM_MODEL_PROFILE=yolov4-fp16
```

**Never** put `WDA_ACTIVATION_KEY` or any secret here — the env file lands in the process
environment/journald. The Activation Key is provisioned separately (below). See
`deepstream/README.md` for deploying a profile's engine and switching the active profile.

## Activation Key provisioning

The key is one-time and out-of-band (ASM-006, D-1). Generate it in the central Dashboard, then:

```bash
sudo systemctl stop weapon-detection-agent
sudo /opt/weapon-detection/agent/deployment/jetson/set-activation-key.sh   # paste when prompted (hidden)
sudo systemctl start weapon-detection-agent
```

The helper reads the key from a **hidden prompt** (or `--stdin`), never from an argument; writes it
**atomically** to `/opt/weapon-detection/config/activation-key` (`0600`, `weapon-detection`); never
echoes it; and refuses to overwrite an existing key without explicit confirmation. The Agent
**deletes** the key file after a successful activation (single-use, BR-003).

## Service commands

```bash
sudo systemctl start   weapon-detection-agent
sudo systemctl stop    weapon-detection-agent
sudo systemctl restart weapon-detection-agent
sudo systemctl status  weapon-detection-agent --no-pager
sudo systemctl enable  weapon-detection-agent    # already enabled by install.sh
```

## Logs

```bash
journalctl -u weapon-detection-agent -n 100 --no-pager
journalctl -u weapon-detection-agent -f
# structured JSON log file:
sudo tail -n 50 /opt/weapon-detection/logs/agent.log
```

No Activation Key or shared secret appears in either destination (structural redaction, §15/MAC-9).

## Verification

```bash
sudo /opt/weapon-detection/agent/deployment/jetson/verify.sh
```

Read-only checks: unit installed/enabled/active; exactly one Uvicorn worker running as
`weapon-detection`; env-file and directory modes; `agent.db` `0600`; exactly one `DeviceIdentity`
row; `ConfigCache` untouched (0 rows, OI-2); activation-key file absent after activation; Backend
health reachable over Tailscale. It prints only the **public Device ID** and activation timestamps —
never the shared secret or a full row. The Agent exposes **no** health endpoint (OI-3); readiness is
proven by the checks above, not by probing an Agent route.

## Update procedure

```bash
sudo bash deployment/jetson/update.sh
```

Stops the service, updates code + venv package, **preserves** the env file, Device Identity,
`ConfigCache`, database, and logs, and restarts **only if it was running before**. It never triggers
reactivation.

## Uninstall procedure and data preservation

```bash
sudo bash deployment/jetson/uninstall.sh          # remove service + app/venv; KEEP all data
sudo bash deployment/jetson/uninstall.sh --purge  # ALSO delete config/database/logs + user (2nd confirm)
```

By default the Device Identity, database, logs, and env file are **preserved** — `--purge` requires
a separate explicit `DELETE` confirmation.

## Restart, reboot, and reactivation behavior

- **Normal restart** (activated, no key): the Agent loads its identity from SQLite, makes **no**
  activation HTTP call, and starts even if the Backend is unreachable (offline start, ARCH-001 §16.3).
- **Reboot:** the unit is enabled, ordered after `network-online.target` and `tailscaled.service`,
  so it starts automatically once Tailscale is up; the existing identity is preserved and no
  reactivation occurs.
- **Reactivation** (MAC-12): regenerate the key in the Dashboard, `stop` the service,
  `set-activation-key.sh`, `start`. The `DeviceId` and original `ActivatedAt` are retained,
  `LastActivatedAt` advances, and the shared secret rotates.

### Credential revocation and the operational lock (IP-05, added 2026-07-21)

Regenerating the Activation Key **immediately revokes** the device's current shared secret on the
Backend. A running Agent detects this via a periodic **credential-validation** call to
`POST /api/v1/device/credentials/validate` (`WDA_CREDENTIAL_VALIDATION_INTERVAL_SECONDS`, default
30s). On a confirmed `401 INVALID_DEVICE_CREDENTIALS` it **clears its local shared secret**, **locks**
itself (stops all operational functionality and the validation loop), and persists a local
`ReactivationRequired` state that **survives restart and reboot**. The Agent never downloads or
receives the replacement key, and never auto-retries activation.

**Bounded detection, not instant.** If the Jetson was offline when the key was regenerated, it locks
only after connectivity returns and the next validation confirms the rejection (within one interval +
timeout). A network outage, Backend `5xx`, `403`, `404`, or any other ambiguous outcome does **not**
lock the Agent (it keeps operating on its still-valid credential and retries later) — **only** a
confirmed `401 INVALID_DEVICE_CREDENTIALS` does. "Offline" is never used for revocation.

**Manual recovery is the only way out of the lock** — the same out-of-band flow above:
`stop` → `set-activation-key.sh` (hidden prompt) → `start`. On success the Agent reactivates once,
retains its `DeviceId`, stores the new secret, clears the lock, and deletes the key file only after
persistence succeeds.

## Troubleshooting

| Symptom | Check |
|---------|-------|
| Service won't start, unactivated | Provision an Activation Key, then start (unactivated + no key fails by design, §12.3). |
| `pip install` fails on Python version | Confirm a ≥3.10 interpreter/venv exists; re-run `install.sh` (builds CPython 3.11 from source on arm64). |
| Activation fails | `journalctl -u weapon-detection-agent -n 100`; verify `WDA_BACKEND_BASE_URL` and Backend health. |
| Ambiguous timeout | The Agent never re-uses a one-time key. **Regenerate** a new key in the Dashboard and re-provision (§14). |
| `DeviceId` mismatch on reactivation | Startup fails loudly by design (OI-4) — investigate the Backend before proceeding. |

## MAC-11 / MAC-12 evidence

The recorded real-Jetson verification (steps, modes, Device ID matching the Dashboard, offline
restart, reactivation, clean-log inspection) is captured in the root `README.md` and IP-02 §21.

## Detection event Backend sync (IP-08, FS-06)

`WDA_DETECTION_SYNC_ENABLED` (`agent.env.example`) turns on `DetectionEventSyncWorker`, which drains
already-persisted `DetectionEvent` rows (written by the Detection Event Bridge, IP-07) to the
Backend's `POST /api/v1/sync/events` and marks only Backend-acknowledged events delivered.

* **Metadata synchronization only.** Snapshot capture and upload are **not** implemented anywhere in
  the Agent/Bridge. Every `Alert` row this feature creates on the Backend has `SnapshotReference =
  null`; there is no code path that populates it. Full FR-DET-004/FR-DET-007 acceptance (which
  requires a snapshot) is not claimed by this feature.
* **Camera name convention.** The Backend resolves the Agent's `WDA_DETECTION_CAMERA_ID` (e.g.
  `"camera1"`) against a `Camera.Name` on the authenticated Device's own Branch, case-insensitively.
  The Branch operator's Camera row must be named identically to this Agent's configured camera id, or
  every event is rejected with `UNKNOWN_CAMERA`.
* **Ship state.** `WDA_DETECTION_SYNC_ENABLED` defaults to `false` and **must stay `false`** for this
  deployment until the isolated end-to-end staging test (IP-08 Phase 16 — Backend + real SQL Server
  test database + a temporary Agent SQLite, exercising online delivery, an outage, reconnection, and
  duplicate retry) has actually passed. That verification is separate from, and has not been
  performed as part of, this feature's Agent-side implementation.

## Current exclusions

No camera/model/GPU config beyond what `deepstream/README.md` documents, no alert-status
transitions (acknowledge/dismiss), no heartbeat, no health endpoint, no remote commands, no siren,
no WebRTC. The Agent is not containerized.

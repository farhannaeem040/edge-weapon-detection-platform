# DeepStream Runtime Integration — deployment (IP-06, FS-04 Phase 1)

Generic, profile-based DeepStream supervision for the Jetson Agent. See
`specs/features/FS-04-deepstream-runtime-integration.md` and
`specs/implementation-plans/IP-06-deepstream-runtime-integration.md` for the full design.

**Detection Event Bridge (IP-07, in progress).** See
`specs/features/FS-05-detection-event-bridge.md` and
`specs/implementation-plans/IP-07-detection-event-bridge.md`. §"Installing `pyds`" below records the
T-80 go/no-go gate result for that work.

**Nothing here is YOLOv4-specific by architecture.** `yolov4-fp16` is the first validated model
*profile* — a self-contained set of model assets. A future model is a new profile directory, never a
code change.

**Disabled by default.** `WDA_DEEPSTREAM_ENABLED` defaults to `false` — even with a profile fully
deployed (below), the Agent will not launch DeepStream until this is explicitly set to `true` in
`/etc/weapon-detection-agent/agent.env` and the Agent is restarted. This is a deliberate rollout gate
separate from credential/Operational state gating.

## Contents

| Path | Purpose |
|------|---------|
| `deepstream-app.txt` | The single, profile-agnostic DeepStream application config the Agent always launches. Its `[primary-gie] config-file=` line is the one line that names the active profile. |
| `deploy-engine.sh` | Installs a validated `.engine` file as a named profile's `model.engine`, after verifying it against that profile's manifest. |
| `deploy-sample-video.sh` | Stages a local test video (for local-video lifecycle verification, T-77) as `samples/deepstream/input.mp4`. Never committed — see `.gitignore`. |
| `verify-deepstream.sh` | Opt-in verification (static checks always; `--run` also does a real bounded launch). |
| `profiles/<profile>/infer-config.txt` | The profile's `[property]` inference config — every model-specific value (dimensions, precision, parser, class count) lives here, never in Agent code. |
| `profiles/<profile>/labels.txt` | The profile's class labels. |
| `profiles/<profile>/manifest.env` | The profile's manifest — checksum and every fact needed to verify the engine before it is trusted (FS-04 §8.3). |

Installed by `install.sh` into `/opt/weapon-detection/config/deepstream/` (config/labels/manifests
only). `models/<profile>/model.engine` is installed **only** by `deploy-engine.sh`, and the local
test video at `samples/deepstream/input.mp4` **only** by `deploy-sample-video.sh` — both run
manually, never automatically by `install.sh`.

## Staging the local test video

```bash
sudo /opt/weapon-detection/agent/deployment/jetson/deepstream/deploy-sample-video.sh \
    --source /path/to/staged/test_video.mp4
```

Refuses a non-regular-file source (symlink/directory/device), refuses to overwrite an existing
`input.mp4` without `--force`, installs atomically, sets `0640 weapon-detection:weapon-detection`,
and never prints the video's contents.

## Deploying a model profile's engine

The engine itself (`*.engine`) is never committed — it is a large, device/runtime-specific build
artifact. Stage it on the Jetson (e.g. `scp` it alongside its manifest), then:

```bash
sudo /opt/weapon-detection/agent/deployment/jetson/deepstream/deploy-engine.sh \
    --profile yolov4-fp16 \
    --engine /path/to/staged/yolov4_fp16.engine \
    --manifest /opt/weapon-detection/config/deepstream/profiles/yolov4-fp16/manifest.env
```

This refuses to install unless: the profile name is safe, the engine is a regular `.engine` file (no
symlink/directory), the manifest is complete, the engine's SHA-256 matches
`manifest.env`'s `ENGINE_SHA256`, the profile's `infer-config.txt`/labels file are already present,
and any referenced parser library exists on this device. It never trusts the source filename. It
refuses to overwrite an existing `model.engine` for that profile without `--force`. Nothing is
printed except filenames, sizes, and hashes — never the engine's binary content.

**Adding a new profile:** create `deployment/jetson/deepstream/profiles/<new-name>/` with its own
`infer-config.txt`, `labels.txt`, and `manifest.env` (commit these — they are small text/metadata),
run `install.sh` (or `update.sh`) to sync them to the device, then `deploy-engine.sh --profile
<new-name> ...` to install its engine. Nothing in `DeepStreamProcessManager` or `AgentSettings`
changes.

**Switching the active profile:** edit the one `config-file=` line in
`/opt/weapon-detection/config/deepstream/deepstream-app.txt` to point at the new profile's
`infer-config.txt`, then restart the Agent (`sudo systemctl restart weapon-detection-agent`). This is
a deliberate, manual operator action — the Agent never decides this for itself.

## Local-video validation procedure (Phase 1)

1. Stage a test video with `deploy-sample-video.sh` (installs it at
   `/opt/weapon-detection/samples/deepstream/input.mp4`, the path `deepstream-app.txt`'s
   `[source0] uri=` references — never commit the video itself).
2. Run static checks: `sudo .../deepstream/verify-deepstream.sh`.
3. Run a real bounded launch: `sudo .../deepstream/verify-deepstream.sh --run --timeout=60`.
4. Confirm the Agent itself supervises it correctly: start the real Agent (`systemctl start
   weapon-detection-agent`) with an activated identity, confirm exactly one `deepstream-app` process
   via `pgrep -f deepstream-app`, then `systemctl stop weapon-detection-agent` and confirm it exits
   cleanly (no zombie, no orphan — `pgrep -f deepstream-app` returns nothing).

## Installing `pyds` (IP-07 T-80 — gate resolved; migrated to the Bridge's own venv)

> **Revision notice.** The DeepStream Bridge is specified (FS-05 §4.6) as a **fully separate
> application** under `/opt/weapon-detection/deepstream-bridge/`, with its own Python 3.8 virtual
> environment. The historical record below ("Install target: `runtime/python3.8/site-packages`"
> through "T-80 gate: passed — IP-07 proceeds to T-81") documents the *original* availability-gate
> verification — kept for the record, since it is what actually proved `pyds` 1.1.6 works on this
> device — but its install location has since been **migrated**. See "**Migration to the Bridge venv
> (complete)**" below for the current, authoritative install. Do not treat
> `/opt/weapon-detection/runtime/python3.8/site-packages` as a real path on this device any more — it
> has been removed. This section will eventually be superseded entirely by
> `deployment/jetson/deepstream/bridge/README.md` once T-89 lands.

The Detection Event Bridge (IP-07) reads real `NvDsObjectMeta` from a separate Python application
using NVIDIA's `pyds` bindings (FS-05 §4). This is a one-time, manual, operator-run step on the
Jetson — never automated by `install.sh` (same posture as `deploy-engine.sh`/`set-activation-key.sh`)
— and it runs against **system Python 3.8** (not the Agent's own Python 3.11 venv), since the Bridge
is a separate OS process, in a separate virtual environment, that the Agent only launches, never
imports (§9.2 SWA doc process boundary; FS-05 §4.3/§4.6).

**Version pin: `pyds` 1.1.6.** Confirmed via the `deepstream_python_apps` GitHub release notes that
`v1.1.6` is the release built against DeepStream SDK 6.2 / Ubuntu 20.04 / Python 3.8 — the exact
combination this device runs (`v1.1.8`+ targets DS 6.3 and will not load against this device's DS
6.2 native libraries). Do not install a newer `pyds` release without first confirming a matching
DeepStream SDK version bump.

**Install target: `/opt/weapon-detection/runtime/python3.8/site-packages`, not a per-user home
directory.** The DeepStream pipeline child runs under the `weapon-detection` service account (the
same account the Agent itself runs as, §systemd unit), which has no access to any interactive
operator's `~/.local` — an initial `pip install --user` done while investigating T-80 was corrected
after review specifically because it would not have worked in production. The controlled location
below follows the same ownership/mode convention `deploy-engine.sh` already uses for
`models/<profile>/model.engine` (§8 of FS-04): `weapon-detection:weapon-detection`, `0750` directories,
`0640` files.

```bash
curl -sL -o /tmp/pyds-1.1.6-py3-none-linux_aarch64.whl \
  https://github.com/NVIDIA-AI-IOT/deepstream_python_apps/releases/download/v1.1.6/pyds-1.1.6-py3-none-linux_aarch64.whl

sudo mkdir -p /opt/weapon-detection/runtime/python3.8/site-packages
sudo /usr/bin/python3 -m pip install --no-deps \
  --target=/opt/weapon-detection/runtime/python3.8/site-packages \
  /tmp/pyds-1.1.6-py3-none-linux_aarch64.whl
sudo /usr/bin/python3 -m pip install \
  --target=/opt/weapon-detection/runtime/python3.8/site-packages \
  pgi   # pyds's one transitive dependency (PyGObject itself is already the system python3-gi package)

sudo chown -R weapon-detection:weapon-detection /opt/weapon-detection/runtime
sudo find /opt/weapon-detection/runtime -type d -exec chmod 0750 {} \;
sudo find /opt/weapon-detection/runtime -type f -exec chmod 0640 {} \;

rm /tmp/pyds-1.1.6-py3-none-linux_aarch64.whl
```

No `apt-get install`/`--reinstall` of any system GStreamer/av package (the upstream
`user_deepstream_python_apps_install.sh` helper script does a broad `apt-get --reinstall` of core
multimedia packages and hardcodes the **x86_64** wheel URL even when asked for a specific version —
both unnecessary and wrong for this aarch64 device, so it was not used).

**How the child process finds it — `PYTHONPATH`, supplied by the launcher, never a global shell
profile.** `DeepStreamProcessManager` (T-72) stays fully generic — it still knows only six settings
(executable path, config path, cwd, stop timeout, restart policy, log path) and passes no custom
environment. The `PYTHONPATH` requirement is therefore satisfied by T-88/T-89's wrapper: the
executable a detection-enabled deployment points `WDA_DEEPSTREAM_EXECUTABLE_PATH` at is a small shell
script (`deployment/jetson/deepstream/pipeline/run-detection-pipeline.sh`, staged by `install.sh`'s
existing rsync pattern) that does exactly:

```bash
#!/bin/sh
export PYTHONPATH="/opt/weapon-detection/runtime/python3.8/site-packages${PYTHONPATH:+:$PYTHONPATH}"
exec /usr/bin/python3 /opt/weapon-detection/config/deepstream/pipeline/detection_pipeline.py "$@"
```

This keeps the environment-variable decision entirely in a committed, reviewable deployment artifact
— never in `/etc/environment`, a `.bashrc`, or any global shell profile — and confirms explicitly
that the child always runs under **system Python 3.8** (`/usr/bin/python3`), never the Agent's own
Python 3.11 venv interpreter. (The wrapper script itself is written in T-88/T-89; this section
records the design decision now because T-80's verification needed to prove the `PYTHONPATH`
mechanism works before that script exists.)

**Verified with the exact production account, interpreter, and `PYTHONPATH` (2026-07-24):**

```bash
sudo -u weapon-detection -H \
  env PYTHONPATH=/opt/weapon-detection/runtime/python3.8/site-packages \
  /usr/bin/python3 -c "
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

produced:

```
pyds: /opt/weapon-detection/runtime/python3.8/site-packages/pyds.so
GStreamer: GStreamer 1.16.3
NvDsObjectMeta: True
NvDsFrameMeta: True
```

Also confirmed, as `weapon-detection` via `sudo -u weapon-detection test -r <path>`: read access to
the deployed engine (`models/yolov4-fp16/model.engine`), the profile's `labels.txt` and
`infer-config.txt`, and `deepstream-app.txt` — all already `weapon-detection`-owned from IP-06's
provisioning, unaffected by this feature. (The pipeline module itself does not exist yet — T-88 —
so its readability is re-checked at T-89/T-90 once `install.sh` stages it into the same
`weapon-detection`-owned tree.)

The install survives an Agent/service restart by construction — `/opt/weapon-detection/runtime/` is
the same durable, `install.sh`-provisioned filesystem location as `models/`/`config/`, not a
per-session or per-user path.

An earlier verification pass had installed `pyds`/`pgi` under the `farhan` account's
`~/.local/lib/python3.8/site-packages` (`pip install --user`) to prove the wheel itself was importable
before committing to a deployment location. That copy has since been **removed**
(`python3 -m pip uninstall -y pyds pgi` as `farhan`) — confirmed `python3 -c "import pyds"` as `farhan`
now fails again — so the only surviving install is the controlled, `weapon-detection`-owned one above.

**T-80 gate: passed.** All five completion criteria held at the time: `weapon-detection` imports
`pyds` (1); via `/usr/bin/python3` (2); `NvDsFrameMeta`/`NvDsObjectMeta` both present (3); the install
location was durable across restarts (4); and `weapon-detection` could read the engine, labels,
inference config, and application config it needs (5). **This install location was then migrated —
see below.**

## Migration to the Bridge venv (complete)

Per FS-05 §4.6 (this revision), `/opt/weapon-detection/runtime/` is reserved exclusively for
transient runtime objects (the detection socket) — the `python3.8/site-packages` copy above was a
correct availability proof but the wrong permanent home. `pyds`/`pgi` now live in a dedicated
virtualenv, and `PYTHONPATH` is no longer part of how `pyds` itself is found (it is still used,
separately, to make the `deepstream_bridge` *application package* importable — see
`deployment/jetson/deepstream/bridge/run.sh`, T-89).

```bash
sudo apt-get install -y python3.8-venv   # required first — ensurepip is not bundled on this image

sudo python3.8 -m venv --system-site-packages /opt/weapon-detection/deepstream-bridge/venv

curl -sL -o /tmp/pyds-1.1.6-py3-none-linux_aarch64.whl \
  https://github.com/NVIDIA-AI-IOT/deepstream_python_apps/releases/download/v1.1.6/pyds-1.1.6-py3-none-linux_aarch64.whl

sudo /opt/weapon-detection/deepstream-bridge/venv/bin/pip install --no-deps \
  /tmp/pyds-1.1.6-py3-none-linux_aarch64.whl
sudo /opt/weapon-detection/deepstream-bridge/venv/bin/pip install pgi

sudo chown -R weapon-detection:weapon-detection /opt/weapon-detection/deepstream-bridge
sudo chmod 0750 /opt/weapon-detection/deepstream-bridge

rm /tmp/pyds-1.1.6-py3-none-linux_aarch64.whl

# The now-superseded install is removed entirely — runtime/ holds only transient objects again.
sudo rm -rf /opt/weapon-detection/runtime/python3.8
```

**Verified with the exact production account, through the venv's own interpreter — no `PYTHONPATH`
override needed (2026-07-24):**

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

produced:

```
pyds: /opt/weapon-detection/deepstream-bridge/venv/lib/python3.8/site-packages/pyds.so
GStreamer: GStreamer 1.16.3
NvDsObjectMeta: True
NvDsFrameMeta: True
```

Confirmed afterward: `pyds` is no longer importable via plain `python3`/`/usr/bin/python3` (as either
`farhan` or root) — the venv is the only place it resolves from. `/opt/weapon-detection/runtime/`
contains no subdirectories at all post-cleanup (`sudo find /opt/weapon-detection/runtime` returns only
the directory itself) — confirmed transient-objects-only, per FS-05 §4.6 requirement 3.

Ownership/mode chain (`namei -l`, via `sudo` since `farhan` cannot traverse the
`weapon-detection`-owned tree): `/` and `/opt` are world-traversable; `weapon-detection/`,
`deepstream-bridge/` are `weapon-detection:weapon-detection` `0750`; `venv/` is
`weapon-detection:weapon-detection` `0755` (its own creation default, unchanged — already gated by
the `0750` parent, consistent with how `deploy-engine.sh`'s installed assets are scoped).

**T-80: fully complete** — gate passed, migration complete, superseded location removed.
IP-07 proceeds to T-83 (T-81 and T-82 were already complete and untouched by this migration).

**One gap found and deferred to T-88**, not a gate failure: DeepStream's own RTSP-output sink
(`[sink] type=4` in `deepstream-app.txt`, not currently used by the committed template but used by
the operator's live RTSP deployment) is implemented internally by `deepstream-app`'s C code via
`GstRtspServer` (`gst-rtsp-server`), **not** the generic `rtspclientsink` element (confirmed absent
from this device's GStreamer registry — expected, it is not what DeepStream itself uses). The
Python bindings for `GstRtspServer` (`gir1.2-gst-rtsp-server-1.0`) are **not yet installed**, though
the underlying runtime library (`libgstrtspserver-1.0-0`) already is, and the introspection package is
available (`apt-get install gir1.2-gst-rtsp-server-1.0`, no reinstall of anything already working).
T-88's pipeline script needs this package to reconstruct the RTSP-out branch in Python, matching
NVIDIA's own `deepstream-test1-rtsp-out.py` sample pattern — recorded here so it isn't rediscovered
mid-T-88.

## T-92 — IDR/SPS-PPS RTSP-out production fix

**Symptom.** Clients occasionally saw visible H.264 block/box corruption in the RTSP output that
persisted for several seconds before self-healing, and a client connecting mid-stream (a late RTSP
`SETUP`) could sometimes never decode at all.

**Root cause and fix (both in `deployment/jetson/deepstream/bridge/app/deepstream_bridge/pipeline.py`
and `config.py`, not the reference `deepstream-app` binary path):**

- `rtph264pay`'s `config-interval` defaulted to sending SPS/PPS once at pipeline start only — a late
  joiner had no parameter sets to decode against and could never recover. Fixed by setting
  `config-interval=-1`, which re-embeds SPS/PPS before every IDR frame (`pipeline.py`,
  `_attach_rtsp_out`).
- `nvv4l2h264enc`'s own `idrinterval` default is 256 frames (~8.5s at 30fps): a lost/corrupted
  P-frame could propagate visible corruption via motion compensation for up to 8.5s before the next
  full-recovery IDR. Fixed by exposing a `[bridge-rtsp-out] idr-interval=` config key (default `30`
  frames, ~1s at 30fps — `config.py`'s `_parse_idr_interval`), applied directly to the encoder's
  `idrinterval` property.

**Explicitly out of scope for T-92** (task brief Part A.6): encoder-side `insert-sps-pps` was not
enabled — `rtph264pay`'s `config-interval=-1` already covers the late-joiner case; a static regression
test (`test_pipeline_rtsp_out_idr.py::test_insert_sps_pps_is_not_enabled_by_this_change`) asserts this
stays true unless later evidence shows an additional need.

**Regression tests:** `deployment/jetson/deepstream/bridge/tests/test_pipeline_rtsp_out_idr.py`.

This is a real-time recovery-speed fix, not a tracker change — see T-93 below for the separate,
independently evaluated question of whether `nvtracker` is needed at all.

## T-93 — Tracker-removal evaluation (isolated A/B, live Jetson)

**Question evaluated.** The project requirement is: detect gun/knife, send raw detections to the
Agent, apply class/camera cooldown, store/report accepted events. Persistent object identity,
trajectory, and unique-object counting are **not** required. `nvtracker` (DeepStream's own
`libnvds_nvmultiobjecttracker.so`) only provides those unneeded capabilities, so this evaluation asks
whether keeping it justifies its GPU/CPU cost — **not** whether it fixes the unrelated T-92 H.264
corruption, which it does not touch (the tracker sits between `nvinfer` and `nvvideoconvert`,
upstream of the encoder entirely).

**Method.** Two isolated runs on the real Jetson (2026-07-28), same camera-equivalent controlled test
video (`samples/deepstream/input.mp4`, the same file already used for the Agent-managed lifecycle
test and T-91's controlled gun/knife validation — a live camera cannot be given a controlled,
repeatable weapon-presence schedule remotely), same model/profile (`yolov4-fp16`), same `nvinfer`
`interval=0` (the real, unmodified production value — not changed for this test), same confidence
threshold (`infer-config.txt`, untouched), same RTSP-over-TCP transport settings, same
`WDA_DETECTION_COOLDOWN_SECONDS` default (5.0s), same 80-second wall-clock window, run back-to-back:

- **Test A** — `[tracker] enable=1` (production's current setting, unmodified).
- **Test B** — `[tracker] enable=0` — `pipeline.py` omits `nvtracker` construction/linking entirely
  (`nvinfer -> nvvideoconvert -> nvdsosd`), never merely disabling it at runtime.

The production `deepstream-app.txt` and its `agent.env` were never edited (verified byte-identical
against a pre-test backup after both runs). Both runs used the real `DetectionIngestHandler` /
`DetectionCooldownTracker` / `DetectionEventRepository` production code (not a simulation), listening
on the real detection socket, writing to an isolated throwaway SQLite database — never the production
`agent.db` — so the full Bridge -> UDS -> Agent -> SQLite path was exercised without touching
production data.

**Initial results (single run, superseded by the acceptance test below).** GPU 58.5%/65.2%
(tracker=1/0), 10 vs 19 accepted events. Test B ran immediately after Test A with no cool-down
interval, so the GPU/temperature comparison was thermally confounded, and the 10-vs-19 event gap was
unexplained. Both issues are resolved by the repeated acceptance test below.

### Acceptance test (2026-07-28): repeated A1/B1/B2/A2, raw-count instrumentation

To resolve the unexplained 10-vs-19 event gap before any production change, a second round added
non-invasive instrumentation (test-only scripts, no edits to `pipeline.py`/`probe.py`/`transport.py`/
`ingest_handler.py`): a wrapped `enqueue` capturing every raw detection fact (frame number, class,
confidence, bbox) to a JSONL file, and a logging handler tabulating
received/rejected-by-reason/suppressed/persisted counts from the Agent's own existing structured logs
— giving raw and accepted counts separately, not just a final SQLite total.

**Order:** A1 (tracker=1) -> B1 (tracker=0) -> B2 (tracker=0) -> A2 (tracker=1), same
`input.mp4` reset to frame zero each run (a fresh process per run), same 60s window, same
`interval=0`/confidence/cooldown/RTSP-TCP settings, isolated throwaway SQLite DB per run, thermal
recheck between runs (junction temp settled to 61.7-63.1C before each of the four runs — a materially
tighter band than the first single-run test, so this comparison is not thermally confounded).

| Run | Raw objects reaching probe | Accepted SQLite events | gun / knife |
|---|---|---|---|
| A1 (tracker=1) | 163 | 7 | 4 / 3 |
| A2 (tracker=1) | 163 (identical to A1) | 7 (identical to A1) | 4 / 3 |
| B1 (tracker=0) | 320 | 15 | 7 / 8 |
| B2 (tracker=0) | 311 | 15 (identical count to B1) | 7 / 8 |

Both configurations are highly reproducible (A1=A2 byte-for-byte; B1≈B2). Every raw detection was a
known class above threshold in all four runs — `rejected_total=0` throughout — and cooldown spacing
was verified `>= 5.0s` for every accepted event, every run, both classes (no violations).

**Root cause of the raw/accepted-count gap, found from the frame-number data (not guessed):**
tracker-off did not detect more objects *within the same portion of the video* — it processed a wider
frame-number range in the same 60s wall-clock window. A1's detections span frames 408-1755; B1's span
frames 85-2542, covering everywhere A1 covered (the same sub-bursts appear at the same frame numbers
in both — e.g. frames 1017-1103 in both) *plus* substantially more of the video before and after. This
is a **throughput/latency difference, not duplicate or spurious detections**: `nvtracker` adds
per-frame processing cost, so in a fixed wall-clock window the tracker-enabled pipeline gets through
less of the source video, encountering fewer weapon-visible frames. Confidence distributions were
statistically indistinguishable (gun avg 0.845 vs 0.809, knife avg 0.896 vs 0.881 — both classes both
configurations comfortably above the 0.50 threshold), ruling out a quality/threshold explanation.

**Performance (tegrastats, tight thermal band, both repeats consistent):**

| Metric | A1 | A2 | B1 | B2 |
|---|---|---|---|---|
| GPU avg / max | 53.8% / 95% | 50.1% / 94% | 63.1% / 89% | 65.8% / 88% |
| CPU avg (per-core %) | 3.4% | 3.4% | 3.2% | 3.3% |
| RAM avg | 4018 MB | 4050 MB | 3904 MB | 3892 MB |
| Junction temp start -> end | 61.8C -> 66.7C | 63.1C -> 67.4C | 62.3C -> 66.8C | 62.4C -> 67.0C |

Tracker-off shows *higher* average GPU utilization, consistently across both repeats — the opposite of
a naive "tracker removal reduces load" expectation, but consistent with the throughput finding above:
more of the GPU-side pipeline (decode/infer/encode) ran per wall-clock second because nothing was
serialized behind the tracker. Temperature differences across four 60s runs in a tight thermal band
were negligible (all four converge to ~67C) — not a discriminator at this timescale.

**Quality classification of the additional tracker-off events.** Inspected against the raw capture:
the extra B-run detections are not duplicates of an already-counted physical object within the same
moment (the overlapping frame ranges between A and B show matching sub-burst structure, not denser
detections in B within those ranges) and are not confidence/threshold artifacts (same confidence
profile). They classify as **genuine additional coverage** of the same video — correct gun/knife
detections in video segments the tracker-enabled pipeline did not reach within the fixed window, not
false positives or duplicate detections.

**Acceptance gate (all satisfied):** tracker-off does not increase false positives (verified above);
genuine weapon detections remain present (both classes, both repeats); cooldown remains correct (0
violations, all 4 runs); accepted-event behaviour is now explainable (throughput/frame-coverage, not a
defect); FPS/temperature remain acceptable (throughput higher, not lower; temperature indistinguishable
at this timescale); RTSP output remained playable (`h264`, `1280x720`) after the change. **Recommendation: disable `nvtracker` — applied to production 2026-07-28.**

### Production change applied (2026-07-28)

`[tracker] enable=1` -> `enable=0` in `/opt/weapon-detection/config/deepstream/deepstream-app.txt` —
the only line changed (diffed against a pre-change backup:
`config/deepstream/ab-test/backup-production-20260728T031117Z-pre-tracker-off.txt`). One controlled
`systemctl start weapon-detection-agent` (the service had been stopped for the isolated evaluation, so
this was a single start, not a stop/start cycle). Post-change verification, all passed: no `tracker`/
`nvtracker` text in the journal; no tracking-ID text in the journal; RTSP output plays (`h264`,
`1280x720`); real production events persisting to `agent.db` with correct 5.0s-exact cooldown spacing
observed on live camera detections; Agent (PID 13495) and Bridge (PID 13498) both stable across the
verification window; junction temperature 61C (safe, normal idle-under-load range).

**Rollback:** restore the pre-change backup above (or flip `enable=0` back to `enable=1`) and restart
the Agent service once — `pipeline.py`'s tracker construction is purely conditional on the parsed
config (`config.py::_parse_tracker`), so no code change is needed either direction.

**Regression tests:**
`deployment/jetson/deepstream/bridge/tests/test_pipeline_tracker_construction.py` (tracker
conditionally constructed/configured/linked; probe registered exactly once and reads metadata
regardless of tracker state; no NMS/parser/confidence/cooldown/SQLite logic in the pipeline module),
`deployment/jetson/deepstream/bridge/tests/test_config.py` (`enable=0`/section-omitted both resolve to
no tracker), `agent/tests/test_detection_cooldown.py` (cooldown key has no tracking-ID parameter;
repeated same-class detections with no tracking ID cooldown identically to the tracker-enabled case).

## Rollback procedure

If DeepStream supervision causes any Agent instability:

```bash
sudo systemctl stop weapon-detection-agent
```

Then choose one:

- **Fastest, no redeploy:** rename `/opt/weapon-detection/config/deepstream/deepstream-app.txt` out
  of the way (or simply never run `deploy-engine.sh`, leaving no `model.engine` installed) —
  `DeepStreamProcessManager.start()` will fail immediately (the executable/config still resolve, but
  DeepStream itself will fail to load a missing engine), which the coordinator's existing rollback
  handles safely (T-60) without touching Device Identity.
- **Full revert:** redeploy the pre-IP-06 Agent code via `update.sh` from the prior commit, or edit
  `agent/src/weapon_detection_agent/main.py` to call `create_app()` with no `components_factory`
  override (reverting to the always-safe empty-components default) and re-run `install.sh`.

In every case, rollback never risks Device Identity, `ActivatedAt`, or the SQLite store —
`DeepStreamProcessManager` is wired in only through `main.py`'s explicit override and touches no
activation/identity/database code path.

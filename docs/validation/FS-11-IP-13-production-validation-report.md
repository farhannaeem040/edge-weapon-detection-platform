# FS-11 / IP-13 — Final Production-Boundary Validation Report

| Field | Value |
|-------|-------|
| Task ID | T-252 |
| Realizes | FS-11 (Server-Driven Device Camera Configuration), IP-13 |
| Date | 2026-07-31 |
| Owner | Farhan Naeem |
| Environment | Production POC — central Docker Backend/SQL Server, real Jetson (JetPack 5.1.2), MediaMTX ingest, Windows publisher host |
| Decision | **PASS / COMPLETE** |

---

## 0. Provenance of Evidence

This report consolidates evidence from the whole FS-11/IP-13 programme. Two distinct provenance
classes are recorded, and they are deliberately not blended:

* **Directly observed in the final validation session (2026-07-31, 19:44–20:10 UTC).** The source1
  immutable-CameraId proof, the annotated-output isolation captures, the publisher-safety evidence,
  the final database/quota state, and the five-minute soak. These were measured live against the
  production Backend and Jetson and are reproduced here from the session's own tool output.
* **Recorded evidence carried forward from earlier authorised sessions.** The source0 mapping test,
  the ConfigCache outage/restart/reboot recovery proofs, the rename/StreamUrl/third-camera
  restart-semantics proofs, the automated test totals, and the GPU-utilisation measurement. These
  were supplied as authoritative recorded results for this report and are **not** re-measured here;
  T-252 is explicitly a documentation task, and re-running them would have required starting
  publishers and restarting the Agent, both prohibited for this task.

Where a figure belongs to the second class it is marked *(recorded)*.

---

## 1. Final Architecture Delivered

```
N enabled Backend Cameras
    ↓
N nvurisrcbin input sources
    ↓
one nvstreammux
    ↓
one nvinfer
    ↓
one shared detection probe on the batched nvstreamdemux sink pad
    ↓
one nvstreamdemux
    ↓
N independent branches:
    queue → conversion → nvdsosd → conversion/caps
      → nvv4l2h264enc → h264parse → rtph264pay
      → UDP intermediary → unique GstRtspServer mount
```

Invariants held in the production configuration:

| Invariant | State |
|---|---|
| One `nvstreammux` | Held — `batch-size = 2` |
| One `nvinfer` | Held — single `[primary-gie]`, `gie-unique-id = 1` |
| One detection-emission probe | Held — shared pre-demux probe; one event per detection, zero duplicates |
| One `nvstreamdemux` | Held |
| N OSD elements / N encoders / N RTSP mounts | Held — 2 sources, 2 `output-path` entries |
| No `nvmultistreamtiler` | Held — `[tiled-display] enable = 0` |
| No second inference pipeline | Held |

---

## 2. Identity Model as Deployed

| Property | Role | Restart semantics |
|---|---|---|
| `Camera.Id` | Immutable identity; stored in `DetectionEvent`; used for Backend Alert resolution | n/a |
| `Camera.Name` | Display-only label | Rename causes **no** Bridge restart *(recorded)* |
| `Camera.StreamUrl` | Authoritative DeepStream input; never an identity | Change causes **exactly one** controlled restart *(recorded)* |
| `Camera.SourceOrder` | Stable DeepStream source index | Change causes one controlled restart |
| `Camera.OutputPath` | Derived `cameras/{full immutable CameraId}`; stable across rename and StreamUrl change | n/a |
| `Device.AnnotatedOutputBaseUrl` | Externally reachable RTSP address for Admin API/frontend composition | Does not affect `configurationVersion`; no restart required |

### Production camera configuration

| Source | Display name | SourceOrder | Immutable CameraId | Input | Output mount |
|---|---|---|---|---|---|
| `source0` | Front Camera | 0 | `2613b331-8783-4d51-903a-3e41a979a14c` | `camera1` | `cameras/2613b331-8783-4d51-903a-3e41a979a14c` |
| `source1` | Rear Entrance | 1 | `ad8a1f09-7fba-4794-8f73-63c7e2c57c92` | `camera2` | `cameras/ad8a1f09-7fba-4794-8f73-63c7e2c57c92` |

Device annotated-output base URL: `rtsp://100.98.226.80:8554`, so dynamic output URLs resolve to
`rtsp://100.98.226.80:8554/cameras/{full-camera-guid}`. `/ds-test` is intentionally **not** mounted in
dynamic-output mode and returns 404.

---

## 3. Implementation Evidence

**Backend** — Device-authenticated `GET /api/v1/device/configuration`; `Camera.SourceOrder`; derived
`Camera.OutputPath`; `Device.AnnotatedOutputBaseUrl`; immutable-CameraId detection-sync resolution
(`AlertSyncService`, `Guid.TryParse` branch); transitional legacy-name fallback retained for rollout
compatibility only, **never taken in production validation**; additive migrations applied
successfully; EF has-pending-model-changes clean *(recorded)*.

**Agent** — Device configuration client, validation, coordinator, source mapping, serialization,
`ConfigCache` writer with last-known-good loading, generated runtime DeepStream configuration.
Feature-gated on `WDA_DEVICE_CONFIG_ENABLED`; server-driven mode **enabled in production**.
`WDA_DEVICE_CONFIG_MAX_CAMERAS = 3`.

**Bridge** — dynamic N-source construction; one shared mux/inference/demux path; dynamic N output
branches; unique RTSP factories with cleanup; shared pre-demux detection probe; post-demux OSD used
for rendering only.

**Frontend** — displays Camera input URL and annotated output URL separately with separate copy
actions; does not assume `/ds-test`; handles a missing Device output-base configuration; does not
claim direct browser RTSP playback.

---

## 4. Automated Verification *(recorded)*

| Suite | Result |
|---|---|
| Backend | 836 passing (after configurable-quota work) |
| Agent | 1053 passing |
| Bridge | 247 passing |
| Frontend | 392 passing |

Also passing: .NET build with 0 errors; EF pending-model check; Agent `ruff check`; Agent format
check; Bridge Python 3.8 compatibility check; Docker Compose validation; relevant Docker image builds.

**Pre-existing warnings — carried forward, not newly introduced by FS-11/IP-13:**

1. `System.Security.Cryptography.Xml` NU1903 advisories.
2. Windows typeshed `asyncio.start_unix_server` mypy false positive.

---

## 5. Production Configuration Validation *(recorded)*

| # | Proof | Result |
|---|---|---|
| 1 | Server-driven configuration fetched from Backend | Confirmed — `device_config_fetch_completed, status=200, camera_count=2` |
| 2 | ConfigCache written and persisted | Confirmed |
| 3 | Cached configuration recovered across Backend outage, Agent restart, and an unplanned full Jetson reboot | Confirmed on all three |
| 4 | Camera rename | **No** Bridge restart |
| 5 | StreamUrl change | Exactly **one** controlled Bridge restart |
| 6 | Adding a second Camera | One controlled restart; `batch-size = 2` |
| 7 | Adding a temporary third Camera | One controlled restart; `batch-size = 3`; three unique output mounts |
| 8 | Removing the third Camera | One controlled restart; `batch-size` back to 2; stale third mount returns 404; original two mounts unchanged |
| 9 | Two non-weapon sources | Separate Camera-specific outputs |
| 10 | Stopping one source | Other output never displayed the wrong Camera |
| 11 | Agent restart | Both output mounts recreated with identical URLs |

---

## 6. Source0 Mapping Evidence *(recorded — history since cleared)*

An earlier production source0 test generated **15 genuine source0 events**:

* all 15 mapped to Front Camera's immutable CameraId;
* zero cross-assigned to Rear Entrance;
* legacy-name fallback usage = 0;
* all delivered.

**Those 15 rows no longer exist.** The detection history was intentionally backed up and cleared
during the authorised POC detection-history reset. This report does not imply their presence in the
current database; the current `DetectionEvent` count of 4 excludes them entirely.

---

## 7. Source1 Mapping Evidence (directly observed, 2026-07-31)

The final source1 test used a **frozen delta baseline**, because one valid pre-existing event already
existed when the session began. The originally briefed "all zero" precondition was stale; deleting
the event or resetting quota was prohibited, so the delta approach was adopted with explicit approval.

| Item | Frozen baseline | Final | Delta |
|---|---|---|---|
| Backend Alerts | 1 | 4 | +3 |
| Jetson DetectionEvent | 1 delivered | 4 delivered | +3 |
| Pending | 0 | 0 | 0 |
| Quota accepted / remaining | 1 / 999 | 4 / 996 | +3 |

**Test delta:** 3 genuine DetectionEvents, 3 Backend Alerts, all `source_id = 1`, all mapped to Rear
Entrance CameraId `ad8a1f09-…`, zero Front Camera assignments, legacy-name fallback count = 0,
duplicates = 0, rejected = 0, quota_exceeded = 0, suppressed = 0.

### Traced event

| Field | Value |
|---|---|
| EventId | `a285ea58-…` |
| Bridge `source_id` | 1 |
| Expected CameraId | `ad8a1f09-…` |
| `DetectionEvent.CameraId` | `ad8a1f09-…` |
| `Alert.CameraId` | `ad8a1f09-…` |
| Resolved display name | Rear Entrance |
| `DetectedAtUtc` | Preserved exactly — SQLite `19:59:29.432034+00:00` = Backend `19:59:29.4320340` |
| Lifecycle | `pending` observed by the watcher, then `delivered` after Backend acknowledgement |
| `SnapshotReference` | NULL |

All 15 payload fields matched field-for-field across the SQLite/Backend boundary (FrameNumber 93,
960×398, bbox 119.375 / 34.84375 / 840.625 / 362.65625, `Status = New`, Backend-generated
`ReceivedAtUtc`).

### Latency

| Event | Backend received after detection | Delivered after detection |
|---|---|---|
| `a285ea58-…` (traced) | ~974.6 ms | ~1405.2 ms |
| second event | ~1567.2 ms | ~1995.1 ms |
| third event | ~251.4 ms | ~687.3 ms |

---

## 8. Annotated-Output Proof (directly observed)

**Front output** (`cameras/2613b331-…`) — showed the benign blue field bearing
`CAMERA1 BENIGN FRONT` with an advancing timecode, confirming live frames; no camera2 content; no
Front Camera detections.

**Rear output** (`cameras/ad8a1f09-…`) — showed camera2 CCTV weapon footage; no Front Camera visual
content. Before camera2 started, the Rear mount was **unavailable** rather than displaying source0
frames — the Rear branch never carried Front frames.

### Visual-evidence limitations (WARNING, not a blocker)

* The sampled Rear frames (2 fps) did not capture the exact frame containing the rendered bounding box.
* The Front output was captured **before** camera2 started, not simultaneously with the live window.

Identity isolation is nonetheless established independently of the visual sample, through:
`source_id` metadata on every event; zero source0 events; separate demux branches; and
CameraId-specific Alerts. The identity mapping itself is fully validated.

---

## 9. Publisher Safety Evidence (directly observed)

* Exact real FFmpeg executable used: `C:\Users\farha\scoop\apps\ffmpeg\current\bin\ffmpeg.exe`.
* `ExecutablePath` equality **asserted at launch** for both publishers.
* No Scoop shim; no Anaconda FFmpeg build; never invoked by name.
* `Start-Process -PassThru` used; real PIDs captured (camera1 = 74672, camera2 = 77512).
* Full-tree termination via `taskkill /PID <pid> /T /F`. The `/T` was load-bearing: camera1's tree
  contained child PID 12120 that a bare kill would have left streaming.

### Orchestration warning

The camera2 publisher stopped through its own `-t 15` bound (~19:59:41 UTC) **before** the delayed
reactive `taskkill` ran (20:01:59 UTC, which found the PID already gone). The watcher had detected the
first event much earlier (19:59:29.599 UTC), but a concurrent frame-capture operation on the same
control path delayed process termination. Consequently **three events were created instead of one**.

The 15-second hard limit and the five-event ceiling were **not** exceeded, and all three events are
genuine and correctly attributed. Future watcher and kill control must run independently from visual
frame capture. **This is an orchestration defect in the test harness, not a platform mapping defect.**

---

## 10. Quota and History State

* Quota is now configurable through `ALERT_QUOTA_MAXIMUM_PER_BRANCH_PER_DAY`.
* Default remains 15; active POC value = **1000**.
* Current accepted = **4**; remaining = **996**; suppressed = 0.
* Prior detection history was intentionally backed up and cleared under authorisation.
* **No further history was deleted after the source1 proof.**

---

## 11. Resource Evidence

* Two-camera RAM: approximately **3.7–3.9 GB of 15.5 GB** (soak observed 3717–3718 MB).
* Temperatures: approximately **52–59 °C** during final validation (soak observed 57.0–59.0 °C).
* Earlier two-camera active-inference measurement: approximately **70 % mean GPU utilisation**,
  frequently reaching **98–99 %** *(recorded)*.
* Three cameras were **functionally** demonstrated; only **two** received the completed stability soak.
* Production configured maximum was therefore **reduced from 8 to 3**.
* **Four- and eight-camera operation is not claimed to be hardware-supported.**

---

## 12. Snapshot State

* Snapshot capture remains **disabled**.
* Snapshot upload remains **disabled**.
* All Alert `SnapshotReference` values are NULL.
* `SnapshotOutbox` = 0.
* Backend snapshot volume = 0 files.
* Jetson snapshot spool = 0 files.
* **FS-08 / IP-10 remains incomplete** due to unresolved real-hardware JPEG correlation.

---

## 13. Final Five-Minute Soak (directly observed)

Six samples, T+0 through T+5 (20:05:00 – 20:10:13 UTC). Every sample identical:

| Metric | Value across all six samples |
|---|---|
| Backend health / restarts | Healthy, HTTP 200 / 0 |
| SQL Server health / restarts | Healthy / 0 |
| Agent PID | 407832 (unchanged) |
| Bridge PID | 407835 (unchanged) |
| systemd `NRestarts` | 0 |
| Agent operational state | Operational |
| Credential validation | HTTP 200 |
| Backend / ConfigCache version | Match (`0cb4b459…`) |
| Enabled Cameras / output mounts | 2 / 2 |
| streammux batch-size | 2 |
| Alerts (Rear / Front) | 4 (4 / 0) |
| DetectionEvent total / pending / delivered | 4 / 0 / 4 |
| `source_id` 1 / 0 events | 4 / 0 |
| Quota accepted / suppressed | 4 / 0 |
| Duplicates | 0 |
| Legacy-fallback log count | 0 |
| Snapshots (all locations) | 0 |
| Publishers | 0 |
| RAM | Stable 3717–3718 MB |
| Temperature | Stable 57.0–59.0 °C |
| GPU | Idle after publishers stopped |

**Soak result: PASS.**

---

## 14. Known Warnings and Deferred Follow-Ups

1. Publisher watcher termination must be decoupled from frame capture.
2. No benign real-world video was available; a synthetic flat field was used successfully.
3. The exact Rear bounding-box frame was not captured in the visual sample.
4. Front and Rear were not captured simultaneously during the source1 live window.
5. Three Cameras were demonstrated, but only two were soak-validated.
6. Four- and eight-camera capacity is unproven.
7. The unexpected Jetson reboot requires a separate power/watchdog/thermal investigation.
8. Shell history contains older token-like strings requiring separate review.
9. Source changes remain uncommitted.
10. Snapshot JPEG correlation remains unresolved (FS-08/IP-10).

**None of these invalidate the completed source-identity and dynamic-output acceptance.**

---

## 15. Final Acceptance Decision

### FS-11 / IP-13 — PASS / COMPLETE

This decision rests on all required evidence now existing:

* Backend supplies Camera configuration;
* `Camera.Name` is display-only;
* `Camera.StreamUrl` drives input;
* `SourceOrder` drives the source index;
* `source_id` maps to the immutable CameraId;
* one shared inference pipeline handles N sources;
* N enabled Cameras create N independent annotated output mounts;
* Camera rename causes no restart;
* StreamUrl changes cause exactly one restart;
* cache survives outage, restart and reboot;
* source0 maps correctly to Front Camera;
* source1 maps correctly to Rear Entrance;
* no cross-assignment;
* no legacy fallback;
* all current events delivered;
* pending = 0;
* snapshots disabled;
* the final soak passed.

---

## 16. Final Production State

* Two enabled Cameras retained.
* Two CameraId-derived annotated RTSP outputs active when inputs are available.
* `/ds-test` disabled in dynamic mode.
* One Agent (PID 407832), one Bridge (PID 407835).
* streammux `batch-size = 2`.
* Configured maximum Cameras = 3.
* Alerts = 4.
* DetectionEvents = 4, all delivered.
* Pending = 0.
* Quota = 4 / 1000 (996 remaining).
* Snapshots disabled everywhere.
* All publishers stopped.

# Feature Specification: Administrator-Defined Camera Keys and Branch Jetson Network Configuration

| Field | Value |
|-------|-------|
| Feature ID | FS-12 |
| Title | Administrator-defined `Camera.CameraKey` replaces the GUID in public RTSP mount paths; `Device.JetsonHost`/`Device.RtspOutputPort` replace the persisted `AnnotatedOutputBaseUrl` as the authoritative network configuration |
| Status | **Complete — Production Deployed and Validated (all tiers).** Migration `AddCameraKeyAndDeviceNetwork` applied to production 2026-08-02T20:46:33Z; Backend and frontend redeployed; Agent adopted the CameraKey configuration and the Bridge restarted exactly once. Production mounts are now `cameras/front-camera` and `cameras/rear-entrance`, proven isolated in a simultaneous two-camera benign capture; old GUID mounts and `/ds-test` return unavailable. Camera GUIDs, Alerts (4), DetectionEvents (4), quota (4/1000), DeviceId, credentials and Data Protection key all preserved. Five-minute soak passed. The FS-12 Agent build was deployed to the Jetson at 2026-08-02T21:14:04Z via the approved `update.sh`; `camera-key=` now appears in the generated runtime config and the Agent-side key validation is active. Snapshots remain disabled (FS-08/IP-10 incomplete). |
| Owner | Farhan Naeem |
| Related SRS Requirements | FR-BRN-* (Branch/Device/Camera administration, existing), FR-DET-* (detection identity, existing) — no new SRS requirement; this feature changes the *public* identifier of an annotated stream only |
| Related Architecture Sections | §13.1 (Branch/Device/Camera model), §14.1 API table (`PUT /api/v1/devices/{branchId}/network` replaces `.../annotated-output-base-url`) |
| Related ADRs | None new |
| Dependencies | FS-02 (Branch/Device/Camera onboarding — delivered), FS-11/IP-13 (server-driven Camera configuration, dynamic per-camera outputs — **Complete**) |
| Explicitly excluded | Browser video playback (RTSP→WebRTC/HLS conversion). CameraKey *editing* after creation. Multiple Devices per Branch. Model/parser/confidence/NMS/tracker/inference-interval/cooldown. Snapshot capture. Production deployment — this specification stops at the production boundary. |

---

## 1. Purpose

Today an annotated stream is published at `cameras/{full Camera GUID}`, so the public monitoring URL is
`rtsp://100.98.226.80:8554/cameras/2613b331-8783-4d51-903a-3e41a979a14c`. That is stable and unique, but it is
unreadable to an operator and impossible to type or communicate.

Separately, `Device.AnnotatedOutputBaseUrl` persists a complete URL string (`rtsp://100.98.226.80:8554`), which
conflates two independent facts — the Jetson's network location and the RTSP port — into one opaque value that
must be re-parsed to be useful.

This feature:

1. introduces an administrator-entered **`Camera.CameraKey`** as the public mount identifier;
2. replaces the persisted base URL with structured **`Device.JetsonHost`** + **`Device.RtspOutputPort`**;
3. leaves **every internal identity untouched** — `Camera.Id` remains the immutable GUID used by
   `DetectionEvent`, `Alert`, and the Agent's `source_id` mapping.

**Tailscale is only the current POC network simulation.** No persisted field, DTO member, column, or UI label
may be named for it. `JetsonHost` must remain valid for a LAN IP, a routed private IP, a public IP, a DNS
hostname, and the POC's current Tailscale IP alike.

---

## 2. Identity Model (frozen)

| Property | Role | Mutable? | Restart impact |
|---|---|---|---|
| `Camera.CameraId` | Immutable Backend-generated GUID. Database primary identity; used in `DetectionEvent`, `Alert`, all internal relationships and the Agent `source_id` map. **Never entered by an administrator. Never replaced by CameraKey.** | No | n/a |
| `Camera.CameraKey` | Administrator-entered stable **public** monitoring identifier. Sole component of the annotated RTSP mount path. Unique within the Branch. | **Immutable after creation** (this increment) | Would restart, but cannot change |
| `Camera.Name` | Editable display label | Yes | No restart; does not change CameraKey or output URL |
| `Camera.RtspUrl` (StreamUrl) | DeepStream input | Yes | Exactly one controlled Bridge restart |
| `Camera.SourceOrder` | Deterministic DeepStream source index; unique among enabled Cameras of a Branch | Yes | One controlled restart |
| `Device.JetsonHost` | IP or hostname of the Jetson | Yes | **No restart** — client-facing only |
| `Device.RtspOutputPort` | RTSP server port on the Jetson, default 8554 | Yes | **No restart** — client-facing only |

### 2.1 Derived values

```
Camera.OutputPath        = "cameras/" + CameraKey            (derived, not persisted)
Device.AnnotatedOutputBase = "rtsp://" + Host + ":" + Port    (computed, not persisted)
Camera.OutputStreamUrl   = AnnotatedOutputBase + "/" + OutputPath
```

Worked example:

```
JetsonHost      100.98.226.80
RtspOutputPort  8554
CameraKey       front-entrance
→ rtsp://100.98.226.80:8554/cameras/front-entrance
```

---

## 3. CameraKey Contract (frozen)

**Pattern:** `^[a-z0-9](?:[a-z0-9-]{1,62}[a-z0-9])$`

* required — never silently generated from `Camera.Name`;
* lowercase only (an uppercase key is **rejected**, not normalised — silent rewriting of an administrator's
  explicit input is prohibited by the task brief);
* 3–64 characters;
* starts and ends with a lowercase letter or digit;
* internal characters may be lowercase letters, digits, or hyphens;
* no spaces, underscores, slashes, backslashes, `..`, query strings, fragments, control characters, URL-encoded
  sequences, or leading/trailing hyphens;
* unique within the Branch (case-sensitively moot — only lowercase is accepted);
* **immutable after creation.**

**Reserved keys** (rejected): `ds-test`, `api`, `admin`, `health`, `metrics`, `cameras`.

### 3.1 Named validation errors

| Code | Condition |
|---|---|
| `CAMERA_KEY_REQUIRED` | Missing, empty or whitespace |
| `CAMERA_KEY_INVALID` | Fails the pattern or the length bounds |
| `CAMERA_KEY_RESERVED` | Matches a reserved key |
| `CAMERA_KEY_ALREADY_EXISTS` | Duplicate within the Branch |
| `CAMERA_KEY_IMMUTABLE` | An update attempted to alter an existing key |

---

## 4. JetsonHost / RtspOutputPort Contract (frozen)

`JetsonHost` — required at Branch creation; trimmed; accepts IPv4, IPv6, or a DNS hostname. Rejects: a URI
scheme, any path, an embedded port, a query, a fragment, credentials, control characters, and anything over
255 characters.

| Valid | Invalid |
|---|---|
| `100.98.226.80` | `rtsp://100.98.226.80` |
| `192.168.1.50` | `100.98.226.80:8554` |
| `10.20.0.15` | `user:password@host` |
| `jetson-ljmu.local` | `host/cameras` |
| `jetson-branch-01.example.internal` | `host?token=x` |

`RtspOutputPort` — integer, default `8554`, range 1–65535; zero and negatives rejected.

**IPv6** is bracketed when composed: `rtsp://[2001:db8::1]:8554/cameras/front-camera`.

Named errors: `JETSON_HOST_REQUIRED`, `JETSON_HOST_INVALID`, `RTSP_OUTPUT_PORT_INVALID`.

---

## 5. `AnnotatedOutputBaseUrl` Transition (Option A — transitional)

1. Add nullable `JetsonHost` and nullable `RtspOutputPort`.
2. Backfill both from any existing `AnnotatedOutputBaseUrl` by parsing host and port.
3. **Retain** the `AnnotatedOutputBaseUrl` column temporarily, as a **read-fallback only**.
4. All new writes target `JetsonHost`/`RtspOutputPort`; the composed base is computed from them.
5. Reads prefer the structured fields and fall back to the legacy column only when the structured fields are
   null.
6. Column removal is deferred to a later feature.

The production value `rtsp://100.98.226.80:8554` must backfill to `JetsonHost = 100.98.226.80`,
`RtspOutputPort = 8554`. **The POC IP must never be hardcoded in reusable domain logic** — it exists only as
migration data and test fixtures.

---

## 6. `configurationVersion`

**Included:** `CameraId`, `StreamUrl`, `SourceOrder`, `OutputPath` (which now embeds the CameraKey).

**Excluded:** `Camera.Name`, `JetsonHost`, `RtspOutputPort`, composed `outputStreamUrl`, all Branch display
fields.

Consequence: changing only `JetsonHost` or `RtspOutputPort` **must not** change `configurationVersion` and
**must not** restart the Bridge. Because `OutputPath` already participates in the hash and CameraKey is
immutable, no normal edit can change a key after creation.

---

## 7. Rollout to the Existing POC Deployment

The Backend's derivation flips from GUID to CameraKey, so `configurationVersion` changes **exactly once**. The
Agent then performs **exactly one** controlled Bridge restart, and the old GUID mounts vanish with the old
Bridge process. Old GUID URLs stop working; both formats are **not** maintained simultaneously.

Approved POC backfill: `Front Camera → front-camera`, `Rear Entrance → rear-entrance`.

---

## 8. One Branch : One Device

Already enforced at the database level by the unique index on `Device.BranchId`. This feature **preserves** that
constraint and does not redesign for multiple Devices per Branch. One Device per Branch is the accepted current
architecture, and that Device is the network host for every Camera output of its Branch.

---

## 9. Acceptance Criteria

1. Branch creation collects and stores `jetsonHost` and `rtspOutputPort` on the reserved Device.
2. Camera creation requires an administrator-entered `cameraKey`; none is ever auto-generated.
3. CameraKey uniqueness is enforced within a Branch, at both service and database level.
4. CameraKey is immutable — an update attempt returns `CAMERA_KEY_IMMUTABLE`.
5. `outputPath == "cameras/" + cameraKey`.
6. `outputStreamUrl == "rtsp://{host}:{port}/cameras/{key}"`, IPv6 bracketed.
7. `DetectionEvent.CameraId` and `Alert.CameraId` remain the immutable GUID.
8. Renaming a Camera changes neither `outputPath` nor `outputStreamUrl` nor `configurationVersion`.
9. Changing `StreamUrl` changes `configurationVersion` but not `outputPath`.
10. Changing `JetsonHost`/`RtspOutputPort` changes `outputStreamUrl` only — not `configurationVersion`, not
    `outputPath`, not credentials, not activation status, not Alerts.
11. An invalid CameraKey rolls back the entire Branch-creation transaction — no Branch, Device, Camera or
    ActivationKey row survives.
12. Existing FS-06 sync, FS-07 Data Protection, FS-09 quota, and snapshot-disabled behaviour remain intact.
13. Browser playback is not implemented.

# Feature Specification: Branch Live Monitoring and Live Inference Viewer

| Field | Value |
|-------|-------|
| Feature ID | FS-14 |
| Title | Branch Live Monitoring and Live Inference Viewer — browser-based live playback of a Camera's raw feed and the Jetson's existing annotated feed, via a WebRTC media gateway |
| Status | In progress |
| Related SRS Requirements | FR-DEV-* (Device/Camera onboarding, existing) — no new SRS requirement; this feature adds a read-only viewing capability on top of data the platform already persists (`Camera.RtspUrl`, `Camera.CameraKey`, `Device.JetsonHost`, `Device.RtspOutputPort`) |
| Related Architecture Sections | §14.1 API table (two new Backend endpoints); single-origin Nginx reverse-proxy convention (ARCH-001 §12.1) — extended, not replaced, with a `/media/` proxy location |
| Related ADRs | None new |
| Owner | Farhan Naeem |
| Dependencies | FS-03 (branch-camera-management — delivered): `Camera.CameraId`/`Name`/`RtspUrl`/`CameraKey`/`BranchId`. FS-11 (device-camera-configuration — delivered): `Camera` as pipeline-input source of truth. FS-12 (camera-keys-jetson-network-configuration — delivered): `Device.JetsonHost`/`RtspOutputPort`, `CameraKey`-derived annotated RTSP mount (`/cameras/{CameraKey}`). FS-10 (monitoring-dashboard-alert-ui — delivered): Alert detail page, Branch/Camera identifiers already carried on `AlertDetail`. |
| Explicitly excluded | Jetson pipeline changes (DeepStream/YOLO26/NvDCF/nvstreammux/nvinfer/nvstreamdemux/OSD), Agent/Bridge changes, `CameraKey` generation, snapshot capture/upload/retrieval, video recording, historical playback, PTZ, audio, multi-camera grid view, person detection, new Camera configuration semantics. |

## 1. Purpose and Scope

Two RTSP feeds already exist for every Camera — the raw feed at `Camera.RtspUrl` and the Jetson's annotated
(YOLO26/NvDCF/OSD) output at `rtsp://{Device.JetsonHost}:{Device.RtspOutputPort}/cameras/{Camera.CameraKey}` — but
nothing in the Admin dashboard lets anyone actually watch either one; `branch-detail.ts` only renders both URLs as
copyable text, with an explicit comment that browser playback is a "separate future feature." This feature is that
future feature: it adds live playback of both feeds inside the Admin dashboard, with no new inference pipeline and
no Jetson-side change of any kind.

Browsers cannot consume `rtsp://` directly. A media gateway is required to convert RTSP into a browser-compatible
transport. A MediaMTX instance already runs in production (ad hoc, for the Windows RTSP test-stream ingestion
workflow documented in CLAUDE.md) and — confirmed by direct protocol probe during design — already has WebRTC
listening internally. This feature formalizes that instance into the project's container-managed deployment
(`compose.yaml`) rather than adding a second, competing media gateway, and uses its WHEP (WebRTC-HTTP Egress
Protocol) interface for low-latency live playback.

```text
Admin selects Branch + Camera + mode (monitoring | inference)
    ↓
Angular calls POST /api/v1/live-streams { branchId, cameraId, mode }  (NEW)
    ↓
Backend authenticates Admin (existing ActiveAdminSessionRequirement fallback policy)
    ↓
Backend verifies Camera belongs to Branch
    ↓
Backend resolves the real RTSP source server-side:
    monitoring → Camera.RtspUrl
    inference  → rtsp://{Device.JetsonHost}:{Device.RtspOutputPort}/cameras/{Camera.CameraKey}
    ↓
Backend ensures a MediaMTX path exists for this Camera+mode (idempotent, via MediaMTX's own config API)
    ↓
Backend returns { sessionId, playbackUrl } — playbackUrl is a relative, single-origin WHEP path; never a raw
RTSP URL, never a credential
    ↓
Angular opens an RTCPeerConnection against the WHEP endpoint (proxied through the existing Nginx origin)
    ↓
MediaMTX pulls the RTSP source on demand (only while a viewer is connected) and serves it over WebRTC
    ↓
Browser renders the live video in a <video> element
```

## 2. In scope

- A Live Monitoring tab on the Branch detail page (`Overview | Live Monitoring`).
- Camera selection scoped to the current Branch, keyed by the immutable `Camera.CameraId` (never `CameraKey`).
- Two modes: `monitoring` (raw `Camera.RtspUrl`) and `inference` (existing annotated Jetson output).
- Browser-compatible live playback via WebRTC (WHEP), served through the existing single-origin Nginx proxy.
- Backend endpoints: `GET /api/v1/branches/{branchId}/live-monitoring/cameras`, `POST /api/v1/live-streams`.
- Server-side-only resolution of both RTSP source URLs — the client only ever sends `{ branchId, cameraId, mode }`,
  never a URL; this is the SSRF/open-proxy protection this feature must not compromise.
- Reuse and formal `compose.yaml` management of the existing MediaMTX instance (no second media gateway).
- Alert detail → "View Live Camera" / "View Live Inference" deep links into this same feature.
- Automated tests (Backend, Frontend, gateway protocol-level) and production deployment.

## 3. Out of scope

Jetson pipeline redesign, a second/duplicate inference pipeline, YOLO26/NvDCF changes, snapshot capture/upload/
retrieval changes, video recording, historical/DVR-style playback, PTZ camera control, audio, a simultaneous
multi-camera grid view (first version is single-camera-at-a-time), person detection, and any change to how
`Camera`/`CameraKey`/`Device.JetsonHost`/`Device.RtspOutputPort` are created or validated.

## 4. Identity model (frozen, unchanged by this feature)

| Concept | Meaning | Used by this feature as |
|---|---|---|
| `Camera.CameraId` | Immutable Backend GUID PK | The only client-facing selection identity (camera `<select>` value, `POST /api/v1/live-streams` request field) |
| `Camera.CameraKey` | Administrator-entered, branch-scoped-unique, used today only to derive the annotated RTSP mount path (`FS-12`) | Used **server-side only**, to build the `inference`-mode source URL. Never sent to the browser as an identity. |
| `Camera.RtspUrl` | Raw camera source, may embed credentials (already documented, already redacted on the way out to Angular by `RtspUrlSanitizer`) | Used **server-side only**, to build the `monitoring`-mode source URL. Never returned to the browser in any form. |
| `Device.JetsonHost` / `Device.RtspOutputPort` | Existing FS-12 fields locating the Jetson's annotated RTSP server | Used **server-side only**, to build the `inference`-mode source URL. |

## 5. Media gateway

MediaMTX (`bluenviron/mediamtx:1`) already runs in production for RTSP test-stream ingestion (CLAUDE.md's
"Windows RTSP Test Stream Control" section — that workflow is unchanged by this feature; RTSP port 8554 keeps
its existing behavior exactly). It is promoted from an ad hoc `docker run` container into a proper `compose.yaml`
service, joined to the `internal` network, with:

- RTSP (8554) published to the host exactly as today.
- WebRTC/WHEP signaling (8889) reachable only inside the compose network — the Backend/Nginx reach it by
  container name, it is never published to the host directly.
- The WHEP HTTP signaling path reverse-proxied by the existing `frontend` Nginx under `/media/`, preserving the
  single-origin architecture (no new absolute origin in Angular's `environment.ts`).
- A WebRTC ICE/UDP media port published to the host — WebRTC's actual media (RTP) cannot be reverse-proxied over
  plain HTTP the way signaling can; only this one UDP port needs host exposure, not the RTSP/API ports.
- MediaMTX's own config API (bound internally only) used by the Backend to idempotently ensure a path exists per
  `{cameraId}-{mode}`, with `sourceOnDemand: yes` — MediaMTX itself pulls the upstream RTSP source only while at
  least one viewer is connected and releases it after a short idle window, so the Backend needs no separate
  session-expiry sweeper for the media connection itself.

## 6. Acceptance criteria

See IP-16 for the phased implementation/validation plan. At a high level: both modes play back in the Admin
dashboard for both existing production Cameras (Front Camera, Rear Entrance), mode/Camera switching cleans up the
previous player, Alert → Live View deep-links work, no client can supply an arbitrary RTSP source, no credential
ever reaches the browser, and all existing functionality (YOLO26, NvDCF, DetectionEvents, Alerts, snapshots,
Agent/Bridge) is unaffected.

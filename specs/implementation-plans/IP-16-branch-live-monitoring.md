# Implementation Plan: Branch Live Monitoring and Live Inference Viewer

| Field | Value |
|-------|-------|
| Plan ID | IP-16 |
| Title | WebRTC-based browser playback of raw and annotated Camera RTSP feeds, via the existing MediaMTX instance formalized into `compose.yaml` |
| Realizes | FS-14 |
| Governing Documents | FS-14, FS-03 (frozen — `Camera` entity), FS-11 (frozen — `Camera` as pipeline source of truth), FS-12 (frozen — `CameraKey`/`Device.JetsonHost`/`RtspOutputPort`, annotated mount derivation), FS-10 (frozen — Alert detail page) |
| Depends On | FS-03, FS-11, FS-12, FS-10 — all delivered |
| Owner | Farhan Naeem |
| Explicitly Excluded | Jetson/DeepStream/Agent/Bridge changes, a second inference pipeline, `CameraKey` generation changes, snapshot changes, recording, historical playback, PTZ, audio, multi-camera grid |

---

## 1. Approved Design Decisions

1. **No second media gateway.** The MediaMTX instance already running in production (ad hoc `docker run`, used for the Windows RTSP test-stream ingestion workflow) is formalized into `compose.yaml`, pinned to the digest already running, rather than adding a competing gateway. Its RTSP port (8554) and existing behavior for that workflow are unchanged.
2. **WebRTC/WHEP is the browser transport**, not HLS — WHEP is a plain HTTP+SDP protocol MediaMTX already supports (confirmed listening internally during design inspection), needs no external signaling server, and gives materially lower latency than HLS's segment-based approach, which matters for a live-monitoring use case. HLS was considered and rejected for this reason; the latency trade-off is documented in FS-14 §5.
3. **No per-session MediaMTX path.** Paths are keyed deterministically by `{cameraId}-{mode}` and ensured idempotently by the Backend. MediaMTX's own `sourceOnDemand`/idle-close handles connect/disconnect lifecycle; the Backend does not run a session-expiry sweeper for the media connection itself. The `sessionId` returned by `POST /api/v1/live-streams` is a Backend-side audit/authorization record, not a gateway resource.
4. **Server-side-only source resolution.** The client never supplies a URL — only `{ branchId, cameraId, mode }`. This is the SSRF/open-proxy protection boundary and must not be weakened.
5. **Single-origin preserved.** WHEP signaling is reverse-proxied by the existing `frontend` Nginx under `/media/`; only the WebRTC ICE/UDP media port needs new host exposure (media itself can't go through an HTTP reverse proxy).

---

## 2. Task Breakdown

### Media Gateway

| Task | Description |
|---|---|
| T-1 | Add `deployment/mediamtx/mediamtx.yml` — RTSP unchanged, `api: yes` bound internally, WebRTC enabled with a published ICE/UDP mux port, no other surface added. |
| T-2 | Add a `mediamtx` service to `compose.yaml` (pinned digest, `internal` network, mounts `mediamtx.yml`, only the ports documented in FS-14 §5 published). Remove/replace the ad hoc container. |
| T-3 | Add the `/media/` reverse-proxy location to `frontend/nginx.conf`. |
| T-4 | Stage B protocol-level proof: raw and annotated RTSP both produce a valid WHEP SDP answer through the gateway, before any Backend/Angular wiring is exposed. |

### Backend

| Task | Description |
|---|---|
| T-5 | `IMediaGatewayClient`/`MediaMtxGatewayClient` (Infrastructure) — idempotent path-ensure against MediaMTX's config API. |
| T-6 | `ILiveStreamService`/`LiveStreamService` — Branch/Camera ownership check, monitoring/inference source resolution, typed outcomes (`Ok`, `CameraNotFound`, `DeviceUnavailable`). |
| T-7 | `LiveMonitoringController` — `GET /api/v1/branches/{branchId}/live-monitoring/cameras`, `POST /api/v1/live-streams`. Default fallback auth policy, same as `AlertController`/`BranchController`. |
| T-8 | `MediaGatewayOptions` config section + DI registration, mirroring `AlertSnapshotStorageOptions`. |
| T-9 | Unit tests (`LiveStreamServiceTests`, `LiveMonitoringControllerTests`) + integration tests (`LiveMonitoringApiTests`) per FS-14's SSRF/credential-leak requirements. Full existing suite stays green. |

### Frontend

| Task | Description |
|---|---|
| T-10 | `live-monitoring.models.ts` + `live-monitoring.service.ts`. |
| T-11 | `live-monitoring.ts` component (Camera select, mode toggle, WHEP `RTCPeerConnection` player, connection-state indicator). |
| T-12 | `branch-detail.ts` — add `Overview \| Live Monitoring` tab hosting the new component, query-param-driven camera/mode state. |
| T-13 | `alert-detail.ts` — "View Live Camera"/"View Live Inference" links navigating into the Branch Live Monitoring tab, preselected. |
| T-14 | Karma tests for the new service/component + updated `alert-detail.spec.ts`. Full existing suite stays green. |

### Deployment / Validation

| Task | Description |
|---|---|
| T-15 | Stage A: all builds/tests green, nothing deployed yet. |
| T-16 | Stage B: gateway deployed and proven independently (T-4), before Backend/Angular changes are live. |
| T-17 | Stage C: Backend/frontend containers rebuilt/deployed, existing SQL/snapshot volumes and Backend config preserved. |
| T-18 | Stage D: production functional/protocol-level validation (both modes, both existing Cameras, mode/Camera switching, Alert deep links, regression of YOLO26/NvDCF/Alerts/snapshots). |
| T-19 | Documentation: this file's Status field, FS-14 §6 acceptance evidence, README/architecture updates as needed. |

---

## 3. Status

**Complete — deployed and protocol-validated. UI enhancement (main-sidebar Monitoring entry) also
complete and deployed:** a `GlobalMonitoringComponent` (`frontend/src/app/monitoring/`) reachable
from a new "Monitoring" item in the main sidebar (between Alerts and Branches), reusing the exact
same `LiveMonitoringComponent` the Branch detail page's own "Live Monitoring" tab already hosts — no
player/WebRTC/WHEP code duplicated. It loads Branches via the existing `BranchService.list()` (no
new Backend endpoint needed), auto-selects when there is exactly one, offers a dropdown otherwise,
and forces a clean destroy/recreate of the live-monitoring player on Branch switch (an Angular
`@for(track branchId)` around a singleton array — not `@if` — since `LiveMonitoringComponent` only
loads its Cameras once, in `ngOnInit`, and a plain `@if` would just patch its input in place and
leave the previous Branch's Cameras/player visible). The Branch detail page's own tab is unchanged
and still works. No Backend, MediaMTX, or Jetson files were touched for this enhancement. MediaMTX formalized into `compose.yaml` (digest-pinned,
`internal` network, control API restricted to the network's pinned subnet). Backend/Frontend built and
deployed with the full feature. WHEP handshake (SDP offer → 201 Created answer) proven directly against
MediaMTX for both source types on real production data — raw `camera1` (Front Camera, monitoring) and the
Jetson's real annotated output (`rtsp://100.98.226.80:8554/cameras/front-camera`, inference) — and proven
again through the complete production path (Angular's real request shape → Backend → Nginx `/media/`
`auth_request`-gated proxy → MediaMTX) for monitoring mode, with an unauthenticated request to the same
proxied endpoint independently confirmed rejected (401). No browser-automation tool was available in this
environment, so interactive in-browser playback confirmation was not performed; protocol-level WHEP proof
against real production sources is the evidence of record. See the session's final report for full detail.

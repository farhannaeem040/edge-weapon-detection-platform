/**
 * The wire contract of the Live Monitoring endpoints (FS-14 §5, IP-16 T-10).
 *
 * Transcribed from the Backend's `LiveMonitoringCameraDto`/`LiveStreamResponseDto`. Neither of the
 * two source RTSP URLs (`Camera.RtspUrl`, the Jetson-annotated output) ever appears anywhere in
 * these shapes — the Backend resolves both server-side and the browser only ever receives a
 * relative, single-origin `playbackUrl` (FS-14 §5's SSRF/credential-leak protection).
 */

/** A Camera as offered for live viewing (`GET /api/v1/branches/{branchId}/live-monitoring/cameras`). */
export interface LiveMonitoringCamera {
  cameraId: string;
  name: string;
  /** The public stream key — never used as a selection identity, only shown/derived server-side. */
  cameraKey: string;
  monitoringAvailable: boolean;
  inferenceAvailable: boolean;
}

/** The two live-viewing modes (FS-14 §5) — matches the Backend's `LiveStreamMode` enum values. */
export type LiveStreamMode = 'monitoring' | 'inference';

/** Request body of `POST /api/v1/live-streams`. Deliberately carries no URL field. */
export interface CreateLiveStreamRequest {
  branchId: string;
  cameraId: string;
  mode: LiveStreamMode;
}

/** `data` of a successful `POST /api/v1/live-streams`. */
export interface LiveStreamSession {
  sessionId: string;
  cameraId: string;
  mode: LiveStreamMode;
  /** A relative, single-origin WHEP path (e.g. `/media/live-.../whep`) — never a raw RTSP URL. */
  playbackUrl: string;
  expiresAtUtc: string;
}

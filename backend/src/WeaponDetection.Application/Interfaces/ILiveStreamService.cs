namespace WeaponDetection.Application.Interfaces;

// FS-14 §5/§6, IP-16 T-6. Resolves a Camera's two RTSP sources (raw / Jetson-annotated) and ensures
// a browser-playable path exists on the media gateway. The Backend is the only thing that ever
// constructs a real RTSP URL for this feature — a client supplies only { branchId, cameraId, mode }
// (LiveStreamRequest below), never a URL, which is what keeps this from becoming an SSRF/open-proxy
// endpoint (FS-14 §5).
public interface ILiveStreamService
{
    // FS-14: GET /api/v1/branches/{branchId}/live-monitoring/cameras. Returns an empty list (never
    // throws NotFound) for an unknown Branch — mirrors IAlertQueryService's list-endpoint shape;
    // there is no ambiguity to disclose since an empty camera list is already the correct answer for
    // "this Branch has no cameras" too.
    Task<IReadOnlyList<LiveMonitoringCameraView>> ListCamerasAsync(
        Guid branchId, CancellationToken cancellationToken = default);

    // FS-14: POST /api/v1/live-streams.
    Task<LiveStreamOutcome> CreateStreamAsync(
        LiveStreamRequest request, CancellationToken cancellationToken = default);
}

public sealed record LiveMonitoringCameraView(
    Guid CameraId, string Name, string CameraKey, bool MonitoringAvailable, bool InferenceAvailable);

// The wire contract's mode values (FS-14 §5): "monitoring" = Camera.RtspUrl, "inference" = the
// existing Jetson-annotated output. Kept as an enum rather than a raw string so an unrecognized
// value is a parse failure at the API boundary, never a value this service has to re-validate.
public enum LiveStreamMode
{
    Monitoring,
    Inference,
}

public sealed record LiveStreamRequest(Guid BranchId, Guid CameraId, LiveStreamMode Mode);

public enum LiveStreamOutcomeKind
{
    Created,
    BranchOrCameraNotFound, // Camera does not exist, or belongs to a different Branch — one outcome,
                            // same non-disclosure convention AlertSnapshotUploadService already uses.
    DeviceUnavailable,      // inference mode requested but the Branch's Device has no
                            // JetsonHost/RtspOutputPort configured yet.
    GatewayUnavailable,     // the media gateway rejected or could not be reached.
}

public sealed record LiveStreamOutcome(
    LiveStreamOutcomeKind Kind, Guid? SessionId, string? PlaybackUrl, DateTime? ExpiresAtUtc)
{
    public static LiveStreamOutcome Created(Guid sessionId, string playbackUrl, DateTime expiresAtUtc) =>
        new(LiveStreamOutcomeKind.Created, sessionId, playbackUrl, expiresAtUtc);

    public static LiveStreamOutcome Failed(LiveStreamOutcomeKind kind) =>
        new(kind, null, null, null);
}

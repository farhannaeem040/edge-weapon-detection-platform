namespace WeaponDetection.Api.Contracts;

// One Jetson-generated detection event as carried in a POST /api/v1/sync/events batch (FS-06 §4.1).
// CameraId is the Agent's local camera identifier (WDA_DETECTION_CAMERA_ID, e.g. "camera1") — not
// the Backend's Camera.CameraId GUID; AlertSyncService resolves it per FS-06 §6.3. DetectedAtUtc is
// the original detection timestamp and is preserved exactly (FR-SYN-004); CreatedAtUtc is the
// Agent's own record-creation time and is carried through only for parity with the wire contract —
// it is never written to the Alert row (ReceivedAtUtc is the server's own UtcNow at persistence).
//
// SnapshotReference is deliberately absent from this DTO entirely (FS-06 §4.3) — the Agent never
// supplies one in this increment, so there is no member to bind it to.
public sealed record DetectionEventDto(
    Guid EventId,
    string CameraId,
    DateTime DetectedAtUtc,
    DateTime CreatedAtUtc,
    int ClassId,
    string ClassName,
    double Confidence,
    int SourceId,
    long FrameNumber,
    int FrameWidth,
    int FrameHeight,
    BoundingBoxDto BoundingBox);

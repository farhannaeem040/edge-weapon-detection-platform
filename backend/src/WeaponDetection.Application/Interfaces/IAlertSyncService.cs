namespace WeaponDetection.Application.Interfaces;

// Owns the per-event Camera/Branch resolution, the idempotent-insert-with-conflict-resolution logic,
// and the batch transaction boundary for POST /api/v1/sync/events (FS-06 §5.2, §6.3). The API layer
// (SyncEventsController) is thin: it authenticates the device, binds the wire DTOs onto
// DetectionEventSyncItem, delegates here, and maps the typed outcomes back onto the wire — no EF or
// transaction logic lives in the controller.
//
// DeviceId/BranchId are supplied by the caller from the already-authenticated
// DeviceCredentialValidationResult (FS-06 §6.2) — this service never re-authenticates or re-resolves
// the device itself.
public interface IAlertSyncService
{
    Task<IReadOnlyList<SyncEventOutcome>> SyncEventsAsync(
        Guid deviceId,
        Guid branchId,
        IReadOnlyList<DetectionEventSyncItem> events,
        CancellationToken cancellationToken = default);
}

// The Application-layer bounding box carried on a DetectionEventSyncItem. Independent of any API DTO
// (mirrors NewCameraRequest/CameraMutation's independence from their API-layer counterparts) — the
// API layer maps its BoundingBoxDto onto this.
public sealed record BoundingBoxValue(double Left, double Top, double Width, double Height);

// The Application-layer shape of one submitted detection event (FS-06 §4.1), independent of the API
// DTO for the same reason NewBranchRequest/NewCameraRequest are independent of their API DTOs — this
// project never references WeaponDetection.Api. CameraId is still the Agent's local wire-format
// camera string at this point; AlertSyncService resolves it to a Camera.CameraId GUID per FS-06 §6.3.
public sealed record DetectionEventSyncItem(
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
    BoundingBoxValue BoundingBox);

// The approved wire outcome strings (FS-06 §4.2, extended by FS-09 §3/§5 with QuotaExceeded), defined
// once so the service and the controller's mapping can never drift apart.
public static class SyncEventOutcomeNames
{
    public const string Accepted = "accepted";
    public const string Duplicate = "duplicate";
    public const string Rejected = "rejected";
    public const string QuotaExceeded = "quota_exceeded";
}

// Per-event rejection reasons (FS-06 §5.2, §6.3; FS-09 §5/§7 adds BranchDailyAlertQuotaReached). Not
// itself placed on the wire — the API layer emits the string value via ToString()-free mapping in the
// controller, kept here so the service and its tests share one spelling and the controller never
// invents a new code.
public static class SyncEventErrorCodes
{
    public const string InvalidConfidence = "INVALID_CONFIDENCE";
    public const string InvalidBoundingBox = "INVALID_BOUNDING_BOX";
    public const string UnknownCamera = "UNKNOWN_CAMERA";
    public const string EventDataConflict = "EVENT_DATA_CONFLICT";
    public const string BranchDailyAlertQuotaReached = "BRANCH_DAILY_ALERT_QUOTA_REACHED";
}

public enum SyncEventOutcomeKind
{
    Accepted,
    Duplicate,
    Rejected,
    QuotaExceeded,
}

// The quota context carried on a QuotaExceeded outcome (FS-09 §5) — only the configured maximum and
// the resolved branch-local (or UTC-fallback) quota day are exposed; no internal database identifiers
// or counts are ever placed on the wire.
public sealed record SyncEventQuotaInfo(int Maximum, string LocalDate);

// One event's typed outcome from AlertSyncService.SyncEventsAsync. AlertId is populated for Accepted
// (the newly created Alert) and Duplicate (the pre-existing Alert); ErrorCode is populated for
// Rejected and QuotaExceeded; Quota is populated only for QuotaExceeded. The controller maps Kind to
// the FS-06 §4.2/FS-09 §5 wire string via SyncEventOutcomeNames.
public sealed record SyncEventOutcome(
    Guid EventId,
    SyncEventOutcomeKind Kind,
    Guid? AlertId,
    string? ErrorCode,
    SyncEventQuotaInfo? Quota = null)
{
    public static SyncEventOutcome Accepted(Guid eventId, Guid alertId) =>
        new(eventId, SyncEventOutcomeKind.Accepted, alertId, null);

    public static SyncEventOutcome Duplicate(Guid eventId, Guid alertId) =>
        new(eventId, SyncEventOutcomeKind.Duplicate, alertId, null);

    public static SyncEventOutcome Rejected(Guid eventId, string errorCode) =>
        new(eventId, SyncEventOutcomeKind.Rejected, null, errorCode);

    public static SyncEventOutcome QuotaExceeded(Guid eventId, int maximum, string localDate) =>
        new(
            eventId,
            SyncEventOutcomeKind.QuotaExceeded,
            null,
            SyncEventErrorCodes.BranchDailyAlertQuotaReached,
            new SyncEventQuotaInfo(maximum, localDate));
}

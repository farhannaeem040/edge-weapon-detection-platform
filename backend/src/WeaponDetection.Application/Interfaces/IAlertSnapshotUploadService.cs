namespace WeaponDetection.Application.Interfaces;

// Owns the whole validation/storage/attach pipeline for POST /api/v1/alerts/{alertId}/snapshot
// (FS-08 §9). The API layer (AlertSnapshotUploadController) is thin: it authenticates the device,
// binds the multipart body onto SnapshotUploadRequest, delegates here, and maps the typed outcome
// back onto the wire — no storage, hashing, or EF logic lives in the controller, mirroring how
// AlertSyncService owns SyncEventsController's real logic (FS-06 §5.2/§6.3, IP-08).
//
// BranchId is supplied by the caller from the already-authenticated DeviceCredentialValidationResult
// (FS-06 §6.2) — this service never re-authenticates the device itself; it only uses BranchId to
// verify the target Alert belongs to the authenticated device's own Branch (FS-08 §9).
public interface IAlertSnapshotUploadService
{
    Task<SnapshotUploadOutcome> UploadAsync(
        SnapshotUploadRequest request, CancellationToken cancellationToken = default);
}

// The Application-layer shape of one snapshot upload attempt (FS-08 §9). Content is the raw request
// body stream (multipart `file` part) — the service reads it at most once, buffering only as much as
// is needed to validate bounds/magic-bytes/hash before handing it to IAlertSnapshotStorage.
public sealed record SnapshotUploadRequest(
    Guid AlertId,
    Guid BranchId,
    Guid EventId,
    string? ClaimedContentType,
    string? ClaimedSha256,
    long ContentLength,
    Stream? Content);

// FS-08 §9's approved outcome set, extended with the specific rejection reasons the controller maps
// to their own distinct HTTP statuses (400/404/409/413/415) rather than a single generic "rejected".
public enum SnapshotUploadOutcomeKind
{
    Accepted,             // first successful upload — 201/200, SnapshotReference set
    Duplicate,            // identical bytes re-uploaded — 200, idempotent, no second file
    Conflict,             // different bytes for an Alert that already has a snapshot — 409
    AlertNotOwnedOrFound, // Alert does not exist, or belongs to a different Branch — 404 (non-disclosure)
    EventIdMismatch,      // eventId does not match Alert.EventId — 400
    MissingFile,          // no file part, or an empty file — 400
    Oversized,            // exceeds the configured size bound — 413
    UnsupportedMediaType, // declared content type is not image/jpeg — 415
    MalformedImage,       // claims to be a JPEG but fails magic-byte/structural validation — 400
}

// The typed result of IAlertSnapshotUploadService.UploadAsync. SnapshotReference is populated only
// for Accepted/Duplicate; ErrorCode/ErrorMessage are populated only for a rejection kind. Never
// carries a filesystem path (FS-08 §8/§9) — SnapshotReference is the opaque key only.
public sealed record SnapshotUploadOutcome(
    SnapshotUploadOutcomeKind Kind, string? SnapshotReference, string? ErrorCode, string? ErrorMessage)
{
    public static SnapshotUploadOutcome Accepted(string snapshotReference) =>
        new(SnapshotUploadOutcomeKind.Accepted, snapshotReference, null, null);

    public static SnapshotUploadOutcome Duplicate(string snapshotReference) =>
        new(SnapshotUploadOutcomeKind.Duplicate, snapshotReference, null, null);

    public static SnapshotUploadOutcome Rejected(
        SnapshotUploadOutcomeKind kind, string errorCode, string errorMessage) =>
        new(kind, null, errorCode, errorMessage);
}

// The approved wire error codes (FS-08 §9), defined once so the service and the controller's mapping
// can never drift apart — mirrors SyncEventErrorCodes' role for FS-06.
public static class SnapshotUploadErrorCodes
{
    public const string NotFound = "NOT_FOUND";
    public const string EventIdMismatch = "EVENT_ID_MISMATCH";
    public const string MissingFile = "MISSING_FILE";
    public const string FileTooLarge = "FILE_TOO_LARGE";
    public const string UnsupportedMediaType = "UNSUPPORTED_MEDIA_TYPE";
    public const string InvalidImage = "INVALID_IMAGE";
    public const string SnapshotConflict = "SNAPSHOT_CONFLICT";
}

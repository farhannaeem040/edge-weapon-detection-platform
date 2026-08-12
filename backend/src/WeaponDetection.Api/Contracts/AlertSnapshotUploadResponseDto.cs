namespace WeaponDetection.Api.Contracts;

// The outbound body for a successful POST /api/v1/alerts/{alertId}/snapshot (FS-08 §9, `data` shown
// inside the uniform envelope). Outcome is "accepted" or "duplicate" — a rejection never reaches this
// type; it goes through the uniform ApiResponse.Fail envelope instead. SnapshotReference is always
// the opaque key IAlertSnapshotStorage handed back — never a filesystem path (FS-08 §8/§9).
public sealed record AlertSnapshotUploadResponseDto(Guid AlertId, string Outcome, string SnapshotReference);

// The approved wire outcome strings for this endpoint's success responses (FS-08 §9) — defined once
// so the controller's mapping cannot silently drift from what it documents.
public static class SnapshotUploadOutcomeNames
{
    public const string Accepted = "accepted";
    public const string Duplicate = "duplicate";
}

namespace WeaponDetection.Application.Interfaces;

// Read-only Alert list/detail projections for the Admin Dashboard (FS-10 §6, IP-12 T-191/T-192).
// Mirrors IBranchService's shape: an Application-layer interface with its own request/read-model
// records, independent of both the EF entity and the API DTO. This service never writes an Alert,
// a BranchDailyAlertQuota row, or a SuppressedDetectionEvent row — it only reads what FS-06/FS-09
// already persisted.
public interface IAlertQueryService
{
    Task<AlertPageResult> ListAlertsAsync(
        AlertListQuery query, CancellationToken cancellationToken = default);

    // Null when no Alert has the given id (mapped to 404 by the API layer, FS-10 §9.3).
    Task<AlertDetailView?> GetAlertAsync(
        Guid alertId, CancellationToken cancellationToken = default);
}

// The whitelisted sort fields (FS-10 §9.2/§11) — the only two fields ExecuteAlertsAsync is permitted
// to sort by. An unrecognized SortBy value is rejected before this record is even constructed (the
// API layer validates the raw query string against this same set — see AlertListQuery.SortFields).
public enum AlertSortField
{
    DetectedAtUtc,
    ReceivedAtUtc,
}

// The Application-layer list request (FS-10 §9.2, §11). Every filter is optional; Page/PageSize are
// always present (the API layer defaults/validates them before constructing this). PageSize is
// already validated against AlertListQuery.MaxPageSize by the API layer — this type does not
// re-validate, mirroring how NewBranchRequest trusts its own already-validated shape.
public sealed record AlertListQuery(
    int Page,
    int PageSize,
    DateTime? FromUtc,
    DateTime? ToUtc,
    string? ClassName,
    Guid? BranchId,
    Guid? CameraId,
    string? Status,
    bool? SnapshotAvailable,
    AlertSortField SortBy,
    bool SortDescending)
{
    // The documented, enforced maximum (FS-10 §9.2, §11) — a page size above this is a validation
    // error at the API layer, never silently clamped here.
    public const int MaxPageSize = 100;
    public const int DefaultPageSize = 25;
}

// One bounded page of Alert list rows plus the total count needed to render pagination controls
// (FS-10 §9.2). Items is never the full unbounded Alert table — ListAlertsAsync applies Page/PageSize
// as a SQL Skip/Take, never an in-memory post-filter of an unbounded query.
public sealed record AlertPageResult(
    IReadOnlyList<AlertListItemView> Items,
    int TotalCount,
    int Page,
    int PageSize);

// One row in the Alert list (FS-10 §9.2). BranchName/CameraName/DeviceExternalId are pre-resolved by
// the join in AlertQueryService — the API layer never performs a second lookup. SnapshotAvailable is
// a boolean derived from SnapshotReference being non-null; the raw reference value is never exposed
// here or anywhere in this feature (FS-10 §6, §10).
public sealed record AlertListItemView(
    Guid AlertId,
    DateTime DetectedAtUtc,
    DateTime ReceivedAtUtc,
    string ClassName,
    double Confidence,
    Guid BranchId,
    string BranchName,
    Guid CameraId,
    string CameraName,
    Guid DeviceExternalId,
    string Status,
    bool SnapshotAvailable);

// The single-Alert detail view (FS-10 §9.3). Never carries SnapshotReference's raw value,
// DeviceRecordId, or any secret — only a safe SnapshotState the API layer maps 1:1 onto the wire.
public sealed record AlertDetailView(
    Guid AlertId,
    Guid EventId,
    DateTime DetectedAtUtc,
    DateTime ReceivedAtUtc,
    int ClassId,
    string ClassName,
    double Confidence,
    Guid BranchId,
    string BranchName,
    Guid CameraId,
    string CameraName,
    Guid DeviceExternalId,
    string Status,
    bool SnapshotAvailable);

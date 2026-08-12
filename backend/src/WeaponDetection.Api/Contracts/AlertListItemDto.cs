using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.Api.Contracts;

// One row in the paginated Alert list (FS-10 §9.2). SnapshotAvailable is a boolean only — the raw
// SnapshotReference value (an opaque storage key, FS-08 §8) is never serialized by this feature.
public sealed record AlertListItemDto(
    Guid AlertId,
    DateTime DetectedAtUtc,
    DateTime ReceivedAtUtc,
    string ClassName,
    double Confidence,
    Guid BranchId,
    string BranchName,
    Guid CameraId,
    string CameraName,
    Guid DeviceId,
    string Status,
    bool SnapshotAvailable)
{
    public static AlertListItemDto From(AlertListItemView view) =>
        new(
            view.AlertId,
            view.DetectedAtUtc,
            view.ReceivedAtUtc,
            view.ClassName,
            view.Confidence,
            view.BranchId,
            view.BranchName,
            view.CameraId,
            view.CameraName,
            view.DeviceExternalId,
            view.Status,
            view.SnapshotAvailable);
}

// The paginated envelope (FS-10 §9.2) — Items is always a bounded page, never the full Alert table.
public sealed record AlertListResponseDto(
    IReadOnlyList<AlertListItemDto> Items,
    int Page,
    int PageSize,
    int TotalCount,
    int TotalPages)
{
    public static AlertListResponseDto From(AlertPageResult result) =>
        new(
            result.Items.Select(AlertListItemDto.From).ToList(),
            result.Page,
            result.PageSize,
            result.TotalCount,
            result.PageSize <= 0 ? 0 : (int)Math.Ceiling(result.TotalCount / (double)result.PageSize));
}

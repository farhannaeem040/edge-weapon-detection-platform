using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.Api.Contracts;

// The single-Alert detail response (FS-10 §9.3). Never carries SnapshotReference's raw value,
// DeviceRecordId, or any secret. DeliveryLatencySeconds is derived here (ReceivedAtUtc - DetectedAtUtc)
// rather than stored, since it is purely a presentation convenience over two already-returned
// timestamps.
public sealed record AlertDetailDto(
    Guid AlertId,
    Guid EventId,
    DateTime DetectedAtUtc,
    DateTime ReceivedAtUtc,
    double DeliveryLatencySeconds,
    int ClassId,
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
    public static AlertDetailDto From(AlertDetailView view) =>
        new(
            view.AlertId,
            view.EventId,
            view.DetectedAtUtc,
            view.ReceivedAtUtc,
            Math.Max(0, (view.ReceivedAtUtc - view.DetectedAtUtc).TotalSeconds),
            view.ClassId,
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

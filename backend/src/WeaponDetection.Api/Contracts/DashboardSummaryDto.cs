using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.Api.Contracts;

// The bounded Admin Dashboard summary (FS-10 §9.1) — matches the task's example shape exactly. No
// Device shared secret, protected secret, Activation Key, or Data Protection material is ever
// reachable from this type.
public sealed record DashboardSummaryDto(
    DashboardBranchDto Branch,
    DashboardAlertsDto Alerts,
    DashboardSuppressionsDto Suppressions,
    DashboardSystemDto System)
{
    public static DashboardSummaryDto From(DashboardSummaryView view) =>
        new(
            new DashboardBranchDto(
                view.BranchId, view.BranchName, view.TimeZoneId, view.LocalDate, view.NextQuotaResetAtUtc),
            new DashboardAlertsDto(
                view.AlertsToday, view.ConfiguredMaximum, view.Remaining, view.LatestAlertAtUtc),
            new DashboardSuppressionsDto(view.SuppressedTotal, view.SuppressedGun, view.SuppressedKnife),
            new DashboardSystemDto(view.DeviceCount, view.CameraCount));
}

public sealed record DashboardBranchDto(
    Guid Id, string Name, string? TimeZoneId, string LocalDate, DateTime NextQuotaResetAtUtc);

public sealed record DashboardAlertsDto(
    int Today, int ConfiguredMaximum, int Remaining, DateTime? LatestAlertAtUtc);

public sealed record DashboardSuppressionsDto(int Total, int Gun, int Knife);

public sealed record DashboardSystemDto(int DeviceCount, int CameraCount);

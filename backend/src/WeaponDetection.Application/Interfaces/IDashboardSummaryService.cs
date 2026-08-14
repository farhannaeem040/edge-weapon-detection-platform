namespace WeaponDetection.Application.Interfaces;

// Bounded, read-only operational summary for the Admin Dashboard (FS-10 §6, §9.1, IP-12 T-193). Reads
// only what FS-06/FS-09 already persisted (Branch, Alert, BranchDailyAlertQuota, Device, Camera) — it
// never writes a quota row, never enforces the quota, and never mutates an Alert.
public interface IDashboardSummaryService
{
    // The summary is always for one explicitly identified Branch (manual-review Correction 3) — there
    // is no "default"/first-Branch fallback. Null when `branchId` does not resolve to any Branch
    // (never existed, or was deleted) — mapped by the API layer to an explicit "unavailable" response,
    // never a fabricated zero-state (FS-10 Phase 12: "do not display zero as though it were
    // confirmed") and never another Branch's data.
    Task<DashboardSummaryView?> GetSummaryAsync(Guid branchId, CancellationToken cancellationToken = default);
}

public sealed record DashboardSummaryView(
    Guid BranchId,
    string BranchName,
    string? TimeZoneId,
    string LocalDate,
    DateTime NextQuotaResetAtUtc,
    int AlertsToday,
    int ConfiguredMaximum,
    int Remaining,
    DateTime? LatestAlertAtUtc,
    int SuppressedTotal,
    int SuppressedGun,
    int SuppressedKnife,
    int DeviceCount,
    int CameraCount);

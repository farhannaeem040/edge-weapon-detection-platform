using WeaponDetection.Application.Analytics;
using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.Api.Contracts;

// The wire shape of GET /api/v1/analytics/operational (FS-15 §6.1) — wrapped in the standard
// {success, message, data} envelope by ApiEnvelopeResultFilter, exactly like DashboardSummaryDto.
//
// Every field here is either read from the database or derived by a formula stated in FS-15 §5. No
// value is defaulted to a plausible-looking constant, and no field name claims a meaning the data
// cannot support — in particular there is no "accuracy", "precision", "confirmationRate", or
// "responseTime" member anywhere in this contract, because the schema contains no ground truth and no
// operator-response timestamp (FS-15 §3.4).
//
// Nothing reachable from this type exposes a Device shared secret, protected secret, Activation Key,
// Data Protection material, RTSP URL, CameraKey, snapshot reference, or filesystem path.
public sealed record OperationalAnalyticsDto(
    AnalyticsFiltersDto Filters,
    AnalyticsSummaryDto Summary,
    IReadOnlyList<AnalyticsTimeBucketDto> DetectionsOverTime,
    IReadOnlyList<AnalyticsBranchCountDto> DetectionsByBranch,
    IReadOnlyList<AnalyticsLatencyBucketDto> LatencyOverTime)
{
    public static OperationalAnalyticsDto From(OperationalAnalyticsView view, AnalyticsRange? range) =>
        new(
            new AnalyticsFiltersDto(
                range is { } value ? AnalyticsWindow.ToWireValue(value) : null,
                view.Filters.FromUtc,
                view.Filters.ToUtc,
                BucketWireValue(view.Filters.Bucket),
                view.Filters.BranchId,
                view.Filters.BranchName,
                view.Filters.DetectionType),
            AnalyticsSummaryDto.From(view.Summary),
            view.DetectionsOverTime
                .Select(point => new AnalyticsTimeBucketDto(point.PeriodStartUtc, point.Count))
                .ToList(),
            view.DetectionsByBranch
                .Select(point => new AnalyticsBranchCountDto(point.BranchId, point.BranchName, point.Count))
                .ToList(),
            view.LatencyOverTime
                .Select(point => new AnalyticsLatencyBucketDto(
                    point.PeriodStartUtc, point.AverageMs, point.SampleCount))
                .ToList());

    // Lower-case wire spelling, so the client switches on "hour"/"day"/"week" rather than on a .NET
    // enum member name that could be renamed without anyone noticing the contract changed.
    public static string BucketWireValue(AnalyticsBucket bucket) => bucket switch
    {
        AnalyticsBucket.Hour => "hour",
        AnalyticsBucket.Week => "week",
        _ => "day",
    };
}

// The echo of exactly what was queried (FS-15 §6.1). `range` is null when the caller supplied an
// absolute fromUtc/toUtc pair instead of a preset. `branchName` is resolved server-side — the client
// identifies a Branch only by `branchId` (FS-15 §4.2).
public sealed record AnalyticsFiltersDto(
    string? Range,
    DateTime FromUtc,
    DateTime ToUtc,
    string Bucket,
    Guid? BranchId,
    string? BranchName,
    string? DetectionType);

// The scalar summary block (FS-15 §5.5). Every nullable member is null specifically when the metric is
// *unavailable* for the selected filters — never zero — so the UI can keep "no data" and "a measured
// zero" visually distinct (FS-15 §7).
public sealed record AnalyticsSummaryDto(
    int TotalDetections,
    int SuppressedDetections,
    double? MeanConfidence,
    int ConfidenceHigh,
    int ConfidenceMedium,
    int ConfidenceLow,
    double HighConfidenceThreshold,
    double MediumConfidenceThreshold,
    double? AverageDeliveryLatencyMs,
    double? MedianDeliveryLatencyMs,
    double? MaxDeliveryLatencyMs,
    int LatencySampleCount,
    int LatencySamplesExcluded,
    int AlertsWithSnapshot,
    int BranchCount,
    int CameraCount,
    int DeviceCount,
    bool ValidationDataAvailable,
    DateTime GeneratedAtUtc)
{
    public static AnalyticsSummaryDto From(AnalyticsSummaryView view) =>
        new(
            view.TotalDetections,
            view.SuppressedDetections,
            view.MeanConfidence,
            view.ConfidenceHigh,
            view.ConfidenceMedium,
            view.ConfidenceLow,
            // The band thresholds travel with the data so the UI's legend cannot drift from the
            // Backend's bucketing (FS-15 §5.2) — the client renders "≥ 75%" from this value rather
            // than from a second hard-coded copy.
            AnalyticsSummaryView.HighConfidenceThreshold,
            AnalyticsSummaryView.MediumConfidenceThreshold,
            view.AverageDeliveryLatencyMs,
            view.MedianDeliveryLatencyMs,
            view.MaxDeliveryLatencyMs,
            view.LatencySampleCount,
            view.LatencySamplesExcluded,
            view.AlertsWithSnapshot,
            view.BranchCount,
            view.CameraCount,
            view.DeviceCount,
            view.ValidationDataAvailable,
            view.GeneratedAtUtc);
}

public sealed record AnalyticsTimeBucketDto(DateTime PeriodStartUtc, int Count);

public sealed record AnalyticsBranchCountDto(Guid BranchId, string BranchName, int Count);

// AverageMs is null — not 0 — for a bucket with no eligible latency sample, so the client draws a gap
// rather than a false "instant delivery" (FS-15 §5.4).
public sealed record AnalyticsLatencyBucketDto(DateTime PeriodStartUtc, double? AverageMs, int SampleCount);

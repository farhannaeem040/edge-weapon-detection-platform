namespace WeaponDetection.Application.Interfaces;

// The read-only Operational Analytics projection for the Admin dashboard (FS-15 §5/§6, IP-17 T-1/T-3).
//
// Mirrors IAlertQueryService's shape: an Application-layer interface with its own request/read-model
// records, independent of both the EF entities and the API DTOs. Every method here is strictly
// read-only — this service never writes an Alert, a BranchDailyAlertQuota row, or a
// SuppressedDetectionEvent row, and never mutates detection data as a side effect of reporting on it
// (FS-15 §1, Phase 5 query rules).
public interface IOperationalAnalyticsService
{
    // Null when Query.BranchId is set but resolves to no Branch (mapped to 404 by the API layer,
    // FS-15 §4.2) — deliberately distinct from "this Branch simply had no Alerts", which is a
    // successful response with zero counts.
    Task<OperationalAnalyticsView?> GetOperationalAsync(
        AnalyticsQuery query, CancellationToken cancellationToken = default);

    // The row-level CSV export projection (FS-15 §6.2). Null carries the same "no such Branch"
    // meaning as above. Bounded by AnalyticsQuery.MaxExportRows + 1 so the API layer can detect an
    // over-large result and reject it rather than silently truncating the file.
    Task<IReadOnlyList<AnalyticsExportRow>?> GetExportRowsAsync(
        AnalyticsQuery query, CancellationToken cancellationToken = default);
}

// The bucket granularity a resolved window is grouped by (FS-15 §4.1). Never client-supplied — it is
// derived from the range preset, so a caller cannot ask for 90 days of hourly buckets.
public enum AnalyticsBucket
{
    Hour,
    Day,
    Week,
}

// The already-validated, already-resolved analytics request (FS-15 §4). The API layer resolves the
// range preset into an absolute UTC window and validates BranchId/DetectionType *before* constructing
// this record, mirroring how AlertListQuery trusts its own already-validated shape.
//
// FromUtc is inclusive and ToUtc exclusive, matching AlertQueryService's existing convention, so an
// Alert can never fall into two adjacent windows or two adjacent buckets.
public sealed record AnalyticsQuery(
    DateTime FromUtc,
    DateTime ToUtc,
    AnalyticsBucket Bucket,
    Guid? BranchId,
    string? DetectionType)
{
    // FS-15 §6.2: the export is bounded rather than streamed unboundedly. A filter matching more than
    // this is a 400 telling the Admin to narrow the range — never a silently truncated file.
    public const int MaxExportRows = 50_000;

    // FS-15 §4.1: the widest window any caller may request, so a single analytics call can never be
    // turned into an unbounded table scan of years of data.
    public const int MaxRangeDays = 366;
}

// The complete payload of GET /api/v1/analytics/operational (FS-15 §6.1). Every series is already
// zero-filled across the window's buckets by the service, so the API and the UI never have to infer a
// missing bucket.
public sealed record OperationalAnalyticsView(
    AnalyticsFiltersView Filters,
    AnalyticsSummaryView Summary,
    IReadOnlyList<TimeBucketPoint> DetectionsOverTime,
    IReadOnlyList<BranchCountPoint> DetectionsByBranch,
    IReadOnlyList<LatencyBucketPoint> LatencyOverTime);

// The echo of exactly what was queried (FS-15 §6.1) — so a screenshot of the dashboard is
// self-describing, and so the client never has to trust its own idea of the window. BranchName is
// resolved server-side from BranchId; the client never identifies a Branch by name (FS-15 §4.2).
public sealed record AnalyticsFiltersView(
    DateTime FromUtc,
    DateTime ToUtc,
    AnalyticsBucket Bucket,
    Guid? BranchId,
    string? BranchName,
    string? DetectionType);

// The scalar summary block (FS-15 §5.5). Every nullable field is null specifically when the metric is
// *unavailable* for the selected filters, never zero — "no data" and "a measured zero" must remain
// distinguishable in the UI (FS-15 §7, Phase 18).
public sealed record AnalyticsSummaryView(
    int TotalDetections,
    int SuppressedDetections,
    double? MeanConfidence,
    int ConfidenceHigh,
    int ConfidenceMedium,
    int ConfidenceLow,
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
    // FS-15 §5.2: the confidence bands are display-layer bucketing of Alert.Confidence, and the
    // thresholds live here so the Backend, the DTO documentation, and the UI's own legend cannot
    // drift apart.
    public const double HighConfidenceThreshold = 0.75;
    public const double MediumConfidenceThreshold = 0.50;
}

// One bucket of the Detections Over Time series (FS-15 §5.1). PeriodStartUtc is the inclusive start of
// the bucket; the exclusive end is the next bucket's start (or the window's ToUtc for the last one).
public sealed record TimeBucketPoint(DateTime PeriodStartUtc, int Count);

// One bar of the Alert Density by Branch series (FS-15 §5.3). BranchName comes from the database, never
// from a mockup.
public sealed record BranchCountPoint(Guid BranchId, string BranchName, int Count);

// One bucket of the Alert Delivery Latency series (FS-15 §5.4). AverageMs is null — not zero — when the
// bucket contains no eligible sample, so the UI renders a gap rather than a false "instant delivery".
public sealed record LatencyBucketPoint(DateTime PeriodStartUtc, double? AverageMs, int SampleCount);

// One row of the CSV export (FS-15 §6.2). Deliberately carries no RtspUrl, CameraKey, SnapshotReference,
// filesystem path, DeviceRecordId, or any secret — the same "safe projection only" posture
// AlertListItemView already takes. DeliveryLatencyMs is null for a row excluded by the §5.4
// eligibility rule and is rendered as an empty CSV cell, never as a misleading number.
public sealed record AnalyticsExportRow(
    DateTime DetectedAtUtc,
    DateTime ReceivedAtUtc,
    double? DeliveryLatencyMs,
    string BranchName,
    string CameraName,
    string DetectionType,
    double Confidence,
    string Status,
    bool SnapshotAvailable);

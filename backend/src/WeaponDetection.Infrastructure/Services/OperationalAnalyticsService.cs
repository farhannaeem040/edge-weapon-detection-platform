using Microsoft.EntityFrameworkCore;
using WeaponDetection.Application.Analytics;
using WeaponDetection.Application.Interfaces;
using WeaponDetection.Domain;
using WeaponDetection.Infrastructure.Persistence;

namespace WeaponDetection.Infrastructure.Services;

// The read-only Operational Analytics aggregations (FS-15 §5, IP-17 T-3).
//
// Follows AlertQueryService exactly: it depends on WeaponDetectionDbContext directly, lives in
// Infrastructure/Services behind an Application interface, and every query is AsNoTracking. Alert
// carries no navigation properties, so a Branch is reached only through the authoritative
// Alert.CameraId → Camera.BranchId path — never inferred from Alert.DeviceId (FS-15 §4.2).
//
// Aggregation discipline (FS-15 Phase 5 rules): every count, sum, average and ordering below is
// executed by SQL Server. Nothing loads the Alerts table into memory. The only in-memory work is
// (a) zero-filling buckets the database legitimately returned no row for, and (b) rolling
// day-grouped rows up into week buckets — both operating on at most ~90 already-aggregated rows,
// and neither expressible portably in LINQ.
//
// This service performs no writes of any kind. Reporting on detection data never mutates it.
public class OperationalAnalyticsService : IOperationalAnalyticsService
{
    private readonly WeaponDetectionDbContext _dbContext;
    private readonly TimeProvider _timeProvider;

    public OperationalAnalyticsService(WeaponDetectionDbContext dbContext, TimeProvider timeProvider)
    {
        _dbContext = dbContext ?? throw new ArgumentNullException(nameof(dbContext));
        _timeProvider = timeProvider ?? throw new ArgumentNullException(nameof(timeProvider));
    }

    public async Task<OperationalAnalyticsView?> GetOperationalAsync(
        AnalyticsQuery query, CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(query);

        // A named Branch that no longer resolves is a distinct outcome from "this Branch had no
        // Alerts" (FS-15 §4.2) — resolved first so the caller can answer 404 rather than rendering an
        // empty dashboard that looks like a real, quiet Branch.
        string? branchName = null;
        if (query.BranchId is { } requestedBranchId)
        {
            branchName = await _dbContext.Branches
                .AsNoTracking()
                .Where(b => b.BranchId == requestedBranchId)
                .Select(b => b.Name)
                .SingleOrDefaultAsync(cancellationToken);

            if (branchName is null)
            {
                return null;
            }
        }

        var qualifying = QualifyingAlerts(query);

        var summary = await BuildSummaryAsync(query, qualifying, cancellationToken);
        var detectionsOverTime = await BuildDetectionsOverTimeAsync(query, qualifying, cancellationToken);
        var detectionsByBranch = await BuildDetectionsByBranchAsync(query, qualifying, cancellationToken);
        var latencyOverTime = await BuildLatencyOverTimeAsync(query, qualifying, cancellationToken);

        return new OperationalAnalyticsView(
            new AnalyticsFiltersView(
                query.FromUtc, query.ToUtc, query.Bucket, query.BranchId, branchName, query.DetectionType),
            summary,
            detectionsOverTime,
            detectionsByBranch,
            latencyOverTime);
    }

    public async Task<IReadOnlyList<AnalyticsExportRow>?> GetExportRowsAsync(
        AnalyticsQuery query, CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(query);

        if (query.BranchId is { } requestedBranchId)
        {
            var branchExists = await _dbContext.Branches
                .AsNoTracking()
                .AnyAsync(b => b.BranchId == requestedBranchId, cancellationToken);

            if (!branchExists)
            {
                return null;
            }
        }

        // The Branch/Camera names are joined here rather than being carried through the qualifying set:
        // only the export needs them, and keeping them out of the aggregation queries is what lets
        // those stay plain grouped scans.
        var rows =
            from alert in QualifyingAlerts(query)
            join camera in _dbContext.Cameras.AsNoTracking() on alert.CameraId equals camera.CameraId
            join branch in _dbContext.Branches.AsNoTracking() on camera.BranchId equals branch.BranchId
            orderby alert.DetectedAtUtc
            select new AnalyticsExportRow(
                alert.DetectedAtUtc,
                alert.ReceivedAtUtc,
                // The §5.4 eligibility rule again, per row: a row that fails it exports a blank cell,
                // never a misleading number.
                alert.ReceivedAtUtc >= alert.DetectedAtUtc
                    && alert.ReceivedAtUtc <= alert.DetectedAtUtc.AddDays(1)
                        ? EF.Functions.DateDiffMillisecond(alert.DetectedAtUtc, alert.ReceivedAtUtc)
                        : (double?)null,
                branch.Name,
                camera.Name,
                alert.ClassName,
                alert.Confidence,
                alert.Status.ToString(),
                alert.SnapshotReference != null);

        // MaxExportRows + 1 so the API layer can tell "exactly at the limit" from "over the limit" and
        // reject the latter, rather than handing back a file that is silently missing rows
        // (FS-15 §6.2).
        return await rows.Take(AnalyticsQuery.MaxExportRows + 1).ToListAsync(cancellationToken);
    }

    // The qualifying set `Q` of FS-15 §5, expressed once so no two cards can disagree about what is
    // being counted. Kept as IQueryable<Alert> — every consumer below composes further SQL onto it
    // rather than enumerating it.
    //
    // The Branch predicate is an EXISTS over Cameras rather than a JOIN, deliberately: it keeps this a
    // single-entity queryable that GroupBy/Average/OrderBy can be composed onto without EF having to
    // carry a transparent identifier through every downstream aggregate.
    //
    // Membership always requires the Alert's Camera row to still exist. An Alert orphaned by a deleted
    // Camera/Branch cannot be attributed to any Branch, so counting it in the total while it is absent
    // from every Branch bar would make the two cards disagree — and the existing Alert list already
    // hides such rows behind the same inner join.
    private IQueryable<Alert> QualifyingAlerts(AnalyticsQuery query)
    {
        var cameras = _dbContext.Cameras.AsNoTracking();

        if (query.BranchId is { } branchId)
        {
            cameras = cameras.Where(c => c.BranchId == branchId);
        }

        // Filtered on DetectedAtUtc (when the weapon was seen), never ReceivedAtUtc (when the row was
        // written) — a store-and-forward backlog replay must not appear as a detection spike on the
        // day it was uploaded (FS-15 §5.1).
        var alerts = _dbContext.Alerts
            .AsNoTracking()
            .Where(a => a.DetectedAtUtc >= query.FromUtc && a.DetectedAtUtc < query.ToUtc)
            .Where(a => cameras.Any(c => c.CameraId == a.CameraId));

        if (query.DetectionType is { } detectionType)
        {
            alerts = alerts.Where(a => a.ClassName == detectionType);
        }

        return alerts;
    }

    private async Task<AnalyticsSummaryView> BuildSummaryAsync(
        AnalyticsQuery query, IQueryable<Alert> qualifying, CancellationToken cancellationToken)
    {
        // One round trip for every count/average over Q. Sum(condition ? 1 : 0) rather than
        // Count(predicate) because it translates to a plain SUM(CASE …); Average is projected as
        // double? so an empty set yields null (SQL AVG over no rows) rather than a fabricated 0
        // (FS-15 §5.5).
        var counters = await qualifying
            .GroupBy(_ => 1)
            .Select(g => new
            {
                Total = g.Count(),
                MeanConfidence = (double?)g.Average(a => a.Confidence),
                High = g.Sum(a => a.Confidence >= AnalyticsSummaryView.HighConfidenceThreshold ? 1 : 0),
                Medium = g.Sum(a =>
                    a.Confidence >= AnalyticsSummaryView.MediumConfidenceThreshold
                    && a.Confidence < AnalyticsSummaryView.HighConfidenceThreshold ? 1 : 0),
                Low = g.Sum(a => a.Confidence < AnalyticsSummaryView.MediumConfidenceThreshold ? 1 : 0),
                WithSnapshot = g.Sum(a => a.SnapshotReference != null ? 1 : 0),
            })
            .SingleOrDefaultAsync(cancellationToken);

        var total = counters?.Total ?? 0;

        // The §5.4 eligibility filter is applied as a WHERE, so SQL Server evaluates
        // DATEDIFF(millisecond, …) only over surviving rows — which is also what keeps it inside int
        // range (it overflows past ≈24.8 days).
        var eligible = EligibleLatencySamples(qualifying);

        var latency = await eligible
            .GroupBy(_ => 1)
            .Select(g => new
            {
                Count = g.Count(),
                Average = (double?)g.Average(a =>
                    (double)EF.Functions.DateDiffMillisecond(a.DetectedAtUtc, a.ReceivedAtUtc)),
                Max = (double?)g.Max(a =>
                    (double)EF.Functions.DateDiffMillisecond(a.DetectedAtUtc, a.ReceivedAtUtc)),
            })
            .SingleOrDefaultAsync(cancellationToken);

        var latencySampleCount = latency?.Count ?? 0;

        // The lower median, fetched by an ORDER BY … OFFSET … FETCH rather than materialising the
        // sample set. Reported alongside the mean because the distribution stays right-skewed even
        // after the eligibility rule (FS-15 §5.4).
        double? median = null;
        if (latencySampleCount > 0)
        {
            median = await eligible
                .Select(a => (double)EF.Functions.DateDiffMillisecond(a.DetectedAtUtc, a.ReceivedAtUtc))
                .OrderBy(value => value)
                .Skip((latencySampleCount - 1) / 2)
                .Take(1)
                .SingleAsync(cancellationToken);
        }

        // Quota-suppressed detections are counted for context and never folded into totalDetections
        // (FS-15 §5.1/§5.5). They are attributed by their own stored BranchId — the authoritative
        // column for that row type — not through a Camera join they do not have.
        var suppressed = _dbContext.SuppressedDetectionEvents
            .AsNoTracking()
            .Where(s => s.DetectedAtUtc >= query.FromUtc && s.DetectedAtUtc < query.ToUtc);

        if (query.BranchId is { } suppressedBranchId)
        {
            suppressed = suppressed.Where(s => s.BranchId == suppressedBranchId);
        }

        if (query.DetectionType is { } suppressedClass)
        {
            suppressed = suppressed.Where(s => s.ClassName == suppressedClass);
        }

        var suppressedCount = await suppressed.CountAsync(cancellationToken);

        var branchCount = await FilteredBranches(query).CountAsync(cancellationToken);

        var cameras = _dbContext.Cameras.AsNoTracking();
        var devices = _dbContext.Devices.AsNoTracking();
        if (query.BranchId is { } fleetBranchId)
        {
            cameras = cameras.Where(c => c.BranchId == fleetBranchId);
            devices = devices.Where(d => d.BranchId == fleetBranchId);
        }

        var cameraCount = await cameras.CountAsync(cancellationToken);
        var deviceCount = await devices.CountAsync(cancellationToken);

        return new AnalyticsSummaryView(
            total,
            suppressedCount,
            counters?.MeanConfidence,
            counters?.High ?? 0,
            counters?.Medium ?? 0,
            counters?.Low ?? 0,
            latencySampleCount > 0 ? latency?.Average : null,
            median,
            latencySampleCount > 0 ? latency?.Max : null,
            latencySampleCount,
            total - latencySampleCount,
            counters?.WithSnapshot ?? 0,
            branchCount,
            cameraCount,
            deviceCount,
            // FS-15 §3.4: AlertStatus has exactly one member, so no confirm/false-positive ground
            // truth exists anywhere in the schema. Stated as an explicit, machine-readable flag so the
            // UI never has to infer it — and so this becomes a one-line change if a future feature
            // adds an operator-review workflow.
            ValidationDataAvailable: false,
            _timeProvider.GetUtcNow().UtcDateTime);
    }

    private async Task<IReadOnlyList<TimeBucketPoint>> BuildDetectionsOverTimeAsync(
        AnalyticsQuery query, IQueryable<Alert> qualifying, CancellationToken cancellationToken)
    {
        // The bucket-start DateTime is composed after materialisation: SQL Server groups by the
        // date-part key, and the (at most ~90) returned rows are turned into instants here, because a
        // DateTime constructor is not translatable inside a projection.
        List<RawBucketCount> rows;

        if (query.Bucket == AnalyticsBucket.Hour)
        {
            var raw = await qualifying
                .GroupBy(a => new
                {
                    a.DetectedAtUtc.Year,
                    a.DetectedAtUtc.Month,
                    a.DetectedAtUtc.Day,
                    a.DetectedAtUtc.Hour,
                })
                .Select(g => new { g.Key.Year, g.Key.Month, g.Key.Day, g.Key.Hour, Count = g.Count() })
                .ToListAsync(cancellationToken);

            rows = raw
                .Select(r => new RawBucketCount(
                    new DateTime(r.Year, r.Month, r.Day, r.Hour, 0, 0, DateTimeKind.Utc), r.Count))
                .ToList();
        }
        else
        {
            var raw = await qualifying
                .GroupBy(a => a.DetectedAtUtc.Date)
                .Select(g => new { Day = g.Key, Count = g.Count() })
                .ToListAsync(cancellationToken);

            rows = raw.Select(r => new RawBucketCount(r.Day, r.Count)).ToList();
        }

        // Day rows are rolled up to their Monday for a week bucket. This keeps ISO week-numbering out
        // of SQL, where it varies with server DATEFIRST/language settings.
        var counts = rows
            .GroupBy(row => AnalyticsWindow.TruncateToBucketStart(row.PeriodStartUtc, query.Bucket))
            .ToDictionary(group => group.Key, group => group.Sum(row => row.Count));

        // Zero-count buckets are emitted, not omitted (FS-15 §5.1), so a quiet period reads as quiet
        // rather than being compressed out of the axis.
        return AnalyticsWindow.EnumerateBuckets(query.FromUtc, query.ToUtc, query.Bucket)
            .Select(start => new TimeBucketPoint(start, counts.GetValueOrDefault(start)))
            .ToList();
    }

    private async Task<IReadOnlyList<BranchCountPoint>> BuildDetectionsByBranchAsync(
        AnalyticsQuery query, IQueryable<Alert> qualifying, CancellationToken cancellationToken)
    {
        // Two queries rather than a correlated per-Branch subquery: the Branch list is small, and this
        // keeps the aggregation itself a single grouped scan.
        var branches = await FilteredBranches(query)
            .Select(b => new { b.BranchId, b.Name })
            .ToListAsync(cancellationToken);

        var counts = await (
                from alert in qualifying
                join camera in _dbContext.Cameras.AsNoTracking() on alert.CameraId equals camera.CameraId
                group alert by camera.BranchId into grouped
                select new { BranchId = grouped.Key, Count = grouped.Count() })
            .ToDictionaryAsync(row => row.BranchId, row => row.Count, cancellationToken);

        // Branches with no qualifying Alert are included at zero (FS-15 §5.3), so "this Branch is
        // quiet" stays distinguishable from "this Branch does not exist". Descending by count, then by
        // name for a stable tie-break.
        return branches
            .Select(b => new BranchCountPoint(b.BranchId, b.Name, counts.GetValueOrDefault(b.BranchId)))
            .OrderByDescending(point => point.Count)
            .ThenBy(point => point.BranchName, StringComparer.OrdinalIgnoreCase)
            .ToList();
    }

    private async Task<IReadOnlyList<LatencyBucketPoint>> BuildLatencyOverTimeAsync(
        AnalyticsQuery query, IQueryable<Alert> qualifying, CancellationToken cancellationToken)
    {
        var eligible = EligibleLatencySamples(qualifying);

        // Sum + count rather than average, so day rows can be rolled up into a week bucket without
        // averaging averages (which would silently weight a quiet day the same as a busy one).
        List<RawLatencyBucket> rows;

        if (query.Bucket == AnalyticsBucket.Hour)
        {
            var raw = await eligible
                .GroupBy(a => new
                {
                    a.DetectedAtUtc.Year,
                    a.DetectedAtUtc.Month,
                    a.DetectedAtUtc.Day,
                    a.DetectedAtUtc.Hour,
                })
                .Select(g => new
                {
                    g.Key.Year,
                    g.Key.Month,
                    g.Key.Day,
                    g.Key.Hour,
                    SumMs = g.Sum(a =>
                        (double)EF.Functions.DateDiffMillisecond(a.DetectedAtUtc, a.ReceivedAtUtc)),
                    Count = g.Count(),
                })
                .ToListAsync(cancellationToken);

            rows = raw
                .Select(r => new RawLatencyBucket(
                    new DateTime(r.Year, r.Month, r.Day, r.Hour, 0, 0, DateTimeKind.Utc), r.SumMs, r.Count))
                .ToList();
        }
        else
        {
            var raw = await eligible
                .GroupBy(a => a.DetectedAtUtc.Date)
                .Select(g => new
                {
                    Day = g.Key,
                    SumMs = g.Sum(a =>
                        (double)EF.Functions.DateDiffMillisecond(a.DetectedAtUtc, a.ReceivedAtUtc)),
                    Count = g.Count(),
                })
                .ToListAsync(cancellationToken);

            rows = raw.Select(r => new RawLatencyBucket(r.Day, r.SumMs, r.Count)).ToList();
        }

        var rolled = rows
            .GroupBy(row => AnalyticsWindow.TruncateToBucketStart(row.PeriodStartUtc, query.Bucket))
            .ToDictionary(
                group => group.Key,
                group => (Sum: group.Sum(row => row.SumMs), Count: group.Sum(row => row.Count)));

        // A bucket with no eligible sample reports null, never 0 — the UI renders a gap rather than a
        // false "instant delivery" (FS-15 §5.4).
        return AnalyticsWindow.EnumerateBuckets(query.FromUtc, query.ToUtc, query.Bucket)
            .Select(start => rolled.TryGetValue(start, out var value) && value.Count > 0
                ? new LatencyBucketPoint(start, value.Sum / value.Count, value.Count)
                : new LatencyBucketPoint(start, null, 0))
            .ToList();
    }

    // FS-15 §5.4's sample-eligibility rule. A negative latency can only be clock skew; a latency above
    // one day is a store-and-forward backlog replay measuring an Agent outage rather than delivery
    // performance. Both are excluded, and the excluded count is always reported (never silently
    // dropped) via AnalyticsSummaryView.LatencySamplesExcluded.
    private static IQueryable<Alert> EligibleLatencySamples(IQueryable<Alert> qualifying) =>
        qualifying.Where(a =>
            a.ReceivedAtUtc >= a.DetectedAtUtc && a.ReceivedAtUtc <= a.DetectedAtUtc.AddDays(1));

    private IQueryable<Branch> FilteredBranches(AnalyticsQuery query)
    {
        var branches = _dbContext.Branches.AsNoTracking();
        return query.BranchId is { } branchId ? branches.Where(b => b.BranchId == branchId) : branches;
    }

    private sealed record RawBucketCount(DateTime PeriodStartUtc, int Count);

    private sealed record RawLatencyBucket(DateTime PeriodStartUtc, double SumMs, int Count);
}

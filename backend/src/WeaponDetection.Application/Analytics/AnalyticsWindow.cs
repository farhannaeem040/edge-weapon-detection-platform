using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.Application.Analytics;

// The range preset → absolute UTC window → bucket-granularity resolution for FS-15 §4.1 (IP-17 T-2).
//
// Deliberately pure and static: it takes `nowUtc` as a parameter rather than reading a clock, so the
// API layer supplies the injected TimeProvider's value and every boundary rule here is directly
// unit-testable without a host. It lives in the Application layer because both the API layer (which
// validates and echoes the resolved window) and the Infrastructure service (which groups by it) need
// exactly the same rules — duplicating them in two places is how a chart and its axis start
// disagreeing.
//
// Two conventions are fixed here and relied on everywhere downstream:
//
//  * A window is `[FromUtc, ToUtc)` — lower bound inclusive, upper exclusive — matching
//    AlertQueryService's existing filter convention, so an Alert can never be counted in two adjacent
//    windows or two adjacent buckets.
//  * Buckets are anchored in UTC, never in a Branch's local calendar (FS-15 §8 limitation 3):
//    Branch.TimeZoneId is null for every production Branch, and an all-Branches series cannot mix
//    calendars coherently.
public static class AnalyticsWindow
{
    // FS-15 §4.1: the default range when the caller supplies none. Not a silent fallback for an
    // *unrecognized* value — that is a 400 (see TryParseRange's contract below).
    public const AnalyticsRange DefaultRange = AnalyticsRange.Last30Days;

    private const string Last24HoursWire = "last24h";
    private const string Last7DaysWire = "last7d";
    private const string Last30DaysWire = "last30d";
    private const string Last90DaysWire = "last90d";

    // The accepted wire values, exposed so the API layer's validation message can name them without
    // hard-coding a second copy of the list.
    public static IReadOnlyList<string> RangeWireValues { get; } =
        [Last24HoursWire, Last7DaysWire, Last30DaysWire, Last90DaysWire];

    // Parses the wire form of a range preset. Returns false for anything unrecognized — the caller
    // maps that to a 400 rather than quietly substituting the default, so a typo in a bookmarked URL
    // can never make the dashboard silently show a different period than the one it names.
    public static bool TryParseRange(string? value, out AnalyticsRange range)
    {
        switch (value?.Trim().ToLowerInvariant())
        {
            case Last24HoursWire:
                range = AnalyticsRange.Last24Hours;
                return true;
            case Last7DaysWire:
                range = AnalyticsRange.Last7Days;
                return true;
            case Last30DaysWire:
                range = AnalyticsRange.Last30Days;
                return true;
            case Last90DaysWire:
                range = AnalyticsRange.Last90Days;
                return true;
            default:
                range = DefaultRange;
                return false;
        }
    }

    public static string ToWireValue(AnalyticsRange range) => range switch
    {
        AnalyticsRange.Last24Hours => Last24HoursWire,
        AnalyticsRange.Last7Days => Last7DaysWire,
        AnalyticsRange.Last90Days => Last90DaysWire,
        _ => Last30DaysWire,
    };

    // Resolves a preset against the caller-supplied clock. The upper bound is `nowUtc` itself (not the
    // end of the current hour/day), so the newest Alert is always included the moment it is persisted.
    public static (DateTime FromUtc, DateTime ToUtc, AnalyticsBucket Bucket) Resolve(
        AnalyticsRange range, DateTime nowUtc)
    {
        var toUtc = DateTime.SpecifyKind(nowUtc, DateTimeKind.Utc);

        return range switch
        {
            AnalyticsRange.Last24Hours => (toUtc.AddHours(-24), toUtc, AnalyticsBucket.Hour),
            AnalyticsRange.Last7Days => (toUtc.AddDays(-7), toUtc, AnalyticsBucket.Day),
            AnalyticsRange.Last90Days => (toUtc.AddDays(-90), toUtc, AnalyticsBucket.Week),
            _ => (toUtc.AddDays(-30), toUtc, AnalyticsBucket.Day),
        };
    }

    // The bucket granularity for an explicit absolute window (FS-15 §4.1 final paragraph). The
    // thresholds mirror the presets: a window no wider than two days is hourly, one no wider than a
    // month is daily, anything larger is weekly — so an absolute range can never produce thousands of
    // buckets.
    public static AnalyticsBucket BucketFor(DateTime fromUtc, DateTime toUtc)
    {
        var span = toUtc - fromUtc;

        if (span <= TimeSpan.FromDays(2))
        {
            return AnalyticsBucket.Hour;
        }

        return span <= TimeSpan.FromDays(31) ? AnalyticsBucket.Day : AnalyticsBucket.Week;
    }

    // Truncates an instant down to the start of the bucket that contains it. Week buckets are ISO
    // (Monday-anchored) rather than culture-dependent, so the same data buckets identically regardless
    // of the server's locale.
    public static DateTime TruncateToBucketStart(DateTime utc, AnalyticsBucket bucket)
    {
        var value = DateTime.SpecifyKind(utc, DateTimeKind.Utc);

        switch (bucket)
        {
            case AnalyticsBucket.Hour:
                return new DateTime(value.Year, value.Month, value.Day, value.Hour, 0, 0, DateTimeKind.Utc);

            case AnalyticsBucket.Week:
                var dayStart = value.Date;
                // DayOfWeek.Sunday is 0, so Monday-anchoring needs Sunday mapped to 6 rather than 0.
                var daysSinceMonday = ((int)dayStart.DayOfWeek + 6) % 7;
                return DateTime.SpecifyKind(dayStart.AddDays(-daysSinceMonday), DateTimeKind.Utc);

            default:
                return DateTime.SpecifyKind(value.Date, DateTimeKind.Utc);
        }
    }

    public static DateTime AdvanceBucket(DateTime bucketStartUtc, AnalyticsBucket bucket) => bucket switch
    {
        AnalyticsBucket.Hour => bucketStartUtc.AddHours(1),
        AnalyticsBucket.Week => bucketStartUtc.AddDays(7),
        _ => bucketStartUtc.AddDays(1),
    };

    // Every bucket start covering `[fromUtc, toUtc)`, in ascending order, including buckets that will
    // turn out to hold nothing (FS-15 §5.1: zero-count buckets are emitted, not omitted, so a quiet
    // period reads as quiet rather than being compressed out of the x-axis).
    //
    // The first bucket start may precede fromUtc (e.g. an hourly window beginning at 14:37 starts its
    // first bucket at 14:00). That bucket still counts only Alerts inside the window, so no Alert
    // outside `[fromUtc, toUtc)` is ever included.
    public static IReadOnlyList<DateTime> EnumerateBuckets(
        DateTime fromUtc, DateTime toUtc, AnalyticsBucket bucket)
    {
        var buckets = new List<DateTime>();

        if (toUtc <= fromUtc)
        {
            return buckets;
        }

        for (var start = TruncateToBucketStart(fromUtc, bucket);
             start < toUtc;
             start = AdvanceBucket(start, bucket))
        {
            buckets.Add(start);
        }

        return buckets;
    }
}

// The date-range presets offered by the UI (FS-15 §4.1). An absolute fromUtc/toUtc pair is also
// accepted by the API, which is why this enum carries no "Custom" member — a custom window is
// expressed by the absolute parameters, not by a preset that would then need a second source of
// truth for its bounds.
public enum AnalyticsRange
{
    Last24Hours,
    Last7Days,
    Last30Days,
    Last90Days,
}

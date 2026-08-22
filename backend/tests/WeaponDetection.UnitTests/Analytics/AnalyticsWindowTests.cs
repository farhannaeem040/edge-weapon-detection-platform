using System;
using System.Linq;
using WeaponDetection.Application.Analytics;
using WeaponDetection.Application.Interfaces;
using Xunit;

namespace WeaponDetection.UnitTests.Analytics;

// FS-15 §4.1, IP-17 T-2/T-8. The range-preset → window → bucket rules, tested directly because they
// are pure: no host, no clock, no database. These boundaries are what keep every card on the page
// agreeing about which period it is showing.
public class AnalyticsWindowTests
{
    private static readonly DateTime Now = new(2026, 8, 17, 14, 37, 21, DateTimeKind.Utc);

    [Theory]
    [InlineData("last24h", AnalyticsRange.Last24Hours)]
    [InlineData("last7d", AnalyticsRange.Last7Days)]
    [InlineData("last30d", AnalyticsRange.Last30Days)]
    [InlineData("last90d", AnalyticsRange.Last90Days)]
    [InlineData("LAST30D", AnalyticsRange.Last30Days)]
    [InlineData("  last7d  ", AnalyticsRange.Last7Days)]
    public void TryParseRange_AcceptsEveryDocumentedWireValue(string wire, AnalyticsRange expected)
    {
        Assert.True(AnalyticsWindow.TryParseRange(wire, out var range));
        Assert.Equal(expected, range);
    }

    [Theory]
    [InlineData("last31d")]
    [InlineData("yesterday")]
    [InlineData("")]
    [InlineData(null)]
    public void TryParseRange_RejectsAnythingUnrecognized(string? wire)
    {
        // Deliberately false rather than a silent fallback: a typo in a bookmarked URL must surface as
        // a 400, never as a dashboard quietly showing a different period than the one it names.
        Assert.False(AnalyticsWindow.TryParseRange(wire, out _));
    }

    [Fact]
    public void ToWireValue_RoundTripsEveryRange()
    {
        foreach (var range in Enum.GetValues<AnalyticsRange>())
        {
            Assert.True(AnalyticsWindow.TryParseRange(AnalyticsWindow.ToWireValue(range), out var parsed));
            Assert.Equal(range, parsed);
        }
    }

    [Fact]
    public void DefaultRange_IsLast30Days()
    {
        Assert.Equal(AnalyticsRange.Last30Days, AnalyticsWindow.DefaultRange);
    }

    [Theory]
    [InlineData(AnalyticsRange.Last24Hours, -24, 0, AnalyticsBucket.Hour)]
    [InlineData(AnalyticsRange.Last7Days, 0, -7, AnalyticsBucket.Day)]
    [InlineData(AnalyticsRange.Last30Days, 0, -30, AnalyticsBucket.Day)]
    [InlineData(AnalyticsRange.Last90Days, 0, -90, AnalyticsBucket.Week)]
    public void Resolve_ProducesTheDocumentedWindowAndBucket(
        AnalyticsRange range, int hourOffset, int dayOffset, AnalyticsBucket expectedBucket)
    {
        var (fromUtc, toUtc, bucket) = AnalyticsWindow.Resolve(range, Now);

        Assert.Equal(Now, toUtc);
        Assert.Equal(Now.AddHours(hourOffset).AddDays(dayOffset), fromUtc);
        Assert.Equal(expectedBucket, bucket);
        Assert.Equal(DateTimeKind.Utc, fromUtc.Kind);
        Assert.Equal(DateTimeKind.Utc, toUtc.Kind);
    }

    [Fact]
    public void Resolve_UpperBoundIsNowSoTheNewestAlertIsAlwaysIncluded()
    {
        // Not the end of the current hour/day: an Alert persisted a second ago must appear immediately.
        var (_, toUtc, _) = AnalyticsWindow.Resolve(AnalyticsRange.Last30Days, Now);
        Assert.Equal(Now, toUtc);
    }

    [Fact]
    public void TruncateToBucketStart_Hour_DropsMinutesAndSeconds()
    {
        Assert.Equal(
            new DateTime(2026, 8, 17, 14, 0, 0, DateTimeKind.Utc),
            AnalyticsWindow.TruncateToBucketStart(Now, AnalyticsBucket.Hour));
    }

    [Fact]
    public void TruncateToBucketStart_Day_IsTheUtcCalendarDay()
    {
        Assert.Equal(
            new DateTime(2026, 8, 17, 0, 0, 0, DateTimeKind.Utc),
            AnalyticsWindow.TruncateToBucketStart(Now, AnalyticsBucket.Day));
    }

    [Theory]
    // 2026-08-17 is a Monday; every day of that week must collapse onto it.
    [InlineData(17, 17)]
    [InlineData(18, 17)]
    [InlineData(22, 17)]
    // 2026-08-23 is the Sunday of that same ISO week — the case a naive
    // `AddDays(-(int)DayOfWeek)` gets wrong, since DayOfWeek.Sunday is 0.
    [InlineData(23, 17)]
    [InlineData(24, 24)]
    public void TruncateToBucketStart_Week_IsMondayAnchored(int day, int expectedMonday)
    {
        var instant = new DateTime(2026, 8, day, 9, 30, 0, DateTimeKind.Utc);

        Assert.Equal(
            new DateTime(2026, 8, expectedMonday, 0, 0, 0, DateTimeKind.Utc),
            AnalyticsWindow.TruncateToBucketStart(instant, AnalyticsBucket.Week));
    }

    [Fact]
    public void EnumerateBuckets_Hourly_CoversTheWholeWindowIncludingThePartialFirstBucket()
    {
        var (fromUtc, toUtc, bucket) = AnalyticsWindow.Resolve(AnalyticsRange.Last24Hours, Now);

        var buckets = AnalyticsWindow.EnumerateBuckets(fromUtc, toUtc, bucket);

        // The window starts at 14:37 on the previous day, so its first bucket is 14:00 — 25 hourly
        // buckets in total, the first and last both partial.
        Assert.Equal(25, buckets.Count);
        Assert.Equal(new DateTime(2026, 8, 16, 14, 0, 0, DateTimeKind.Utc), buckets[0]);
        Assert.Equal(new DateTime(2026, 8, 17, 14, 0, 0, DateTimeKind.Utc), buckets[^1]);
        Assert.All(buckets, b => Assert.True(b < toUtc));
    }

    [Fact]
    public void EnumerateBuckets_Daily_IsContiguousWithNoGapsOrDuplicates()
    {
        var (fromUtc, toUtc, bucket) = AnalyticsWindow.Resolve(AnalyticsRange.Last30Days, Now);

        var buckets = AnalyticsWindow.EnumerateBuckets(fromUtc, toUtc, bucket);

        Assert.Equal(31, buckets.Count);
        Assert.Equal(buckets.Count, buckets.Distinct().Count());
        for (var i = 1; i < buckets.Count; i++)
        {
            Assert.Equal(buckets[i - 1].AddDays(1), buckets[i]);
        }
    }

    [Fact]
    public void EnumerateBuckets_Weekly_AdvancesSevenDaysAtATime()
    {
        var (fromUtc, toUtc, bucket) = AnalyticsWindow.Resolve(AnalyticsRange.Last90Days, Now);

        var buckets = AnalyticsWindow.EnumerateBuckets(fromUtc, toUtc, bucket);

        Assert.NotEmpty(buckets);
        Assert.All(buckets, b => Assert.Equal(DayOfWeek.Monday, b.DayOfWeek));
        for (var i = 1; i < buckets.Count; i++)
        {
            Assert.Equal(buckets[i - 1].AddDays(7), buckets[i]);
        }
    }

    [Fact]
    public void EnumerateBuckets_ReturnsEmptyForAnInvertedOrEmptyWindow()
    {
        Assert.Empty(AnalyticsWindow.EnumerateBuckets(Now, Now, AnalyticsBucket.Day));
        Assert.Empty(AnalyticsWindow.EnumerateBuckets(Now, Now.AddDays(-1), AnalyticsBucket.Day));
    }

    [Theory]
    [InlineData(1, AnalyticsBucket.Hour)]
    [InlineData(2, AnalyticsBucket.Hour)]
    [InlineData(3, AnalyticsBucket.Day)]
    [InlineData(31, AnalyticsBucket.Day)]
    [InlineData(32, AnalyticsBucket.Week)]
    [InlineData(365, AnalyticsBucket.Week)]
    public void BucketFor_KeepsAnAbsoluteWindowFromProducingThousandsOfBuckets(
        int spanDays, AnalyticsBucket expected)
    {
        Assert.Equal(expected, AnalyticsWindow.BucketFor(Now.AddDays(-spanDays), Now));
    }

    [Fact]
    public void AdvanceBucket_MatchesEachBucketSize()
    {
        Assert.Equal(Now.AddHours(1), AnalyticsWindow.AdvanceBucket(Now, AnalyticsBucket.Hour));
        Assert.Equal(Now.AddDays(1), AnalyticsWindow.AdvanceBucket(Now, AnalyticsBucket.Day));
        Assert.Equal(Now.AddDays(7), AnalyticsWindow.AdvanceBucket(Now, AnalyticsBucket.Week));
    }
}

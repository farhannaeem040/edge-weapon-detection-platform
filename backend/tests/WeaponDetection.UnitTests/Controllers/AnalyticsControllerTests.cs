using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.AspNetCore.Mvc;
using WeaponDetection.Api.Contracts;
using WeaponDetection.Api.Controllers;
using WeaponDetection.Application.Analytics;
using WeaponDetection.Application.Interfaces;
using Xunit;

namespace WeaponDetection.UnitTests.Controllers;

// FS-15 §4/§6, IP-17 T-5/T-8. Controller-layer tests against a stub IOperationalAnalyticsService: the
// parameter validation, the range→window resolution handed to the service, and the HTTP shape of each
// outcome. The aggregations themselves are covered by AnalyticsApiTests against real SQL Server.
public class AnalyticsControllerTests
{
    private static readonly DateTime Now = new(2026, 8, 17, 14, 0, 0, DateTimeKind.Utc);
    private static readonly Guid BranchId = Guid.NewGuid();

    private sealed class StubAnalyticsService : IOperationalAnalyticsService
    {
        public AnalyticsQuery? LastQuery { get; private set; }
        public OperationalAnalyticsView? View { get; init; } = MakeView();
        public IReadOnlyList<AnalyticsExportRow>? ExportRows { get; init; } = [];

        public Task<OperationalAnalyticsView?> GetOperationalAsync(
            AnalyticsQuery query, CancellationToken cancellationToken = default)
        {
            LastQuery = query;
            return Task.FromResult(View);
        }

        public Task<IReadOnlyList<AnalyticsExportRow>?> GetExportRowsAsync(
            AnalyticsQuery query, CancellationToken cancellationToken = default)
        {
            LastQuery = query;
            return Task.FromResult(ExportRows);
        }
    }

    private static OperationalAnalyticsView MakeView(
        Guid? branchId = null, string? branchName = null, string? detectionType = null) =>
        new(
            new AnalyticsFiltersView(
                Now.AddDays(-30), Now, AnalyticsBucket.Day, branchId, branchName, detectionType),
            new AnalyticsSummaryView(
                TotalDetections: 594,
                SuppressedDetections: 2154,
                MeanConfidence: 0.83,
                ConfidenceHigh: 500,
                ConfidenceMedium: 80,
                ConfidenceLow: 14,
                AverageDeliveryLatencyMs: 2489.8,
                MedianDeliveryLatencyMs: 2440,
                MaxDeliveryLatencyMs: 9389,
                LatencySampleCount: 593,
                LatencySamplesExcluded: 1,
                AlertsWithSnapshot: 568,
                BranchCount: 1,
                CameraCount: 2,
                DeviceCount: 1,
                ValidationDataAvailable: false,
                GeneratedAtUtc: Now),
            [new TimeBucketPoint(Now.Date, 151)],
            [new BranchCountPoint(BranchId, "Ljmu Branch", 594)],
            [new LatencyBucketPoint(Now.Date, 2489.8, 593)]);

    private static AnalyticsController MakeController(StubAnalyticsService service) =>
        new(service, new FixedTimeProvider(Now));

    private sealed class FixedTimeProvider(DateTime utcNow) : TimeProvider
    {
        public override DateTimeOffset GetUtcNow() => new(utcNow, TimeSpan.Zero);
    }

    // ---------------------------------------------------------------- Range resolution

    [Fact]
    public async Task Operational_WithNoRange_DefaultsToLast30Days()
    {
        var service = new StubAnalyticsService();

        await MakeController(service).Operational(null, null, null, null, null, CancellationToken.None);

        Assert.Equal(Now.AddDays(-30), service.LastQuery!.FromUtc);
        Assert.Equal(Now, service.LastQuery.ToUtc);
        Assert.Equal(AnalyticsBucket.Day, service.LastQuery.Bucket);
    }

    [Theory]
    [InlineData("last24h", AnalyticsBucket.Hour)]
    [InlineData("last7d", AnalyticsBucket.Day)]
    [InlineData("last30d", AnalyticsBucket.Day)]
    [InlineData("last90d", AnalyticsBucket.Week)]
    public async Task Operational_ResolvesEachPresetToItsDocumentedBucket(string range, AnalyticsBucket expected)
    {
        var service = new StubAnalyticsService();

        await MakeController(service).Operational(range, null, null, null, null, CancellationToken.None);

        Assert.Equal(expected, service.LastQuery!.Bucket);
    }

    [Fact]
    public async Task Operational_EchoesTheResolvedRangeBackToTheClient()
    {
        var service = new StubAnalyticsService();

        var result = await MakeController(service)
            .Operational("last7d", null, null, null, null, CancellationToken.None);

        var dto = Assert.IsType<OperationalAnalyticsDto>(Assert.IsType<OkObjectResult>(result).Value);
        Assert.Equal("last7d", dto.Filters.Range);
    }

    [Fact]
    public async Task Operational_WithAnUnrecognizedRange_Returns400RatherThanSilentlyDefaulting()
    {
        var service = new StubAnalyticsService();

        var result = await MakeController(service)
            .Operational("last31d", null, null, null, null, CancellationToken.None);

        AssertValidationError(result);
        Assert.Null(service.LastQuery);
    }

    // ---------------------------------------------------------------- Absolute windows

    [Fact]
    public async Task Operational_WithAnAbsoluteWindow_UsesItAndReportsNoPresetRange()
    {
        var service = new StubAnalyticsService();
        var from = Now.AddDays(-3);

        var result = await MakeController(service)
            .Operational(null, from, Now, null, null, CancellationToken.None);

        Assert.Equal(from, service.LastQuery!.FromUtc);
        Assert.Equal(Now, service.LastQuery.ToUtc);
        var dto = Assert.IsType<OperationalAnalyticsDto>(Assert.IsType<OkObjectResult>(result).Value);
        Assert.Null(dto.Filters.Range);
    }

    [Fact]
    public async Task Operational_WithBothRangeAndAbsoluteWindow_Returns400()
    {
        // Honouring both would leave the echoed filters ambiguous about which one actually applied.
        var result = await MakeController(new StubAnalyticsService())
            .Operational("last7d", Now.AddDays(-1), Now, null, null, CancellationToken.None);

        AssertValidationError(result);
    }

    [Fact]
    public async Task Operational_WithOnlyOneAbsoluteBound_Returns400()
    {
        var controller = MakeController(new StubAnalyticsService());

        AssertValidationError(
            await controller.Operational(null, Now.AddDays(-1), null, null, null, CancellationToken.None));
        AssertValidationError(
            await controller.Operational(null, null, Now, null, null, CancellationToken.None));
    }

    [Fact]
    public async Task Operational_WithAnInvertedWindow_Returns400()
    {
        var result = await MakeController(new StubAnalyticsService())
            .Operational(null, Now, Now.AddDays(-1), null, null, CancellationToken.None);

        AssertValidationError(result);
    }

    [Fact]
    public async Task Operational_WithAZeroLengthWindow_Returns400()
    {
        var result = await MakeController(new StubAnalyticsService())
            .Operational(null, Now, Now, null, null, CancellationToken.None);

        AssertValidationError(result);
    }

    [Fact]
    public async Task Operational_WithAnUnreasonablyLargeWindow_Returns400()
    {
        var result = await MakeController(new StubAnalyticsService())
            .Operational(
                null, Now.AddDays(-(AnalyticsQuery.MaxRangeDays + 1)), Now, null, null, CancellationToken.None);

        AssertValidationError(result);
    }

    // ---------------------------------------------------------------- Branch and detection type

    [Fact]
    public async Task Operational_PassesTheRequestedBranchIdThrough()
    {
        var service = new StubAnalyticsService { View = MakeView(BranchId, "Ljmu Branch") };

        await MakeController(service).Operational(null, null, null, BranchId, null, CancellationToken.None);

        Assert.Equal(BranchId, service.LastQuery!.BranchId);
    }

    [Fact]
    public async Task Operational_WithAnEmptyGuidBranchId_Returns400()
    {
        var result = await MakeController(new StubAnalyticsService())
            .Operational(null, null, null, Guid.Empty, null, CancellationToken.None);

        AssertValidationError(result);
    }

    [Fact]
    public async Task Operational_WhenTheBranchDoesNotResolve_Returns404()
    {
        // Deliberately distinct from an empty chart: "this Branch was deleted" must never look like
        // "this Branch was quiet".
        var service = new StubAnalyticsService { View = null };

        var result = await MakeController(service)
            .Operational(null, null, null, Guid.NewGuid(), null, CancellationToken.None);

        var notFound = Assert.IsType<NotFoundObjectResult>(result);
        Assert.Equal("NOT_FOUND", Assert.IsType<ApiResponse>(notFound.Value).ErrorCode);
    }

    [Theory]
    [InlineData("gun")]
    [InlineData("knife")]
    [InlineData("GUN")]
    public async Task Operational_AcceptsTheProjectsOwnDetectionTaxonomyCaseInsensitively(string detectionType)
    {
        var service = new StubAnalyticsService();

        var result = await MakeController(service)
            .Operational(null, null, null, null, detectionType, CancellationToken.None);

        Assert.IsType<OkObjectResult>(result);
        Assert.Equal(detectionType.ToLowerInvariant(), service.LastQuery!.DetectionType);
    }

    [Theory]
    [InlineData("intrusion")]
    [InlineData("fire")]
    [InlineData("person")]
    public async Task Operational_RejectsAClassOutsideTheProjectsTaxonomy(string detectionType)
    {
        // The Stitch mockup offers Intrusion / Object Left Behind / Fire-Smoke / Loitering; this
        // platform detects gun and knife, and must not pretend otherwise.
        var result = await MakeController(new StubAnalyticsService())
            .Operational(null, null, null, null, detectionType, CancellationToken.None);

        AssertValidationError(result);
    }

    [Fact]
    public async Task Operational_WithABlankDetectionType_TreatsItAsAllDetections()
    {
        var service = new StubAnalyticsService();

        await MakeController(service).Operational(null, null, null, null, "  ", CancellationToken.None);

        Assert.Null(service.LastQuery!.DetectionType);
    }

    // ---------------------------------------------------------------- Payload honesty

    [Fact]
    public async Task Operational_ReportsThatNoValidationGroundTruthExists()
    {
        var result = await MakeController(new StubAnalyticsService())
            .Operational(null, null, null, null, null, CancellationToken.None);

        var dto = Assert.IsType<OperationalAnalyticsDto>(Assert.IsType<OkObjectResult>(result).Value);
        Assert.False(dto.Summary.ValidationDataAvailable);
    }

    [Fact]
    public void ResponseContract_ExposesNoAccuracyOrResponseTimeField()
    {
        // FS-15 §3.4: the schema holds no ground truth and no operator-response timestamp, so no field
        // may claim either. This guards the contract itself against a well-meaning future rename.
        var properties = typeof(AnalyticsSummaryDto).GetProperties().Select(p => p.Name).ToList();

        foreach (var forbidden in new[]
                 { "Accuracy", "Precision", "Recall", "FalsePositive", "Confirmed", "ConfirmationRate", "ResponseTime" })
        {
            Assert.DoesNotContain(
                properties, name => name.Contains(forbidden, StringComparison.OrdinalIgnoreCase));
        }
    }

    [Fact]
    public async Task Operational_CarriesTheConfidenceBandThresholdsSoTheUiLegendCannotDrift()
    {
        var result = await MakeController(new StubAnalyticsService())
            .Operational(null, null, null, null, null, CancellationToken.None);

        var dto = Assert.IsType<OperationalAnalyticsDto>(Assert.IsType<OkObjectResult>(result).Value);
        Assert.Equal(AnalyticsSummaryView.HighConfidenceThreshold, dto.Summary.HighConfidenceThreshold);
        Assert.Equal(AnalyticsSummaryView.MediumConfidenceThreshold, dto.Summary.MediumConfidenceThreshold);
    }

    [Fact]
    public async Task Operational_MapsEverySeriesOntoTheWire()
    {
        var result = await MakeController(new StubAnalyticsService())
            .Operational(null, null, null, null, null, CancellationToken.None);

        var dto = Assert.IsType<OperationalAnalyticsDto>(Assert.IsType<OkObjectResult>(result).Value);
        Assert.Single(dto.DetectionsOverTime);
        Assert.Single(dto.DetectionsByBranch);
        Assert.Single(dto.LatencyOverTime);
        Assert.Equal("Ljmu Branch", dto.DetectionsByBranch[0].BranchName);
        Assert.Equal("day", dto.Filters.Bucket);
    }

    // ---------------------------------------------------------------- CSV export

    [Fact]
    public async Task Export_ReturnsCsvWithTheDocumentedFilenameAndContentType()
    {
        var service = new StubAnalyticsService
        {
            ExportRows =
            [
                new AnalyticsExportRow(Now, Now.AddSeconds(2), 2000, "Ljmu Branch", "Front Camera",
                    "gun", 0.91, "New", true),
            ],
        };

        var result = await MakeController(service)
            .ExportOperational(null, null, null, null, null, CancellationToken.None);

        var file = Assert.IsType<FileContentResult>(result);
        Assert.Equal("text/csv; charset=utf-8", file.ContentType);
        Assert.Equal("operational-analytics-2026-08-17.csv", file.FileDownloadName);
        Assert.Contains("Ljmu Branch", Encoding.UTF8.GetString(file.FileContents));
    }

    [Fact]
    public async Task Export_StartsWithAUtf8BomSoExcelReadsNonAsciiNamesCorrectly()
    {
        var result = await MakeController(new StubAnalyticsService())
            .ExportOperational(null, null, null, null, null, CancellationToken.None);

        var file = Assert.IsType<FileContentResult>(result);
        Assert.Equal(Encoding.UTF8.GetPreamble(), file.FileContents.Take(3).ToArray());
    }

    [Fact]
    public async Task Export_AppliesTheSameFilterValidationAsTheAnalyticsView()
    {
        // The two endpoints share one validation path, so an Admin can never export a data set the
        // dashboard itself would have refused to show.
        var controller = MakeController(new StubAnalyticsService());

        AssertValidationError(
            await controller.ExportOperational("nope", null, null, null, null, CancellationToken.None));
        AssertValidationError(
            await controller.ExportOperational(null, null, null, null, "intrusion", CancellationToken.None));
        AssertValidationError(
            await controller.ExportOperational(null, Now, Now.AddDays(-1), null, null, CancellationToken.None));
    }

    [Fact]
    public async Task Export_PassesTheSameFiltersToTheServiceAsTheAnalyticsView()
    {
        var service = new StubAnalyticsService { View = MakeView(BranchId, "Ljmu Branch", "knife") };

        await MakeController(service)
            .ExportOperational("last7d", null, null, BranchId, "knife", CancellationToken.None);

        Assert.Equal(BranchId, service.LastQuery!.BranchId);
        Assert.Equal("knife", service.LastQuery.DetectionType);
        Assert.Equal(Now.AddDays(-7), service.LastQuery.FromUtc);
    }

    [Fact]
    public async Task Export_WhenTheBranchDoesNotResolve_Returns404()
    {
        var service = new StubAnalyticsService { ExportRows = null };

        var result = await MakeController(service)
            .ExportOperational(null, null, null, Guid.NewGuid(), null, CancellationToken.None);

        Assert.IsType<NotFoundObjectResult>(result);
    }

    [Fact]
    public async Task Export_OverTheRowLimit_Returns400RatherThanASilentlyTruncatedFile()
    {
        var rows = Enumerable
            .Range(0, AnalyticsQuery.MaxExportRows + 1)
            .Select(_ => new AnalyticsExportRow(Now, Now, 0, "B", "C", "gun", 0.9, "New", false))
            .ToList();
        var service = new StubAnalyticsService { ExportRows = rows };

        var result = await MakeController(service)
            .ExportOperational(null, null, null, null, null, CancellationToken.None);

        AssertValidationError(result);
    }

    [Fact]
    public async Task Export_ExactlyAtTheRowLimit_Succeeds()
    {
        var rows = Enumerable
            .Range(0, AnalyticsQuery.MaxExportRows)
            .Select(_ => new AnalyticsExportRow(Now, Now, 0, "B", "C", "gun", 0.9, "New", false))
            .ToList();
        var service = new StubAnalyticsService { ExportRows = rows };

        var result = await MakeController(service)
            .ExportOperational(null, null, null, null, null, CancellationToken.None);

        Assert.IsType<FileContentResult>(result);
    }

    private static void AssertValidationError(IActionResult result)
    {
        var badRequest = Assert.IsType<BadRequestObjectResult>(result);
        var envelope = Assert.IsType<ApiResponse>(badRequest.Value);
        Assert.False(envelope.Success);
        Assert.Equal("VALIDATION_ERROR", envelope.ErrorCode);
    }
}

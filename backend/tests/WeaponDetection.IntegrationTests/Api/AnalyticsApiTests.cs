using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using WeaponDetection.Domain;
using Xunit;

namespace WeaponDetection.IntegrationTests.Api;

// Full HTTP-pipeline, real-SQL-Server integration tests for GET /api/v1/analytics/operational and its
// CSV export (FS-15, IP-17 T-9).
//
// Alerts are seeded directly through the DbContext rather than through POST /api/v1/sync/events, for
// two reasons: DetectedAtUtc must be placed precisely into chosen buckets (the sync endpoint stamps
// ReceivedAtUtc itself), and the Branch daily Alert quota (FS-09, 15/Branch/day) would otherwise cap
// a fixture at 15 rows and start exercising suppression instead of analytics. The seeded rows use the
// same Alert constructor the sync path uses, so no invariant is bypassed.
[Collection(ApiHostCollection.Name)]
public class AnalyticsApiTests : IDisposable
{
    private const string DeviceIdHeader = "X-Device-Id";
    private const string DeviceSecretHeader = "X-Device-Secret";
    private const string OperationalUrl = "/api/v1/analytics/operational";
    private const string ExportUrl = "/api/v1/analytics/operational/export";

    private readonly AnalyticsApiFactory _factory;
    private readonly HttpClient _client;

    public AnalyticsApiTests()
    {
        _factory = new AnalyticsApiFactory();
        _client = _factory.CreateClient();
    }

    public void Dispose()
    {
        _client.Dispose();
        _factory.Dispose();
    }

    private sealed record ApiEnvelope(bool Success, string? Message, JsonElement? Data, string? ErrorCode);

    // ---------------------------------------------------------------- Fixture helpers

    private async Task<string> LoginAsync()
    {
        var response = await _client.PostAsJsonAsync("/api/v1/auth/login", new
        {
            credentialIdentifier = SqlServerApiHostFactory.AdminIdentifier,
            password = SqlServerApiHostFactory.AdminPassword,
        });
        response.EnsureSuccessStatusCode();
        var envelope = await response.Content.ReadFromJsonAsync<ApiEnvelope>();
        return envelope!.Data!.Value.GetProperty("token").GetString()!;
    }

    private sealed record SeededBranch(Guid BranchId, string Name, Guid CameraId, string CameraName);

    private async Task<SeededBranch> SeedBranchAsync(string name, string cameraName = "Front Camera")
    {
        using var db = _factory.CreateDbContext();

        var branch = new Branch(name, "1 High Street", "ops@example.local");
        db.Branches.Add(branch);

        var camera = new Camera(
            branch.BranchId, cameraName, "rtsp://camera.example.local:554/stream1",
            $"cam-{Guid.NewGuid():N}"[..20]);
        db.Cameras.Add(camera);

        // A Branch always owns a Device in the real creation path (FS-02), and BranchResponseDto
        // projects it — so the fixture creates one too rather than leaving a shape production never
        // produces.
        db.Devices.Add(new Device(branch.BranchId));

        await db.SaveChangesAsync();
        return new SeededBranch(branch.BranchId, branch.Name, camera.CameraId, camera.Name);
    }

    private async Task SeedAlertsAsync(
        Guid cameraId,
        IEnumerable<(DateTime DetectedAtUtc, double LatencySeconds, string ClassName, double Confidence)> rows)
    {
        using var db = _factory.CreateDbContext();
        var deviceId = Guid.NewGuid();

        foreach (var row in rows)
        {
            db.Alerts.Add(new Alert(
                deviceId,
                Guid.NewGuid(),
                cameraId,
                row.DetectedAtUtc,
                row.DetectedAtUtc.AddSeconds(row.LatencySeconds),
                row.ClassName == "gun" ? 0 : 1,
                row.ClassName,
                row.Confidence,
                frameNumber: 1,
                frameWidth: 1280,
                frameHeight: 720,
                bboxLeft: 0,
                bboxTop: 0,
                bboxWidth: 10,
                bboxHeight: 10));
        }

        await db.SaveChangesAsync();
    }

    private async Task<HttpResponseMessage> GetAsync(string token, string url) =>
        await _client.SendAsync(new HttpRequestMessage(HttpMethod.Get, url)
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
        });

    private static async Task<JsonElement> DataAsync(HttpResponseMessage response)
    {
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var envelope = await response.Content.ReadFromJsonAsync<ApiEnvelope>();
        Assert.True(envelope!.Success);
        return envelope.Data!.Value;
    }

    // The API envelope omits null members (ARCH-001 §14.3 / ADR-009, configured once in Program.cs),
    // so an unavailable metric is either an absent property or an explicit null — and, critically,
    // never a zero standing in for "no data" (FS-15 §5.5).
    private static void AssertUnavailable(JsonElement parent, string property)
    {
        if (parent.TryGetProperty(property, out var value))
        {
            Assert.Equal(JsonValueKind.Null, value.ValueKind);
        }
    }

    private static bool IsUnavailable(JsonElement parent, string property) =>
        !parent.TryGetProperty(property, out var value) || value.ValueKind == JsonValueKind.Null;

    private static string Query(
        string? range = null, Guid? branchId = null, string? detectionType = null,
        DateTime? fromUtc = null, DateTime? toUtc = null)
    {
        var parts = new List<string>();
        if (range is not null) parts.Add("range=" + range);
        if (branchId is not null) parts.Add("branchId=" + branchId);
        if (detectionType is not null) parts.Add("detectionType=" + detectionType);
        if (fromUtc is not null) parts.Add("fromUtc=" + Uri.EscapeDataString(fromUtc.Value.ToString("O", CultureInfo.InvariantCulture)));
        if (toUtc is not null) parts.Add("toUtc=" + Uri.EscapeDataString(toUtc.Value.ToString("O", CultureInfo.InvariantCulture)));
        return parts.Count == 0 ? string.Empty : "?" + string.Join('&', parts);
    }

    // ---------------------------------------------------------------- Authorization

    [Fact]
    public async Task Operational_WithoutAuthentication_Returns401()
    {
        using var response = await _client.GetAsync(OperationalUrl);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task Export_WithoutAuthentication_Returns401()
    {
        using var response = await _client.GetAsync(ExportUrl);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task Operational_WithDeviceCredentials_Returns401()
    {
        // Device credentials authenticate the Jetson for sync/snapshot upload; they must never open an
        // Admin analytics view.
        var request = new HttpRequestMessage(HttpMethod.Get, OperationalUrl);
        request.Headers.Add(DeviceIdHeader, Guid.NewGuid().ToString());
        request.Headers.Add(DeviceSecretHeader, "not-a-real-secret");

        using var response = await _client.SendAsync(request);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task Operational_WithAnInvalidToken_Returns401()
    {
        using var response = await _client.SendAsync(new HttpRequestMessage(HttpMethod.Get, OperationalUrl)
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", "not.a.token") },
        });

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    // ---------------------------------------------------------------- Defaults and validation

    [Fact]
    public async Task Operational_DefaultsToLast30DaysOfDailyBucketsAcrossAllBranches()
    {
        var token = await LoginAsync();

        var data = await DataAsync(await GetAsync(token, OperationalUrl));

        var filters = data.GetProperty("filters");
        Assert.Equal("last30d", filters.GetProperty("range").GetString());
        Assert.Equal("day", filters.GetProperty("bucket").GetString());
        AssertUnavailable(filters, "branchId");
        AssertUnavailable(filters, "detectionType");

        var from = filters.GetProperty("fromUtc").GetDateTime();
        var to = filters.GetProperty("toUtc").GetDateTime();
        Assert.Equal(30, Math.Round((to - from).TotalDays));
        Assert.Equal(31, data.GetProperty("detectionsOverTime").GetArrayLength());
    }

    [Theory]
    [InlineData("last24h", "hour")]
    [InlineData("last7d", "day")]
    [InlineData("last30d", "day")]
    [InlineData("last90d", "week")]
    public async Task Operational_ResolvesEachRangePresetToItsDocumentedBucket(string range, string bucket)
    {
        var token = await LoginAsync();

        var data = await DataAsync(await GetAsync(token, OperationalUrl + Query(range: range)));

        Assert.Equal(bucket, data.GetProperty("filters").GetProperty("bucket").GetString());
    }

    [Fact]
    public async Task Operational_WithAnUnrecognizedRange_Returns400()
    {
        var token = await LoginAsync();

        using var response = await GetAsync(token, OperationalUrl + Query(range: "last31d"));

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        var envelope = await response.Content.ReadFromJsonAsync<ApiEnvelope>();
        Assert.Equal("VALIDATION_ERROR", envelope!.ErrorCode);
    }

    [Fact]
    public async Task Operational_WithACustomDateRange_UsesItAndReportsNoPreset()
    {
        var token = await LoginAsync();
        var to = DateTime.UtcNow;
        var from = to.AddDays(-5);

        var data = await DataAsync(await GetAsync(token, OperationalUrl + Query(fromUtc: from, toUtc: to)));

        var filters = data.GetProperty("filters");
        AssertUnavailable(filters, "range");
        Assert.Equal("day", filters.GetProperty("bucket").GetString());
    }

    [Fact]
    public async Task Operational_WithAnInvertedCustomRange_Returns400()
    {
        var token = await LoginAsync();
        var now = DateTime.UtcNow;

        using var response = await GetAsync(
            token, OperationalUrl + Query(fromUtc: now, toUtc: now.AddDays(-1)));

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    [Fact]
    public async Task Operational_WithAnExcessiveCustomRange_Returns400()
    {
        var token = await LoginAsync();
        var now = DateTime.UtcNow;

        using var response = await GetAsync(
            token, OperationalUrl + Query(fromUtc: now.AddDays(-400), toUtc: now));

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    [Fact]
    public async Task Operational_WithAnInvalidBranch_Returns404NotAnEmptyDashboard()
    {
        var token = await LoginAsync();

        using var response = await GetAsync(token, OperationalUrl + Query(branchId: Guid.NewGuid()));

        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
    }

    [Fact]
    public async Task Operational_WithAnInvalidDetectionType_Returns400()
    {
        var token = await LoginAsync();

        using var response = await GetAsync(token, OperationalUrl + Query(detectionType: "intrusion"));

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    // ---------------------------------------------------------------- Aggregation correctness

    [Fact]
    public async Task Operational_WithNoAlerts_ReturnsAnHonestEmptyStateRatherThanZeroedMetrics()
    {
        var token = await LoginAsync();
        var branch = await SeedBranchAsync("Empty Branch " + Guid.NewGuid());

        var data = await DataAsync(await GetAsync(token, OperationalUrl + Query(branchId: branch.BranchId)));
        var summary = data.GetProperty("summary");

        Assert.Equal(0, summary.GetProperty("totalDetections").GetInt32());
        // Unavailable, not zero: a 0 % confidence or 0 ms latency would be a claim the data does not
        // support (FS-15 §5.5).
        AssertUnavailable(summary, "meanConfidence");
        AssertUnavailable(summary, "averageDeliveryLatencyMs");
        AssertUnavailable(summary, "medianDeliveryLatencyMs");
        Assert.Equal(0, summary.GetProperty("latencySampleCount").GetInt32());
    }

    [Fact]
    public async Task Operational_CountsOnlyAlertsInsideTheWindow()
    {
        var token = await LoginAsync();
        var branch = await SeedBranchAsync("Window Branch " + Guid.NewGuid());
        var now = DateTime.UtcNow;

        await SeedAlertsAsync(branch.CameraId,
        [
            (now.AddHours(-2), 2, "gun", 0.9),
            (now.AddHours(-5), 2, "gun", 0.9),
            // Outside a 24-hour window, inside a 30-day one.
            (now.AddDays(-3), 2, "gun", 0.9),
        ]);

        var day = await DataAsync(await GetAsync(
            token, OperationalUrl + Query(range: "last24h", branchId: branch.BranchId)));
        var month = await DataAsync(await GetAsync(
            token, OperationalUrl + Query(range: "last30d", branchId: branch.BranchId)));

        Assert.Equal(2, day.GetProperty("summary").GetProperty("totalDetections").GetInt32());
        Assert.Equal(3, month.GetProperty("summary").GetProperty("totalDetections").GetInt32());
    }

    [Fact]
    public async Task Operational_GroupsDetectionsIntoTheCorrectHourlyBuckets()
    {
        var token = await LoginAsync();
        var branch = await SeedBranchAsync("Bucket Branch " + Guid.NewGuid());
        var anchor = DateTime.UtcNow.AddHours(-3);
        var hourStart = new DateTime(anchor.Year, anchor.Month, anchor.Day, anchor.Hour, 0, 0, DateTimeKind.Utc);

        await SeedAlertsAsync(branch.CameraId,
        [
            (hourStart.AddMinutes(1), 2, "gun", 0.9),
            (hourStart.AddMinutes(30), 2, "gun", 0.9),
            (hourStart.AddMinutes(59), 2, "gun", 0.9),
            (hourStart.AddMinutes(61), 2, "gun", 0.9),
        ]);

        var data = await DataAsync(await GetAsync(
            token, OperationalUrl + Query(range: "last24h", branchId: branch.BranchId)));

        var buckets = data.GetProperty("detectionsOverTime").EnumerateArray().ToList();
        var target = buckets.Single(b => b.GetProperty("periodStartUtc").GetDateTime() == hourStart);
        var next = buckets.Single(b => b.GetProperty("periodStartUtc").GetDateTime() == hourStart.AddHours(1));

        // A bucket is [start, start+1h): the 59-minute Alert belongs to the first, the 61-minute one to
        // the second. No Alert is ever counted twice.
        Assert.Equal(3, target.GetProperty("count").GetInt32());
        Assert.Equal(1, next.GetProperty("count").GetInt32());
        Assert.Equal(4, buckets.Sum(b => b.GetProperty("count").GetInt32()));
    }

    [Fact]
    public async Task Operational_EmitsZeroCountBucketsRatherThanOmittingThem()
    {
        var token = await LoginAsync();
        var branch = await SeedBranchAsync("Quiet Branch " + Guid.NewGuid());
        await SeedAlertsAsync(branch.CameraId, [(DateTime.UtcNow.AddHours(-1), 2, "gun", 0.9)]);

        var data = await DataAsync(await GetAsync(
            token, OperationalUrl + Query(range: "last30d", branchId: branch.BranchId)));

        var buckets = data.GetProperty("detectionsOverTime").EnumerateArray().ToList();
        Assert.Equal(31, buckets.Count);
        Assert.Contains(buckets, b => b.GetProperty("count").GetInt32() == 0);
        Assert.Equal(1, buckets.Sum(b => b.GetProperty("count").GetInt32()));
    }

    [Fact]
    public async Task Operational_FiltersByBranchUsingTheCameraToBranchRelationship()
    {
        var token = await LoginAsync();
        var first = await SeedBranchAsync("Branch A " + Guid.NewGuid());
        var second = await SeedBranchAsync("Branch B " + Guid.NewGuid());
        var now = DateTime.UtcNow;

        await SeedAlertsAsync(first.CameraId, [(now.AddHours(-1), 2, "gun", 0.9), (now.AddHours(-2), 2, "gun", 0.9)]);
        await SeedAlertsAsync(second.CameraId, [(now.AddHours(-1), 2, "gun", 0.9)]);

        var firstData = await DataAsync(await GetAsync(token, OperationalUrl + Query(branchId: first.BranchId)));
        var secondData = await DataAsync(await GetAsync(token, OperationalUrl + Query(branchId: second.BranchId)));

        Assert.Equal(2, firstData.GetProperty("summary").GetProperty("totalDetections").GetInt32());
        Assert.Equal(1, secondData.GetProperty("summary").GetProperty("totalDetections").GetInt32());
        Assert.Equal(first.Name, firstData.GetProperty("filters").GetProperty("branchName").GetString());
    }

    [Fact]
    public async Task Operational_GroupsAlertDensityByRealBranchNamesIncludingQuietBranchesAtZero()
    {
        var token = await LoginAsync();
        var busy = await SeedBranchAsync("Busy Branch " + Guid.NewGuid());
        var quiet = await SeedBranchAsync("Quiet Branch " + Guid.NewGuid());
        var now = DateTime.UtcNow;

        await SeedAlertsAsync(busy.CameraId,
            Enumerable.Range(1, 4).Select(i => (now.AddHours(-i), 2d, "gun", 0.9)));

        var data = await DataAsync(await GetAsync(token, OperationalUrl));

        var byBranch = data.GetProperty("detectionsByBranch").EnumerateArray().ToList();
        var busyRow = byBranch.Single(b => b.GetProperty("branchId").GetGuid() == busy.BranchId);
        var quietRow = byBranch.Single(b => b.GetProperty("branchId").GetGuid() == quiet.BranchId);

        Assert.Equal(4, busyRow.GetProperty("count").GetInt32());
        Assert.Equal(busy.Name, busyRow.GetProperty("branchName").GetString());
        // Present at zero, so "quiet" stays distinguishable from "does not exist" (FS-15 §5.3).
        Assert.Equal(0, quietRow.GetProperty("count").GetInt32());
    }

    [Fact]
    public async Task Operational_SortsAlertDensityByCountDescending()
    {
        var token = await LoginAsync();
        var small = await SeedBranchAsync("Small Branch " + Guid.NewGuid());
        var large = await SeedBranchAsync("Large Branch " + Guid.NewGuid());
        var now = DateTime.UtcNow;

        await SeedAlertsAsync(small.CameraId, [(now.AddHours(-1), 2, "gun", 0.9)]);
        await SeedAlertsAsync(large.CameraId,
            Enumerable.Range(1, 5).Select(i => (now.AddHours(-i), 2d, "gun", 0.9)));

        var data = await DataAsync(await GetAsync(token, OperationalUrl));

        var counts = data.GetProperty("detectionsByBranch").EnumerateArray()
            .Select(b => b.GetProperty("count").GetInt32()).ToList();
        Assert.Equal(counts.OrderByDescending(c => c).ToList(), counts);
    }

    [Theory]
    [InlineData("gun", 2)]
    [InlineData("knife", 1)]
    public async Task Operational_FiltersByDetectionType(string detectionType, int expected)
    {
        var token = await LoginAsync();
        var branch = await SeedBranchAsync("Class Branch " + Guid.NewGuid());
        var now = DateTime.UtcNow;

        await SeedAlertsAsync(branch.CameraId,
        [
            (now.AddHours(-1), 2, "gun", 0.9),
            (now.AddHours(-2), 2, "gun", 0.9),
            (now.AddHours(-3), 2, "knife", 0.6),
        ]);

        var data = await DataAsync(await GetAsync(
            token, OperationalUrl + Query(branchId: branch.BranchId, detectionType: detectionType)));

        Assert.Equal(expected, data.GetProperty("summary").GetProperty("totalDetections").GetInt32());
    }

    [Fact]
    public async Task Operational_ComputesDeliveryLatencyFromTheRealTimestampPair()
    {
        var token = await LoginAsync();
        var branch = await SeedBranchAsync("Latency Branch " + Guid.NewGuid());
        var now = DateTime.UtcNow;

        await SeedAlertsAsync(branch.CameraId,
        [
            (now.AddHours(-1), 1, "gun", 0.9),
            (now.AddHours(-2), 3, "gun", 0.9),
            (now.AddHours(-3), 5, "gun", 0.9),
        ]);

        var summary = (await DataAsync(await GetAsync(
            token, OperationalUrl + Query(branchId: branch.BranchId)))).GetProperty("summary");

        Assert.Equal(3, summary.GetProperty("latencySampleCount").GetInt32());
        Assert.Equal(3000, summary.GetProperty("averageDeliveryLatencyMs").GetDouble(), 1);
        Assert.Equal(3000, summary.GetProperty("medianDeliveryLatencyMs").GetDouble(), 1);
        Assert.Equal(5000, summary.GetProperty("maxDeliveryLatencyMs").GetDouble(), 1);
        Assert.Equal(0, summary.GetProperty("latencySamplesExcluded").GetInt32());
    }

    [Fact]
    public async Task Operational_ExcludesBacklogReplaysAndClockSkewFromLatencyAndReportsTheExclusions()
    {
        var token = await LoginAsync();
        var branch = await SeedBranchAsync("Skew Branch " + Guid.NewGuid());
        var now = DateTime.UtcNow;

        await SeedAlertsAsync(branch.CameraId,
        [
            (now.AddHours(-1), 2, "gun", 0.9),
            // A store-and-forward backlog replay: measures an Agent outage, not delivery performance.
            (now.AddDays(-3), 3 * 24 * 3600, "gun", 0.9),
            // Clock skew: ReceivedAtUtc before DetectedAtUtc.
            (now.AddHours(-4), -30, "gun", 0.9),
        ]);

        var summary = (await DataAsync(await GetAsync(
            token, OperationalUrl + Query(branchId: branch.BranchId)))).GetProperty("summary");

        // All three still count as detections — only the latency metric filters them.
        Assert.Equal(3, summary.GetProperty("totalDetections").GetInt32());
        Assert.Equal(1, summary.GetProperty("latencySampleCount").GetInt32());
        Assert.Equal(2, summary.GetProperty("latencySamplesExcluded").GetInt32());
        Assert.Equal(2000, summary.GetProperty("averageDeliveryLatencyMs").GetDouble(), 1);
    }

    [Fact]
    public async Task Operational_ReportsNullLatencyForABucketWithNoEligibleSample()
    {
        var token = await LoginAsync();
        var branch = await SeedBranchAsync("Gap Branch " + Guid.NewGuid());
        await SeedAlertsAsync(branch.CameraId, [(DateTime.UtcNow.AddHours(-1), 2, "gun", 0.9)]);

        var data = await DataAsync(await GetAsync(
            token, OperationalUrl + Query(range: "last30d", branchId: branch.BranchId)));

        var latency = data.GetProperty("latencyOverTime").EnumerateArray().ToList();
        // Null/absent, never 0 — a gap in the line, not a claim of instant delivery.
        Assert.Contains(latency, b => IsUnavailable(b, "averageMs"));
        Assert.DoesNotContain(latency, b =>
            b.GetProperty("sampleCount").GetInt32() == 0 && !IsUnavailable(b, "averageMs"));
    }

    [Fact]
    public async Task Operational_ComputesMeanConfidenceAndItsBandDistribution()
    {
        var token = await LoginAsync();
        var branch = await SeedBranchAsync("Confidence Branch " + Guid.NewGuid());
        var now = DateTime.UtcNow;

        await SeedAlertsAsync(branch.CameraId,
        [
            (now.AddHours(-1), 2, "gun", 0.90),
            (now.AddHours(-2), 2, "gun", 0.80),
            (now.AddHours(-3), 2, "gun", 0.60),
            (now.AddHours(-4), 2, "gun", 0.30),
        ]);

        var summary = (await DataAsync(await GetAsync(
            token, OperationalUrl + Query(branchId: branch.BranchId)))).GetProperty("summary");

        Assert.Equal(0.65, summary.GetProperty("meanConfidence").GetDouble(), 3);
        Assert.Equal(2, summary.GetProperty("confidenceHigh").GetInt32());
        Assert.Equal(1, summary.GetProperty("confidenceMedium").GetInt32());
        Assert.Equal(1, summary.GetProperty("confidenceLow").GetInt32());
    }

    [Fact]
    public async Task Operational_NeverClaimsValidationGroundTruthExists()
    {
        // FS-15 §3.4: AlertStatus has exactly one member, so no accuracy/precision/false-positive
        // figure is computable. The payload says so explicitly rather than leaving the UI to guess.
        var token = await LoginAsync();

        var data = await DataAsync(await GetAsync(token, OperationalUrl));
        var summaryJson = data.GetProperty("summary").GetRawText();

        Assert.False(data.GetProperty("summary").GetProperty("validationDataAvailable").GetBoolean());
        foreach (var forbidden in new[] { "accuracy", "precision", "falsePositive", "confirmed", "responseTime" })
        {
            Assert.DoesNotContain(forbidden, summaryJson, StringComparison.OrdinalIgnoreCase);
        }
    }

    [Fact]
    public async Task Operational_ExcludesQuotaSuppressedDetectionsFromTheDetectionCount()
    {
        var token = await LoginAsync();
        var branch = await SeedBranchAsync("Suppression Branch " + Guid.NewGuid());
        var now = DateTime.UtcNow;

        await SeedAlertsAsync(branch.CameraId, [(now.AddHours(-1), 2, "gun", 0.9)]);

        using (var db = _factory.CreateDbContext())
        {
            db.SuppressedDetectionEvents.Add(new SuppressedDetectionEvent(
                Guid.NewGuid(), Guid.NewGuid(), branch.BranchId,
                now.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture), "gun", now.AddHours(-1),
                SuppressedDetectionEvent.BranchDailyAlertQuotaReachedReason, now));
            await db.SaveChangesAsync();
        }

        var summary = (await DataAsync(await GetAsync(
            token, OperationalUrl + Query(branchId: branch.BranchId)))).GetProperty("summary");

        // Reported separately for context, never folded into the detection total (FS-15 §5.1).
        Assert.Equal(1, summary.GetProperty("totalDetections").GetInt32());
        Assert.Equal(1, summary.GetProperty("suppressedDetections").GetInt32());
    }

    [Fact]
    public async Task Operational_ReportsFleetCountsScopedToTheSelectedBranch()
    {
        var token = await LoginAsync();
        var branch = await SeedBranchAsync("Fleet Branch " + Guid.NewGuid());

        var summary = (await DataAsync(await GetAsync(
            token, OperationalUrl + Query(branchId: branch.BranchId)))).GetProperty("summary");

        Assert.Equal(1, summary.GetProperty("branchCount").GetInt32());
        Assert.Equal(1, summary.GetProperty("cameraCount").GetInt32());
    }

    // ---------------------------------------------------------------- CSV export

    [Fact]
    public async Task Export_ReturnsCsvWithTheDocumentedFilename()
    {
        var token = await LoginAsync();
        var branch = await SeedBranchAsync("Export Branch " + Guid.NewGuid());
        await SeedAlertsAsync(branch.CameraId, [(DateTime.UtcNow.AddHours(-1), 2, "gun", 0.91)]);

        using var response = await GetAsync(token, ExportUrl + Query(branchId: branch.BranchId));

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Equal("text/csv", response.Content.Headers.ContentType!.MediaType);
        Assert.StartsWith("operational-analytics-", response.Content.Headers.ContentDisposition!.FileName!.Trim('"'));
        Assert.EndsWith(".csv", response.Content.Headers.ContentDisposition.FileName!.Trim('"'));
    }

    [Fact]
    public async Task Export_RespectsTheBranchAndDetectionTypeFilters()
    {
        var token = await LoginAsync();
        var branch = await SeedBranchAsync("Export Filter Branch " + Guid.NewGuid());
        var other = await SeedBranchAsync("Export Other Branch " + Guid.NewGuid());
        var now = DateTime.UtcNow;

        await SeedAlertsAsync(branch.CameraId,
            [(now.AddHours(-1), 2, "gun", 0.9), (now.AddHours(-2), 2, "knife", 0.6)]);
        await SeedAlertsAsync(other.CameraId, [(now.AddHours(-1), 2, "gun", 0.9)]);

        var csv = await (await GetAsync(
            token, ExportUrl + Query(branchId: branch.BranchId, detectionType: "gun"))).Content.ReadAsStringAsync();

        var rows = csv.Split("\r\n", StringSplitOptions.RemoveEmptyEntries);
        Assert.Equal(2, rows.Length); // header + one gun Alert
        Assert.Contains(branch.Name, rows[1]);
        Assert.DoesNotContain(other.Name, csv);
        Assert.DoesNotContain("knife", csv);
    }

    [Fact]
    public async Task Export_RespectsTheDateRangeFilter()
    {
        var token = await LoginAsync();
        var branch = await SeedBranchAsync("Export Range Branch " + Guid.NewGuid());
        var now = DateTime.UtcNow;

        await SeedAlertsAsync(branch.CameraId,
            [(now.AddHours(-1), 2, "gun", 0.9), (now.AddDays(-10), 2, "gun", 0.9)]);

        var recent = await (await GetAsync(
            token, ExportUrl + Query(range: "last24h", branchId: branch.BranchId))).Content.ReadAsStringAsync();
        var all = await (await GetAsync(
            token, ExportUrl + Query(range: "last30d", branchId: branch.BranchId))).Content.ReadAsStringAsync();

        Assert.Equal(2, recent.Split("\r\n", StringSplitOptions.RemoveEmptyEntries).Length);
        Assert.Equal(3, all.Split("\r\n", StringSplitOptions.RemoveEmptyEntries).Length);
    }

    [Fact]
    public async Task Export_ContainsTheDocumentedHeaderAndRealValues()
    {
        var token = await LoginAsync();
        var branch = await SeedBranchAsync("Export Header Branch " + Guid.NewGuid(), "Rear Entrance");
        await SeedAlertsAsync(branch.CameraId, [(DateTime.UtcNow.AddHours(-1), 2, "gun", 0.91)]);

        var csv = await (await GetAsync(
            token, ExportUrl + Query(branchId: branch.BranchId))).Content.ReadAsStringAsync();
        var rows = csv.Split("\r\n", StringSplitOptions.RemoveEmptyEntries);

        Assert.Contains("Detected At (UTC)", rows[0]);
        Assert.Contains("Delivery Latency (ms)", rows[0]);
        Assert.Contains("Rear Entrance", rows[1]);
        Assert.Contains("2000", rows[1]);
        Assert.Contains("New", rows[1]);
    }

    [Fact]
    public async Task Export_LeaksNoRtspUrlCredentialOrInternalPath()
    {
        var token = await LoginAsync();
        var branch = await SeedBranchAsync("Export Secrets Branch " + Guid.NewGuid());
        await SeedAlertsAsync(branch.CameraId, [(DateTime.UtcNow.AddHours(-1), 2, "gun", 0.9)]);

        var csv = await (await GetAsync(
            token, ExportUrl + Query(branchId: branch.BranchId))).Content.ReadAsStringAsync();

        foreach (var forbidden in new[]
                 { "rtsp://", "camera.example.local", "cam-", "/var/lib", "sharedSecret", "activationKey", "sha256" })
        {
            Assert.DoesNotContain(forbidden, csv, StringComparison.OrdinalIgnoreCase);
        }
    }

    [Fact]
    public async Task Export_EscapesABranchNameContainingACommaSoTheRowKeepsItsColumnCount()
    {
        var token = await LoginAsync();
        var branch = await SeedBranchAsync($"Liverpool, Mount Pleasant {Guid.NewGuid()}");
        await SeedAlertsAsync(branch.CameraId, [(DateTime.UtcNow.AddHours(-1), 2, "gun", 0.9)]);

        var csv = await (await GetAsync(
            token, ExportUrl + Query(branchId: branch.BranchId))).Content.ReadAsStringAsync();

        Assert.Contains("\"Liverpool, Mount Pleasant", csv);
    }

    [Fact]
    public async Task Export_WithNoMatchingAlerts_ReturnsAHeaderOnlyFile()
    {
        var token = await LoginAsync();
        var branch = await SeedBranchAsync("Export Empty Branch " + Guid.NewGuid());

        var csv = await (await GetAsync(
            token, ExportUrl + Query(branchId: branch.BranchId))).Content.ReadAsStringAsync();

        Assert.Single(csv.Split("\r\n", StringSplitOptions.RemoveEmptyEntries));
    }

    [Fact]
    public async Task Export_WithAnInvalidBranch_Returns404()
    {
        var token = await LoginAsync();

        using var response = await GetAsync(token, ExportUrl + Query(branchId: Guid.NewGuid()));

        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
    }

    [Fact]
    public async Task Export_IsUtf8BomPrefixedForSpreadsheetCompatibility()
    {
        var token = await LoginAsync();

        var bytes = await (await GetAsync(token, ExportUrl)).Content.ReadAsByteArrayAsync();

        Assert.Equal(Encoding.UTF8.GetPreamble(), bytes.Take(3).ToArray());
    }

    // ---------------------------------------------------------------- Non-regression

    [Fact]
    public async Task Analytics_DoesNotMutateAnyAlertOrQuotaRow()
    {
        var token = await LoginAsync();
        var branch = await SeedBranchAsync("Read Only Branch " + Guid.NewGuid());
        await SeedAlertsAsync(branch.CameraId, [(DateTime.UtcNow.AddHours(-1), 2, "gun", 0.9)]);

        int alertsBefore, quotasBefore, suppressedBefore;
        using (var db = _factory.CreateDbContext())
        {
            alertsBefore = db.Alerts.Count();
            quotasBefore = db.BranchDailyAlertQuotas.Count();
            suppressedBefore = db.SuppressedDetectionEvents.Count();
        }

        await GetAsync(token, OperationalUrl);
        await GetAsync(token, ExportUrl);

        using (var db = _factory.CreateDbContext())
        {
            Assert.Equal(alertsBefore, db.Alerts.Count());
            Assert.Equal(quotasBefore, db.BranchDailyAlertQuotas.Count());
            Assert.Equal(suppressedBefore, db.SuppressedDetectionEvents.Count());
            Assert.All(db.Alerts.ToList(), a => Assert.Equal(AlertStatus.New, a.Status));
        }
    }

    [Fact]
    public async Task ExistingAlertBranchAndDashboardEndpointsStillRespond()
    {
        // Analytics is purely additive; the endpoints it reads alongside must be untouched.
        var token = await LoginAsync();
        var branch = await SeedBranchAsync("Regression Branch " + Guid.NewGuid());

        Assert.Equal(HttpStatusCode.OK, (await GetAsync(token, "/api/v1/alerts")).StatusCode);
        Assert.Equal(HttpStatusCode.OK, (await GetAsync(token, "/api/v1/branches")).StatusCode);
        Assert.Equal(
            HttpStatusCode.OK,
            (await GetAsync(token, $"/api/v1/dashboard/summary?branchId={branch.BranchId}")).StatusCode);
    }
}

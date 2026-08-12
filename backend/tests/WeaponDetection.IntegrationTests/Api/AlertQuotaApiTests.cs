using System;
using System.Collections.Generic;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.EntityFrameworkCore;
using Xunit;

namespace WeaponDetection.IntegrationTests.Api;

// Full HTTP-pipeline, real-SQL-Server integration tests for the Branch daily Alert quota (FS-09,
// IP-11 T-175/T-187/T-188/T-189), against a real in-process TestServer — the isolated end-to-end
// validation the task brief's Phase 12 asks for, run at the HTTP layer rather than a separate
// docker-compose project: a real, activated Device, device-authenticated exactly as the Jetson
// Agent's DetectionEventSyncWorker would authenticate, against the production
// SyncEventsController/AlertSyncService/SQL Server path with no test-only shortcuts.
// AlertQuota:MaximumPerBranchPerDay is the shipped default (15) — this class does not override it,
// since 15 is the exact production policy under test.
//
// Only one Device per Branch: Device.cs documents "The single Jetson device reserved for a Branch"
// (FS-02 §1.3, ARCH-001 §13.1) — the current, frozen architecture does not support activating a
// second Device against the same Branch (CON-007 explicitly excludes multiple Jetson devices per
// branch from this phase). The task brief's "two Devices share one quota" scenario is therefore
// proven at the AlertSyncService layer instead (AlertSyncServiceTests.
// SyncEventsAsync_QuotaSharedAcrossMultipleDevicesOnSameBranch, real SQL Server, two independent
// DeviceIds supplied directly — exactly what the already-authenticated controller would pass
// through) — the only two-Device scenario constructible without inventing an unapproved schema
// change to Device's one-per-Branch cardinality. This class instead proves per-Branch (not
// per-Camera) sharing at the full HTTP layer using the two Cameras a single real Branch/Device can
// have, and proves the full HTTP/SQL Server round trip for every other Phase 12 requirement.
[Collection(ApiHostCollection.Name)]
public class AlertQuotaApiTests : IDisposable
{
    private const string DeviceIdHeader = "X-Device-Id";
    private const string DeviceSecretHeader = "X-Device-Secret";
    private const int Maximum = 15;

    private readonly AlertQuotaApiFactory _factory;
    private readonly HttpClient _client;

    public AlertQuotaApiTests()
    {
        _factory = new AlertQuotaApiFactory();
        _client = _factory.CreateClient();
    }

    public void Dispose()
    {
        _client.Dispose();
        _factory.Dispose();
    }

    private sealed record ApiEnvelope(bool Success, string? Message, JsonElement? Data, string? ErrorCode);

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

    private async Task<(Guid BranchId, string ActivationKey)> CreateBranchWithTwoCamerasAsync(
        string token, string name)
    {
        var response = await _client.SendAsync(new HttpRequestMessage(HttpMethod.Post, "/api/v1/branches")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
            Content = JsonContent.Create(new
            {
                name,
                address = "1 High Street",
                contactDetails = "ops@example.local",
                jetsonHost = "100.98.226.80",
                rtspOutputPort = 8554,
                cameras = new[]
                {
                    new { name = "camera1", rtspUrl = "rtsp://camera.example.local:554/stream1", cameraKey = $"cam-{Guid.NewGuid():N}" },
                    new { name = "camera2", rtspUrl = "rtsp://camera.example.local:554/stream2", cameraKey = $"cam-{Guid.NewGuid():N}" },
                },
            }),
        });
        Assert.Equal(HttpStatusCode.Created, response.StatusCode);
        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        return (data.GetProperty("branchId").GetGuid(), data.GetProperty("activationKey").GetString()!);
    }

    private async Task<(Guid DeviceId, string Secret)> ActivateDeviceAsync(string activationKey)
    {
        using var response = await _client.PostAsJsonAsync(
            "/api/v1/activate", new { activationKey });
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        return (data.GetProperty("deviceId").GetGuid(), data.GetProperty("sharedSecret").GetString()!);
    }

    private async Task<(Guid BranchId, Guid DeviceId, string Secret)> SeedActivatedBranchAsync(
        string branchName)
    {
        var token = await LoginAsync();
        var (branchId, activationKey) = await CreateBranchWithTwoCamerasAsync(token, branchName);
        var (deviceId, secret) = await ActivateDeviceAsync(activationKey);
        return (branchId, deviceId, secret);
    }

    private async Task<HttpResponseMessage> SendEventAsync(
        Guid deviceId, string secret, Guid eventId, DateTime detectedAtUtc, string cameraId = "camera1")
    {
        var request = new HttpRequestMessage(HttpMethod.Post, "/api/v1/sync/events")
        {
            Headers = { { DeviceIdHeader, deviceId.ToString() }, { DeviceSecretHeader, secret } },
            Content = JsonContent.Create(new
            {
                events = new[]
                {
                    new
                    {
                        eventId,
                        cameraId,
                        detectedAtUtc,
                        createdAtUtc = DateTime.UtcNow,
                        classId = 0,
                        className = "gun",
                        confidence = 0.9,
                        sourceId = 0,
                        frameNumber = 1,
                        frameWidth = 1280,
                        frameHeight = 720,
                        boundingBox = new { left = 0.0, top = 0.0, width = 10.0, height = 10.0 },
                    },
                },
            }),
        };
        return await _client.SendAsync(request);
    }

    private static async Task<string> OutcomeOfAsync(HttpResponseMessage response)
    {
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        return data.GetProperty("results")[0].GetProperty("outcome").GetString()!;
    }

    // --- Isolated 20-event validation (Phase 12 items 1-8) -----------------------------------------

    [Fact]
    public async Task TwentyUniqueEvents_AcrossTwoCamerasOnOneBranch_Accepts15AndSuppresses5()
    {
        var (branchId, deviceId, secret) = await SeedActivatedBranchAsync(
            "Quota Branch " + Guid.NewGuid());
        var detectedAt = new DateTime(2026, 7, 29, 10, 0, 0, DateTimeKind.Utc);

        var outcomes = new List<string>();
        for (var i = 0; i < 20; i++)
        {
            // Alternates between the two Cameras on the same Branch/Device — proves the quota is
            // shared per-Branch, not per-Camera (FS-09 §4, task Phase 12).
            var cameraId = i % 2 == 0 ? "camera1" : "camera2";
            var response = await SendEventAsync(
                deviceId, secret, Guid.NewGuid(), detectedAt.AddSeconds(i), cameraId);
            outcomes.Add(await OutcomeOfAsync(response));
        }

        Assert.Equal(Maximum, outcomes.Count(o => o == "accepted"));
        Assert.Equal(20 - Maximum, outcomes.Count(o => o == "quota_exceeded"));

        await using var dbContext = _factory.CreateDbContext();
        var alertCount = await dbContext.Alerts.CountAsync(a => a.DeviceId == deviceId);
        Assert.Equal(Maximum, alertCount);

        var quotaRow = await dbContext.BranchDailyAlertQuotas
            .SingleAsync(q => q.BranchId == branchId);
        Assert.Equal(Maximum, quotaRow.AcceptedAlertCount);
        Assert.Equal(20 - Maximum, quotaRow.SuppressedDetectionCount);
    }

    // --- Concurrent boundary (Phase 12: "Alerts 14-17") ---------------------------------------------

    [Fact]
    public async Task ConcurrentSubmissionsAroundTheBoundary_NeverExceedTheMaximum()
    {
        var (branchId, deviceId, secret) = await SeedActivatedBranchAsync(
            "Quota Boundary Branch " + Guid.NewGuid());
        var detectedAt = new DateTime(2026, 7, 29, 11, 0, 0, DateTimeKind.Utc);

        // Fill 13 of the 15 slots sequentially, leaving the boundary (Alerts 14-17) for the
        // concurrent phase below.
        for (var i = 0; i < 13; i++)
        {
            var response = await SendEventAsync(
                deviceId, secret, Guid.NewGuid(), detectedAt.AddSeconds(i));
            Assert.Equal("accepted", await OutcomeOfAsync(response));
        }

        // Four concurrent requests race for the remaining 2 slots — a real HTTP fan-out against the
        // real TestServer/SQL Server, exercising the same connection-pool/atomic-UPDATE concurrency
        // path a production flood would.
        var tasks = Enumerable.Range(0, 4).Select(i =>
            SendEventAsync(deviceId, secret, Guid.NewGuid(), detectedAt.AddSeconds(13 + i)));
        var responses = await Task.WhenAll(tasks);
        var outcomes = await Task.WhenAll(responses.Select(OutcomeOfAsync));

        Assert.Equal(2, outcomes.Count(o => o == "accepted"));
        Assert.Equal(2, outcomes.Count(o => o == "quota_exceeded"));

        await using var dbContext = _factory.CreateDbContext();
        var quotaRow = await dbContext.BranchDailyAlertQuotas
            .SingleAsync(q => q.BranchId == branchId);
        Assert.Equal(Maximum, quotaRow.AcceptedAlertCount);
    }

    // --- Next Branch-local day resets the quota (Phase 12) ------------------------------------------

    [Fact]
    public async Task NextBranchLocalDay_ResetsTheQuota()
    {
        var (branchId, deviceId, secret) = await SeedActivatedBranchAsync(
            "Quota Reset Branch " + Guid.NewGuid());
        var day1 = new DateTime(2026, 7, 29, 12, 0, 0, DateTimeKind.Utc);

        for (var i = 0; i < Maximum; i++)
        {
            var response = await SendEventAsync(deviceId, secret, Guid.NewGuid(), day1.AddSeconds(i));
            Assert.Equal("accepted", await OutcomeOfAsync(response));
        }

        var overLimitResponse = await SendEventAsync(
            deviceId, secret, Guid.NewGuid(), day1.AddMinutes(1));
        Assert.Equal("quota_exceeded", await OutcomeOfAsync(overLimitResponse));

        // The Branch has no configured TimeZoneId (FS-09 §4 UTC fallback), so the next UTC calendar
        // day is the next quota day.
        var day2 = day1.AddDays(1);
        var day2Response = await SendEventAsync(deviceId, secret, Guid.NewGuid(), day2);
        Assert.Equal("accepted", await OutcomeOfAsync(day2Response));

        await using var dbContext = _factory.CreateDbContext();
        var quotaRows = await dbContext.BranchDailyAlertQuotas
            .Where(q => q.BranchId == branchId)
            .ToListAsync();
        Assert.Equal(2, quotaRows.Count);
        Assert.Contains(quotaRows, q => q.LocalDate == "2026-07-29" && q.AcceptedAlertCount == Maximum);
        Assert.Contains(quotaRows, q => q.LocalDate == "2026-07-30" && q.AcceptedAlertCount == 1);
    }

    // --- Quota is per-Branch, never system-wide (manual-review Finding 1) ---------------------------
    //
    // Consolidates, at the full HTTP/SQL-Server layer and at the production maximum (15), the proof
    // that AlertSyncServiceTests.SyncEventsAsync_DifferentBranches_HaveIndependentQuotas already
    // establishes at maximum=1: Branch A's (BranchId, LocalDate) row is a wholly separate primary-key
    // row from Branch B's (BranchDailyAlertQuotaConfiguration.BranchIdLocalDatePrimaryKeyName), so
    // exhausting A's 15 Alerts touches only A's row and leaves B's counter at zero.
    [Fact]
    public async Task TwoBranches_ExhaustingBranchAQuotaLeavesBranchBIndependent()
    {
        var (branchAId, deviceAId, secretA) = await SeedActivatedBranchAsync(
            "Quota Isolation Branch A " + Guid.NewGuid());
        var (branchBId, deviceBId, secretB) = await SeedActivatedBranchAsync(
            "Quota Isolation Branch B " + Guid.NewGuid());
        var detectedAt = new DateTime(2026, 7, 29, 13, 0, 0, DateTimeKind.Utc);

        // Branch A: consume all 15 (alternating its two Cameras, proving the shared-per-Branch,
        // not-per-Camera rule holds here too), then prove Alert 16 is quota_exceeded.
        for (var i = 0; i < Maximum; i++)
        {
            var cameraId = i % 2 == 0 ? "camera1" : "camera2";
            var response = await SendEventAsync(
                deviceAId, secretA, Guid.NewGuid(), detectedAt.AddSeconds(i), cameraId);
            Assert.Equal("accepted", await OutcomeOfAsync(response));
        }

        var branchASixteenth = await SendEventAsync(deviceAId, secretA, Guid.NewGuid(), detectedAt.AddMinutes(1));
        Assert.Equal("quota_exceeded", await OutcomeOfAsync(branchASixteenth));

        // Branch B, untouched until now: its first event must be accepted — exhausting A must not
        // have consumed, blocked, or otherwise been visible to B's counter.
        var branchBFirst = await SendEventAsync(deviceBId, secretB, Guid.NewGuid(), detectedAt);
        Assert.Equal("accepted", await OutcomeOfAsync(branchBFirst));

        await using var dbContext = _factory.CreateDbContext();
        var branchARow = await dbContext.BranchDailyAlertQuotas.SingleAsync(q => q.BranchId == branchAId);
        var branchBRow = await dbContext.BranchDailyAlertQuotas.SingleAsync(q => q.BranchId == branchBId);

        Assert.Equal(Maximum, branchARow.AcceptedAlertCount);
        Assert.Equal(1, branchARow.SuppressedDetectionCount);
        Assert.Equal(1, branchBRow.AcceptedAlertCount);
        Assert.Equal(0, branchBRow.SuppressedDetectionCount);

        // Never a single row keyed by something coarser than (BranchId, LocalDate) — two Branches on
        // the same LocalDate produce two distinct rows, not one shared/global counter.
        Assert.NotEqual(branchARow.BranchId, branchBRow.BranchId);
    }

    // --- Dashboard summary reflects one explicitly-requested Branch's own quota, never a cross-Branch
    //     total (manual-review Correction 3: GET /api/v1/dashboard/summary now requires branchId) ----
    //
    // Requesting each Branch's summary by its own id is deterministic — unlike the pre-Correction-3
    // contract, there is no "whichever Branch the Backend happens to pick" ambiguity to work around.
    [Fact]
    public async Task DashboardSummary_RequestedByBranchId_ReturnsExactlyThatBranchsOwnQuota()
    {
        var (branchAId, deviceAId, secretA) = await SeedActivatedBranchAsync(
            "Dashboard Scope Branch A " + Guid.NewGuid());
        var (branchBId, deviceBId, secretB) = await SeedActivatedBranchAsync(
            "Dashboard Scope Branch B " + Guid.NewGuid());
        var now = DateTime.UtcNow;

        const int branchAAccepted = 3;
        const int branchBAccepted = 5;

        for (var i = 0; i < branchAAccepted; i++)
        {
            var response = await SendEventAsync(deviceAId, secretA, Guid.NewGuid(), now.AddMinutes(-i));
            Assert.Equal("accepted", await OutcomeOfAsync(response));
        }
        for (var i = 0; i < branchBAccepted; i++)
        {
            var response = await SendEventAsync(deviceBId, secretB, Guid.NewGuid(), now.AddMinutes(-i));
            Assert.Equal("accepted", await OutcomeOfAsync(response));
        }

        var token = await LoginAsync();

        async Task<JsonElement> SummaryAsync(Guid branchId)
        {
            using var response = await _client.SendAsync(new HttpRequestMessage(
                HttpMethod.Get, $"/api/v1/dashboard/summary?branchId={branchId}")
            {
                Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
            });
            Assert.Equal(HttpStatusCode.OK, response.StatusCode);
            return (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        }

        var dataA = await SummaryAsync(branchAId);
        var dataB = await SummaryAsync(branchBId);

        Assert.Equal(branchAId, dataA.GetProperty("branch").GetProperty("id").GetGuid());
        Assert.Equal(branchAAccepted, dataA.GetProperty("alerts").GetProperty("today").GetInt32());

        Assert.Equal(branchBId, dataB.GetProperty("branch").GetProperty("id").GetGuid());
        Assert.Equal(branchBAccepted, dataB.GetProperty("alerts").GetProperty("today").GetInt32());
    }
}

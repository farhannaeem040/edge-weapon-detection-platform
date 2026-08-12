using System;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using System.Threading.Tasks;
using Xunit;

namespace WeaponDetection.IntegrationTests.Api;

// Full HTTP-pipeline, real-SQL-Server integration tests for the dashboard-summary endpoint (FS-10,
// IP-12 T-199), extended by manual-review Correction 3: branchId is now a required query parameter —
// GET /api/v1/dashboard/summary?branchId=<id>.
[Collection(ApiHostCollection.Name)]
public class DashboardApiTests : IDisposable
{
    private const string DeviceIdHeader = "X-Device-Id";
    private const string DeviceSecretHeader = "X-Device-Secret";

    private readonly DashboardApiFactory _factory;
    private readonly HttpClient _client;

    public DashboardApiTests()
    {
        _factory = new DashboardApiFactory();
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

    private async Task<(Guid BranchId, string ActivationKey)> CreateBranchAsync(string token, string name)
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
                cameras = new[] { new { name = "camera1", rtspUrl = "rtsp://camera.example.local:554/stream1", cameraKey = $"cam-{Guid.NewGuid():N}" } },
            }),
        });
        Assert.Equal(HttpStatusCode.Created, response.StatusCode);
        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        return (data.GetProperty("branchId").GetGuid(), data.GetProperty("activationKey").GetString()!);
    }

    private async Task<(Guid DeviceId, string Secret)> ActivateDeviceAsync(string activationKey)
    {
        using var response = await _client.PostAsJsonAsync("/api/v1/activate", new { activationKey });
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        return (data.GetProperty("deviceId").GetGuid(), data.GetProperty("sharedSecret").GetString()!);
    }

    private async Task SendEventAsync(Guid deviceId, string secret, DateTime detectedAtUtc)
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
                        eventId = Guid.NewGuid(),
                        cameraId = "camera1",
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
        using var response = await _client.SendAsync(request);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }

    private async Task<HttpResponseMessage> GetSummaryAsync(string token, Guid? branchId)
    {
        var url = branchId is null
            ? "/api/v1/dashboard/summary"
            : $"/api/v1/dashboard/summary?branchId={branchId}";

        return await _client.SendAsync(new HttpRequestMessage(HttpMethod.Get, url)
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
        });
    }

    [Fact]
    public async Task Summary_WithoutAuthentication_Returns401()
    {
        using var response = await _client.GetAsync("/api/v1/dashboard/summary?branchId=" + Guid.NewGuid());

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task Summary_WithDeviceCredentials_Returns401()
    {
        var token = await LoginAsync();
        var (branchId, activationKey) = await CreateBranchAsync(token, "Device Cred Dashboard " + Guid.NewGuid());
        var (deviceId, secret) = await ActivateDeviceAsync(activationKey);

        var request = new HttpRequestMessage(HttpMethod.Get, $"/api/v1/dashboard/summary?branchId={branchId}");
        request.Headers.Add(DeviceIdHeader, deviceId.ToString());
        request.Headers.Add(DeviceSecretHeader, secret);
        using var response = await _client.SendAsync(request);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task Summary_WithoutBranchId_Returns400()
    {
        var token = await LoginAsync();

        using var response = await GetSummaryAsync(token, branchId: null);

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        var data = await response.Content.ReadFromJsonAsync<ApiEnvelope>();
        Assert.Equal("VALIDATION_ERROR", data!.ErrorCode);
    }

    [Fact]
    public async Task Summary_WithBranchIdThatDoesNotExist_Returns404()
    {
        var token = await LoginAsync();

        using var response = await GetSummaryAsync(token, Guid.NewGuid());

        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
    }

    [Fact]
    public async Task Summary_ReflectsConfiguredMaximumAndAcceptedCount()
    {
        var token = await LoginAsync();
        var (branchId, activationKey) = await CreateBranchAsync(token, "Summary Branch " + Guid.NewGuid());
        var (deviceId, secret) = await ActivateDeviceAsync(activationKey);
        await SendEventAsync(deviceId, secret, DateTime.UtcNow);

        using var response = await GetSummaryAsync(token, branchId);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        Assert.Equal(15, data.GetProperty("alerts").GetProperty("configuredMaximum").GetInt32());
        Assert.Equal(1, data.GetProperty("alerts").GetProperty("today").GetInt32());
        Assert.Equal(14, data.GetProperty("alerts").GetProperty("remaining").GetInt32());
        Assert.Equal(branchId, data.GetProperty("branch").GetProperty("id").GetGuid());
    }

    [Fact]
    public async Task Summary_UsesBranchLocalDate_AndReturnsNextResetTimestamp()
    {
        var token = await LoginAsync();
        var (branchId, _) = await CreateBranchAsync(token, "Local Date Branch " + Guid.NewGuid());

        using var response = await GetSummaryAsync(token, branchId);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;

        // No TimeZoneId is set on a freshly created Branch (FS-09 §4 UTC fallback), so localDate must
        // equal today's UTC date, and the next reset must be strictly in the future.
        var expectedLocalDate = DateTime.UtcNow.ToString("yyyy-MM-dd");
        Assert.Equal(expectedLocalDate, data.GetProperty("branch").GetProperty("localDate").GetString());
        var nextReset = data.GetProperty("branch").GetProperty("nextQuotaResetAtUtc").GetDateTime();
        Assert.True(nextReset > DateTime.UtcNow);
        Assert.True(nextReset <= DateTime.UtcNow.AddDays(1).AddMinutes(1));
    }

    [Fact]
    public async Task Summary_WithNoAlertsToday_ReturnsZeroStateNotAnError()
    {
        var token = await LoginAsync();
        var (branchId, _) = await CreateBranchAsync(token, "Zero State Branch " + Guid.NewGuid());

        using var response = await GetSummaryAsync(token, branchId);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        Assert.Equal(0, data.GetProperty("alerts").GetProperty("today").GetInt32());
        Assert.Equal(0, data.GetProperty("suppressions").GetProperty("total").GetInt32());
        Assert.Equal(15, data.GetProperty("alerts").GetProperty("remaining").GetInt32());
    }

    [Fact]
    public async Task Summary_ReturnsDeviceAndCameraCounts()
    {
        var token = await LoginAsync();
        var (branchId, _) = await CreateBranchAsync(token, "Counts Branch " + Guid.NewGuid());

        using var response = await GetSummaryAsync(token, branchId);

        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        Assert.Equal(1, data.GetProperty("system").GetProperty("deviceCount").GetInt32());
        Assert.Equal(1, data.GetProperty("system").GetProperty("cameraCount").GetInt32());
    }

    [Fact]
    public async Task Summary_NeverExposesSecretsOrInternalPaths()
    {
        var token = await LoginAsync();
        var (branchId, _) = await CreateBranchAsync(token, "No Secrets Branch " + Guid.NewGuid());

        using var response = await GetSummaryAsync(token, branchId);

        var raw = await response.Content.ReadAsStringAsync();
        Assert.DoesNotContain("activationKey", raw, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("protectedSharedSecret", raw, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("/var/lib/weapon-detection", raw, StringComparison.OrdinalIgnoreCase);
    }

    // --- Multi-Branch scoping (manual-review Correction 3) ------------------------------------------

    [Fact]
    public async Task Summary_WithTwoBranches_EachReturnsOnlyItsOwnData_NeverTheOthers()
    {
        var token = await LoginAsync();
        var (branchAId, activationKeyA) = await CreateBranchAsync(token, "Dashboard A " + Guid.NewGuid());
        var (deviceAId, secretA) = await ActivateDeviceAsync(activationKeyA);
        var (branchBId, activationKeyB) = await CreateBranchAsync(token, "Dashboard B " + Guid.NewGuid());
        var (deviceBId, secretB) = await ActivateDeviceAsync(activationKeyB);

        var now = DateTime.UtcNow;
        await SendEventAsync(deviceAId, secretA, now);
        await SendEventAsync(deviceAId, secretA, now.AddMinutes(-1));
        await SendEventAsync(deviceBId, secretB, now);

        using var responseA = await GetSummaryAsync(token, branchAId);
        using var responseB = await GetSummaryAsync(token, branchBId);

        var dataA = (await responseA.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        var dataB = (await responseB.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;

        Assert.Equal(branchAId, dataA.GetProperty("branch").GetProperty("id").GetGuid());
        Assert.Equal(2, dataA.GetProperty("alerts").GetProperty("today").GetInt32());

        Assert.Equal(branchBId, dataB.GetProperty("branch").GetProperty("id").GetGuid());
        Assert.Equal(1, dataB.GetProperty("alerts").GetProperty("today").GetInt32());
    }
}

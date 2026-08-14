using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using Xunit;

namespace WeaponDetection.IntegrationTests.Api;

// The Branch daily Alert quota maximum must come from configuration, never from a hardcoded number
// (compose.yaml maps ALERT_QUOTA_MAXIMUM_PER_BRANCH_PER_DAY into AlertQuota__MaximumPerBranchPerDay).
//
// These run against a host configured with a deliberately non-default maximum, so an assertion that
// passed only because the value happened to equal the C# default of 15 would fail here.
//
// In ApiHostCollection like every other SQL-Server-backed API suite: these factories configure the
// host through process-wide environment variables, so running one concurrently with another host
// would let this class's AlertQuota override leak into it (and add SQL Server contention).
[Collection(ApiHostCollection.Name)]
public class ConfigurableAlertQuotaApiTests : IDisposable
{
    private readonly ConfigurableAlertQuotaApiFactory _factory;
    private readonly HttpClient _client;

    public ConfigurableAlertQuotaApiTests()
    {
        _factory = new ConfigurableAlertQuotaApiFactory();
        _client = _factory.CreateClient();
    }

    public void Dispose()
    {
        _client.Dispose();
        _factory.Dispose();
        GC.SuppressFinalize(this);
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

    private async Task<Guid> CreateBranchAsync(string token, string name)
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
        var envelope = await response.Content.ReadFromJsonAsync<ApiEnvelope>();
        return envelope!.Data!.Value.GetProperty("branchId").GetGuid();
    }

    // The quota figures live on the summary's "alerts" block (DashboardAlertsDto).
    private async Task<JsonElement> GetQuotaAsync(string token, Guid branchId)
    {
        using var response = await _client.SendAsync(
            new HttpRequestMessage(HttpMethod.Get, $"/api/v1/dashboard/summary?branchId={branchId}")
            {
                Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
            });
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var envelope = await response.Content.ReadFromJsonAsync<ApiEnvelope>();
        return envelope!.Data!.Value.GetProperty("alerts");
    }

    [Fact]
    public async Task DashboardConfiguredMaximum_ComesFromConfiguration_NotTheDefault()
    {
        var token = await LoginAsync();
        var branchId = await CreateBranchAsync(token, "Configurable Quota Branch " + Guid.NewGuid());

        var quota = await GetQuotaAsync(token, branchId);

        Assert.Equal(
            ConfigurableAlertQuotaApiFactory.ConfiguredMaximum,
            quota.GetProperty("configuredMaximum").GetInt32());
        // Guards the whole point of the test: a pass must not be possible on the C# default.
        Assert.NotEqual(15, quota.GetProperty("configuredMaximum").GetInt32());
    }

    [Fact]
    public async Task DashboardRemaining_IsDerivedFromTheConfiguredMaximum()
    {
        var token = await LoginAsync();
        var branchId = await CreateBranchAsync(token, "Configurable Remaining Branch " + Guid.NewGuid());

        var quota = await GetQuotaAsync(token, branchId);

        // No Alert has been accepted yet, so the full configured allowance remains.
        Assert.Equal(0, quota.GetProperty("today").GetInt32());
        Assert.Equal(
            ConfigurableAlertQuotaApiFactory.ConfiguredMaximum,
            quota.GetProperty("remaining").GetInt32());
    }
}

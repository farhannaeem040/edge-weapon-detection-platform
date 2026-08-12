using System;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using System.Threading.Tasks;
using Xunit;

namespace WeaponDetection.IntegrationTests.Api;

// Full HTTP-pipeline, real-SQL-Server integration tests for the Alert list's `branchId` filter
// (manual-review Correction 2). AlertQueryService already joins Alert -> Camera -> Branch and filters
// on `x.branch.BranchId == branchId` when supplied (FS-10 §9.2) — these tests prove that filter at the
// full HTTP layer with two real, independent Branches.
[Collection(ApiHostCollection.Name)]
public class AlertListBranchFilterApiTests : IDisposable
{
    private const string DeviceIdHeader = "X-Device-Id";
    private const string DeviceSecretHeader = "X-Device-Secret";

    private readonly AlertApiFactory _factory;
    private readonly HttpClient _client;

    public AlertListBranchFilterApiTests()
    {
        _factory = new AlertApiFactory();
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

    private async Task<(Guid BranchId, Guid DeviceId, string Secret)> SeedActivatedBranchAsync(
        string token, string branchName)
    {
        var createResponse = await _client.SendAsync(new HttpRequestMessage(HttpMethod.Post, "/api/v1/branches")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
            Content = JsonContent.Create(new
            {
                name = branchName,
                address = "1 High Street",
                contactDetails = "ops@example.local",
                jetsonHost = "100.98.226.80",
                rtspOutputPort = 8554,
                cameras = new[] { new { name = "camera1", rtspUrl = "rtsp://camera.example.local:554/stream1", cameraKey = $"cam-{Guid.NewGuid():N}" } },
            }),
        });
        Assert.Equal(HttpStatusCode.Created, createResponse.StatusCode);
        var created = (await createResponse.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        var branchId = created.GetProperty("branchId").GetGuid();
        var activationKey = created.GetProperty("activationKey").GetString()!;

        using var activateResponse = await _client.PostAsJsonAsync("/api/v1/activate", new { activationKey });
        Assert.Equal(HttpStatusCode.OK, activateResponse.StatusCode);
        var activated = (await activateResponse.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        return (branchId, activated.GetProperty("deviceId").GetGuid(), activated.GetProperty("sharedSecret").GetString()!);
    }

    private async Task SendEventAsync(Guid deviceId, string secret, string className = "gun")
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
                        detectedAtUtc = DateTime.UtcNow,
                        createdAtUtc = DateTime.UtcNow,
                        classId = className == "gun" ? 0 : 1,
                        className,
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

    private async Task<JsonElement> ListAlertsAsync(string token, string queryString)
    {
        using var response = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Get, $"/api/v1/alerts{queryString}")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
        });
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        return (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
    }

    [Fact]
    public async Task NoBranchFilter_ReturnsAlertsFromBothBranches()
    {
        var token = await LoginAsync();
        var (branchAId, deviceAId, secretA) = await SeedActivatedBranchAsync(token, "List Filter A " + Guid.NewGuid());
        var (branchBId, deviceBId, secretB) = await SeedActivatedBranchAsync(token, "List Filter B " + Guid.NewGuid());
        await SendEventAsync(deviceAId, secretA);
        await SendEventAsync(deviceBId, secretB);

        var data = await ListAlertsAsync(token, "?pageSize=100");
        var branchIds = data.GetProperty("items").EnumerateArray()
            .Select(i => i.GetProperty("branchId").GetGuid())
            .ToHashSet();

        Assert.Contains(branchAId, branchIds);
        Assert.Contains(branchBId, branchIds);
    }

    [Fact]
    public async Task BranchAFilter_ReturnsOnlyBranchAAlerts()
    {
        var token = await LoginAsync();
        var (branchAId, deviceAId, secretA) = await SeedActivatedBranchAsync(token, "List Filter A2 " + Guid.NewGuid());
        var (_, deviceBId, secretB) = await SeedActivatedBranchAsync(token, "List Filter B2 " + Guid.NewGuid());
        await SendEventAsync(deviceAId, secretA);
        await SendEventAsync(deviceAId, secretA);
        await SendEventAsync(deviceBId, secretB);

        var data = await ListAlertsAsync(token, $"?branchId={branchAId}&pageSize=100");

        var items = data.GetProperty("items").EnumerateArray().ToList();
        Assert.Equal(2, items.Count);
        Assert.All(items, item => Assert.Equal(branchAId, item.GetProperty("branchId").GetGuid()));
        Assert.Equal(2, data.GetProperty("totalCount").GetInt32());
    }

    [Fact]
    public async Task BranchBFilter_ReturnsOnlyBranchBAlerts()
    {
        var token = await LoginAsync();
        var (_, deviceAId, secretA) = await SeedActivatedBranchAsync(token, "List Filter A3 " + Guid.NewGuid());
        var (branchBId, deviceBId, secretB) = await SeedActivatedBranchAsync(token, "List Filter B3 " + Guid.NewGuid());
        await SendEventAsync(deviceAId, secretA);
        await SendEventAsync(deviceBId, secretB);
        await SendEventAsync(deviceBId, secretB);
        await SendEventAsync(deviceBId, secretB);

        var data = await ListAlertsAsync(token, $"?branchId={branchBId}&pageSize=100");

        var items = data.GetProperty("items").EnumerateArray().ToList();
        Assert.Equal(3, items.Count);
        Assert.All(items, item => Assert.Equal(branchBId, item.GetProperty("branchId").GetGuid()));
    }

    [Fact]
    public async Task BranchAAndBranchB_HaveIndependentPaginationTotals()
    {
        var token = await LoginAsync();
        var (branchAId, deviceAId, secretA) = await SeedActivatedBranchAsync(token, "List Filter A4 " + Guid.NewGuid());
        var (branchBId, deviceBId, secretB) = await SeedActivatedBranchAsync(token, "List Filter B4 " + Guid.NewGuid());
        for (var i = 0; i < 4; i++) await SendEventAsync(deviceAId, secretA);
        for (var i = 0; i < 7; i++) await SendEventAsync(deviceBId, secretB);

        var dataA = await ListAlertsAsync(token, $"?branchId={branchAId}&pageSize=1");
        var dataB = await ListAlertsAsync(token, $"?branchId={branchBId}&pageSize=1");

        Assert.Equal(4, dataA.GetProperty("totalCount").GetInt32());
        Assert.Equal(7, dataB.GetProperty("totalCount").GetInt32());
    }

    [Fact]
    public async Task BranchFilter_CombinesWithClassNameFilter()
    {
        var token = await LoginAsync();
        var (branchAId, deviceAId, secretA) = await SeedActivatedBranchAsync(token, "List Filter A5 " + Guid.NewGuid());
        var (_, deviceBId, secretB) = await SeedActivatedBranchAsync(token, "List Filter B5 " + Guid.NewGuid());
        await SendEventAsync(deviceAId, secretA, "gun");
        await SendEventAsync(deviceAId, secretA, "knife");
        await SendEventAsync(deviceBId, secretB, "gun");

        var data = await ListAlertsAsync(token, $"?branchId={branchAId}&className=gun&pageSize=100");

        var items = data.GetProperty("items").EnumerateArray().ToList();
        Assert.Single(items);
        Assert.Equal(branchAId, items[0].GetProperty("branchId").GetGuid());
        Assert.Equal("gun", items[0].GetProperty("className").GetString());
    }

    [Fact]
    public async Task ValidBranchIdWithNoAlerts_ReturnsAnEmptyPage_NotAnError()
    {
        var token = await LoginAsync();
        var (branchId, _, _) = await SeedActivatedBranchAsync(token, "Empty Branch " + Guid.NewGuid());

        var data = await ListAlertsAsync(token, $"?branchId={branchId}");

        Assert.Empty(data.GetProperty("items").EnumerateArray());
        Assert.Equal(0, data.GetProperty("totalCount").GetInt32());
    }

    [Fact]
    public async Task BranchIdForANonExistentBranch_ReturnsAnEmptyPage_NotAnError()
    {
        var token = await LoginAsync();

        // A syntactically valid Guid that matches no Branch is a safe empty result, not a fault — the
        // same treatment CameraId/Status filters already get when nothing matches (FS-10 §9.2).
        var data = await ListAlertsAsync(token, $"?branchId={Guid.NewGuid()}");

        Assert.Empty(data.GetProperty("items").EnumerateArray());
        Assert.Equal(0, data.GetProperty("totalCount").GetInt32());
    }

    [Fact]
    public async Task MalformedBranchId_IsRejectedSafely_NeverA500()
    {
        var token = await LoginAsync();

        using var response = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Get, "/api/v1/alerts?branchId=not-a-guid")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
        });

        // ASP.NET Core's model binding rejects an unparseable Guid? query value before the action
        // runs ([ApiController] auto-400s on invalid ModelState) — this proves that holds for
        // branchId specifically, the same as it already does for the pre-existing cameraId filter.
        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }
}

using System;
using System.Collections.Generic;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using System.Threading.Tasks;
using Xunit;

namespace WeaponDetection.IntegrationTests.Api;

// Full HTTP-pipeline, real-SQL-Server integration tests for the Alert list/detail endpoints (FS-10,
// IP-12 T-199): GET /api/v1/alerts, GET /api/v1/alerts/{id}. Alerts are created via the real
// POST /api/v1/sync/events path (the same one FS-06/FS-09's own tests use) rather than raw SQL, so
// every test exercises the actual production write path feeding this feature's read path.
[Collection(ApiHostCollection.Name)]
public class AlertApiTests : IDisposable
{
    private const string DeviceIdHeader = "X-Device-Id";
    private const string DeviceSecretHeader = "X-Device-Secret";

    private readonly AlertApiFactory _factory;
    private readonly HttpClient _client;

    public AlertApiTests()
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
        using var response = await _client.PostAsJsonAsync("/api/v1/activate", new { activationKey });
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        return (data.GetProperty("deviceId").GetGuid(), data.GetProperty("sharedSecret").GetString()!);
    }

    private async Task<Guid> SendEventAsync(
        Guid deviceId,
        string secret,
        DateTime detectedAtUtc,
        string cameraId = "camera1",
        string className = "gun",
        double confidence = 0.9)
    {
        var eventId = Guid.NewGuid();
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
                        classId = className == "gun" ? 0 : 1,
                        className,
                        confidence,
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
        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        var outcome = data.GetProperty("results")[0].GetProperty("outcome").GetString();
        Assert.Equal("accepted", outcome);
        return eventId;
    }

    private async Task<(Guid BranchId, Guid DeviceId, string Secret)> SeedActivatedBranchAsync(
        string branchName)
    {
        var token = await LoginAsync();
        var (branchId, activationKey) = await CreateBranchWithTwoCamerasAsync(token, branchName);
        var (deviceId, secret) = await ActivateDeviceAsync(activationKey);
        return (branchId, deviceId, secret);
    }

    // --- Authentication (Phase 15 items 1-2) -------------------------------------------------------

    [Fact]
    public async Task List_WithoutAuthentication_Returns401()
    {
        using var response = await _client.GetAsync("/api/v1/alerts");

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task List_WithDeviceCredentials_Returns401NotAdminData()
    {
        var (_, deviceId, secret) = await SeedActivatedBranchAsync("Device Cred Branch " + Guid.NewGuid());

        var request = new HttpRequestMessage(HttpMethod.Get, "/api/v1/alerts");
        request.Headers.Add(DeviceIdHeader, deviceId.ToString());
        request.Headers.Add(DeviceSecretHeader, secret);
        using var response = await _client.SendAsync(request);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task GetById_WithoutAuthentication_Returns401()
    {
        using var response = await _client.GetAsync($"/api/v1/alerts/{Guid.NewGuid()}");

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    // --- Pagination / sort / filters (Phase 15 items 7-16) ------------------------------------------

    [Fact]
    public async Task List_IsPaginated_AndDefaultsToNewestFirst()
    {
        var (_, deviceId, secret) = await SeedActivatedBranchAsync("Pagination Branch " + Guid.NewGuid());
        var baseTime = new DateTime(2026, 7, 30, 10, 0, 0, DateTimeKind.Utc);
        for (var i = 0; i < 5; i++)
        {
            await SendEventAsync(deviceId, secret, baseTime.AddMinutes(i));
        }

        var token = await LoginAsync();
        using var response = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Get, "/api/v1/alerts?pageSize=2&page=1")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
        });

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        Assert.Equal(2, data.GetProperty("items").GetArrayLength());
        Assert.Equal(5, data.GetProperty("totalCount").GetInt32());
        Assert.Equal(3, data.GetProperty("totalPages").GetInt32());

        var first = data.GetProperty("items")[0].GetProperty("detectedAtUtc").GetDateTime();
        var second = data.GetProperty("items")[1].GetProperty("detectedAtUtc").GetDateTime();
        Assert.True(first > second, "default ordering must be newest first");
    }

    [Fact]
    public async Task List_PageSizeAboveMaximum_Returns400()
    {
        var token = await LoginAsync();

        using var response = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Get, "/api/v1/alerts?pageSize=101")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
        });

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    [Fact]
    public async Task List_InvalidSortBy_Returns400()
    {
        var token = await LoginAsync();

        using var response = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Get, "/api/v1/alerts?sortBy=notAField")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
        });

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    [Fact]
    public async Task List_DateRangeFilter_ExcludesOutOfRangeAlerts()
    {
        var (_, deviceId, secret) = await SeedActivatedBranchAsync("Date Filter Branch " + Guid.NewGuid());
        await SendEventAsync(deviceId, secret, new DateTime(2026, 7, 29, 10, 0, 0, DateTimeKind.Utc));
        await SendEventAsync(deviceId, secret, new DateTime(2026, 7, 30, 10, 0, 0, DateTimeKind.Utc));

        var token = await LoginAsync();
        using var response = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Get,
            "/api/v1/alerts?fromUtc=2026-07-30T00:00:00Z&toUtc=2026-07-31T00:00:00Z")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
        });

        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        Assert.Equal(1, data.GetProperty("totalCount").GetInt32());
    }

    [Fact]
    public async Task List_ClassNameFilter_ReturnsOnlyMatchingClass()
    {
        var (_, deviceId, secret) = await SeedActivatedBranchAsync("Class Filter Branch " + Guid.NewGuid());
        var baseTime = new DateTime(2026, 7, 30, 11, 0, 0, DateTimeKind.Utc);
        await SendEventAsync(deviceId, secret, baseTime, className: "gun");
        await SendEventAsync(deviceId, secret, baseTime.AddMinutes(1), className: "knife");

        var token = await LoginAsync();
        using var response = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Get, "/api/v1/alerts?className=knife")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
        });

        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        Assert.Equal(1, data.GetProperty("totalCount").GetInt32());
        Assert.Equal("knife", data.GetProperty("items")[0].GetProperty("className").GetString());
    }

    [Fact]
    public async Task List_ClassNameUnrecognized_Returns400()
    {
        var token = await LoginAsync();

        using var response = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Get, "/api/v1/alerts?className=bazooka")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
        });

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    [Fact]
    public async Task List_BranchIdFilter_ReturnsOnlyThatBranchesAlerts()
    {
        var (branchA, deviceA, secretA) = await SeedActivatedBranchAsync("Branch A " + Guid.NewGuid());
        var (_, deviceB, secretB) = await SeedActivatedBranchAsync("Branch B " + Guid.NewGuid());
        var baseTime = new DateTime(2026, 7, 30, 12, 0, 0, DateTimeKind.Utc);
        await SendEventAsync(deviceA, secretA, baseTime);
        await SendEventAsync(deviceB, secretB, baseTime.AddMinutes(1));

        var token = await LoginAsync();
        using var response = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Get, $"/api/v1/alerts?branchId={branchA}")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
        });

        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        Assert.Equal(1, data.GetProperty("totalCount").GetInt32());
        Assert.Equal(branchA, data.GetProperty("items")[0].GetProperty("branchId").GetGuid());
    }

    [Fact]
    public async Task List_CameraIdFilter_ReturnsOnlyThatCamerasAlerts()
    {
        var (branchId, deviceId, secret) =
            await SeedActivatedBranchAsync("Camera Filter Branch " + Guid.NewGuid());
        var baseTime = new DateTime(2026, 7, 30, 13, 0, 0, DateTimeKind.Utc);
        await SendEventAsync(deviceId, secret, baseTime, cameraId: "camera1");
        await SendEventAsync(deviceId, secret, baseTime.AddMinutes(1), cameraId: "camera2");

        var token = await LoginAsync();
        using var listResponse = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Get, "/api/v1/alerts?pageSize=100")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
        });
        var listData = (await listResponse.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        var camera1Id = listData.GetProperty("items")
            .EnumerateArray()
            .First(i => i.GetProperty("cameraName").GetString() == "camera1")
            .GetProperty("cameraId")
            .GetGuid();

        using var response = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Get, $"/api/v1/alerts?cameraId={camera1Id}")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
        });

        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        Assert.Equal(1, data.GetProperty("totalCount").GetInt32());
    }

    [Fact]
    public async Task List_StatusFilter_ReturnsOnlyMatchingStatus()
    {
        var (_, deviceId, secret) = await SeedActivatedBranchAsync("Status Filter Branch " + Guid.NewGuid());
        await SendEventAsync(deviceId, secret, new DateTime(2026, 7, 30, 14, 0, 0, DateTimeKind.Utc));

        var token = await LoginAsync();
        using var response = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Get, "/api/v1/alerts?status=New")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
        });

        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        Assert.Equal(1, data.GetProperty("totalCount").GetInt32());
    }

    [Fact]
    public async Task List_StatusUnrecognized_Returns400()
    {
        var token = await LoginAsync();

        using var response = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Get, "/api/v1/alerts?status=Resolved")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
        });

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    [Fact]
    public async Task List_SnapshotAvailableFilter_MatchesNullSnapshotReference()
    {
        var (_, deviceId, secret) =
            await SeedActivatedBranchAsync("Snapshot Filter Branch " + Guid.NewGuid());
        await SendEventAsync(deviceId, secret, new DateTime(2026, 7, 30, 15, 0, 0, DateTimeKind.Utc));

        var token = await LoginAsync();
        using var availableResponse = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Get, "/api/v1/alerts?snapshotAvailable=true")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
        });
        using var unavailableResponse = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Get, "/api/v1/alerts?snapshotAvailable=false")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
        });

        var availableData = (await availableResponse.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        var unavailableData = (await unavailableResponse.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        Assert.Equal(0, availableData.GetProperty("totalCount").GetInt32());
        Assert.Equal(1, unavailableData.GetProperty("totalCount").GetInt32());
    }

    // --- Detail / 404 / no-secrets (Phase 15 items 17-18) --------------------------------------------

    [Fact]
    public async Task GetById_UnknownAlert_Returns404()
    {
        var token = await LoginAsync();

        using var response = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Get, $"/api/v1/alerts/{Guid.NewGuid()}")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
        });

        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
        var envelope = await response.Content.ReadFromJsonAsync<ApiEnvelope>();
        Assert.Equal("NOT_FOUND", envelope!.ErrorCode);
    }

    [Fact]
    public async Task GetById_KnownAlert_ReturnsDetailWithoutSecretsOrInternalPaths()
    {
        var (_, deviceId, secret) = await SeedActivatedBranchAsync("Detail Branch " + Guid.NewGuid());
        await SendEventAsync(deviceId, secret, new DateTime(2026, 7, 30, 16, 0, 0, DateTimeKind.Utc));

        var token = await LoginAsync();
        using var listResponse = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Get, "/api/v1/alerts")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
        });
        var listData = (await listResponse.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        var alertId = listData.GetProperty("items")[0].GetProperty("alertId").GetGuid();

        using var response = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Get, $"/api/v1/alerts/{alertId}")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
        });

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var raw = await response.Content.ReadAsStringAsync();
        Assert.DoesNotContain("snapshotReference", raw, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("protectedSharedSecret", raw, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("deviceRecordId", raw, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("/var/lib/weapon-detection", raw, StringComparison.OrdinalIgnoreCase);
    }

    // --- No N+1 (Phase 15 item 22 — provable by construction: AlertQueryService issues exactly one
    // Alert-Camera-Branch join query plus one Count query per List call, never a per-row follow-up
    // lookup; this test proves correctness at a multi-row, multi-camera, multi-branch scale rather
    // than instrumenting the SQL command count directly) ---------------------------------------------

    [Fact]
    public async Task List_AtScale_ReturnsCorrectCountsAcrossMultipleBranchesAndCameras()
    {
        var (branchA, deviceA, secretA) = await SeedActivatedBranchAsync("Scale Branch A " + Guid.NewGuid());
        var (branchB, deviceB, secretB) = await SeedActivatedBranchAsync("Scale Branch B " + Guid.NewGuid());
        var baseTime = new DateTime(2026, 7, 30, 17, 0, 0, DateTimeKind.Utc);
        for (var i = 0; i < 6; i++)
        {
            await SendEventAsync(deviceA, secretA, baseTime.AddMinutes(i), cameraId: i % 2 == 0 ? "camera1" : "camera2");
        }
        for (var i = 0; i < 4; i++)
        {
            await SendEventAsync(deviceB, secretB, baseTime.AddMinutes(10 + i));
        }

        var token = await LoginAsync();
        using var response = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Get, "/api/v1/alerts?pageSize=100")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
        });

        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        Assert.Equal(10, data.GetProperty("totalCount").GetInt32());
        Assert.Equal(6, data.GetProperty("items").EnumerateArray()
            .Count(i => i.GetProperty("branchId").GetGuid() == branchA));
        Assert.Equal(4, data.GetProperty("items").EnumerateArray()
            .Count(i => i.GetProperty("branchId").GetGuid() == branchB));
    }
}

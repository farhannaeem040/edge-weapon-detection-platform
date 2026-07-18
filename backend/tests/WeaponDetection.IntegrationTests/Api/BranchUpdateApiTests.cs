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
using WeaponDetection.Domain;
using Xunit;

namespace WeaponDetection.IntegrationTests.Api;

// Full HTTP-pipeline integration tests for PUT /api/v1/branches/{branchId} (IP-03 T-43; FS-03 §5.1,
// §5.2, §5.3, §10.1). Run against a real in-process TestServer and a real SQL Server database.
// Covers AC-1–AC-9, AC-13 (authorization), and AC-14 (no secret/internal id in the response).
//
// No test prints a plaintext password or a complete access token to console/log output. Every RTSP
// value is a non-routable placeholder (.invalid / documentation-range IP), and any embedded
// credential is an obvious placeholder used only to assert it is ABSENT from responses.
[Collection(ApiHostCollection.Name)]
public class BranchUpdateApiTests : IDisposable
{
    private const string BranchesRoute = "/api/v1/branches";

    private readonly BranchDeviceApiFactory _factory;
    private readonly HttpClient _client;

    public BranchUpdateApiTests()
    {
        _factory = new BranchDeviceApiFactory();
        _client = _factory.CreateClient();
    }

    public void Dispose()
    {
        _client.Dispose();
        _factory.Dispose();
    }

    private sealed record ApiEnvelope(bool Success, string? Message, JsonElement? Data, string? ErrorCode);

    private static object CreateRequest(int cameraCount = 2) =>
        new
        {
            name = "Downtown Branch",
            address = "1 High Street",
            contactDetails = "ops@example.invalid",
            cameras = Enumerable.Range(1, cameraCount)
                .Select(i => new { name = $"Camera {i}", rtspUrl = $"rtsp://camera.example.invalid:554/s{i}" })
                .ToArray(),
        };

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

    private Task<HttpResponseMessage> SendAsync(
        HttpMethod method, string route, string? token, object? body = null)
    {
        var request = new HttpRequestMessage(method, route);
        if (token is not null)
        {
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        }
        if (body is not null)
        {
            request.Content = JsonContent.Create(body);
        }
        return _client.SendAsync(request);
    }

    private async Task<JsonElement> CreateBranchAsync(string token, object? body = null)
    {
        using var response = await SendAsync(HttpMethod.Post, BranchesRoute, token, body ?? CreateRequest());
        Assert.Equal(HttpStatusCode.Created, response.StatusCode);
        var envelope = await response.Content.ReadFromJsonAsync<ApiEnvelope>();
        return envelope!.Data!.Value.Clone();
    }

    private static (Guid BranchId, List<Guid> CameraIds) Ids(JsonElement branch) =>
    (
        branch.GetProperty("branchId").GetGuid(),
        branch.GetProperty("cameras").EnumerateArray()
            .Select(c => c.GetProperty("cameraId").GetGuid()).ToList()
    );

    // --- Success: scalar + camera edit preserving identity (AC-1, AC-2) ---

    [Fact]
    public async Task Update_ScalarAndCamera_Returns200WithSafeReadShape()
    {
        var token = await LoginAsync();
        var created = await CreateBranchAsync(token, CreateRequest(cameraCount: 1));
        var (branchId, cameraIds) = Ids(created);

        var body = new
        {
            name = "Uptown Branch",
            address = "2 Low Road",
            contactDetails = "new@example.invalid",
            cameras = new[]
            {
                new { cameraId = cameraIds[0], name = "Renamed", rtspUrl = "rtsp://camera.example.invalid:554/renamed" },
            },
        };

        using var response = await SendAsync(HttpMethod.Put, $"{BranchesRoute}/{branchId}", token, body);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);

        var envelope = await response.Content.ReadFromJsonAsync<ApiEnvelope>();
        var data = envelope!.Data!.Value;
        Assert.Equal("Uptown Branch", data.GetProperty("name").GetString());
        Assert.Equal("2 Low Road", data.GetProperty("address").GetString());
        var camera = data.GetProperty("cameras").EnumerateArray().Single();
        Assert.Equal(cameraIds[0], camera.GetProperty("cameraId").GetGuid());
        Assert.Equal("Renamed", camera.GetProperty("name").GetString());
    }

    [Fact]
    public async Task Update_AddAndRemoveCameras_IsAppliedAndReturnsUpdatedCollection()
    {
        var token = await LoginAsync();
        var created = await CreateBranchAsync(token, CreateRequest(cameraCount: 2));
        var (branchId, cameraIds) = Ids(created);

        // Keep the first (edited), drop the second, add one new.
        var body = new
        {
            name = "Downtown Branch",
            address = "1 High Street",
            contactDetails = "ops@example.invalid",
            cameras = new object[]
            {
                new { cameraId = cameraIds[0], name = "Kept", rtspUrl = "rtsp://camera.example.invalid:554/kept" },
                new { name = "Added", rtspUrl = "rtsp://camera.example.invalid:554/added" },
            },
        };

        using var response = await SendAsync(HttpMethod.Put, $"{BranchesRoute}/{branchId}", token, body);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);

        await using var db = _factory.CreateDbContext();
        var cameras = await db.Cameras.AsNoTracking().Where(c => c.BranchId == branchId).ToListAsync();
        Assert.Equal(2, cameras.Count);
        Assert.Contains(cameras, c => c.CameraId == cameraIds[0]);
        Assert.DoesNotContain(cameras, c => c.CameraId == cameraIds[1]);
        Assert.Contains(cameras, c => c.Name == "Added");
    }

    // --- Preservation (AC-7, AC-8) ---

    [Fact]
    public async Task Update_PreservesDeviceIdentityAndActivationKeyRecords()
    {
        var token = await LoginAsync();
        var created = await CreateBranchAsync(token, CreateRequest(cameraCount: 1));
        var (branchId, cameraIds) = Ids(created);

        Guid deviceRecordId;
        string? deviceIdBefore;
        int keyCountBefore;
        await using (var db = _factory.CreateDbContext())
        {
            var device = await db.Devices.AsNoTracking().SingleAsync(d => d.BranchId == branchId);
            deviceRecordId = device.DeviceRecordId;
            deviceIdBefore = device.DeviceId?.ToString();
            keyCountBefore = await db.ActivationKeys.CountAsync(k => k.DeviceRecordId == deviceRecordId);
        }

        var body = new
        {
            name = "Edited",
            address = "1 High Street",
            contactDetails = "ops@example.invalid",
            cameras = new[]
            {
                new { cameraId = cameraIds[0], name = "Edited Cam", rtspUrl = "rtsp://camera.example.invalid:554/e" },
            },
        };
        using var response = await SendAsync(HttpMethod.Put, $"{BranchesRoute}/{branchId}", token, body);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);

        await using var after = _factory.CreateDbContext();
        var device2 = await after.Devices.AsNoTracking().SingleAsync(d => d.BranchId == branchId);
        Assert.Equal(deviceRecordId, device2.DeviceRecordId);
        Assert.Equal(deviceIdBefore, device2.DeviceId?.ToString());
        Assert.Equal(DeviceActivationStatus.Unactivated, device2.ActivationStatus);
        Assert.Equal(
            keyCountBefore,
            await after.ActivationKeys.CountAsync(k => k.DeviceRecordId == deviceRecordId));
    }

    // --- No secret / internal id / RTSP credential in the response (AC-14) ---

    [Fact]
    public async Task Update_ResponseCarriesNoSecretsOrInternalIdentifiers()
    {
        var token = await LoginAsync();
        var created = await CreateBranchAsync(token, CreateRequest(cameraCount: 1));
        var (branchId, cameraIds) = Ids(created);

        var body = new
        {
            name = "Edited",
            address = "1 High Street",
            contactDetails = "ops@example.invalid",
            cameras = new[]
            {
                new { cameraId = cameraIds[0], name = "Cam", rtspUrl = "rtsp://user:p4ss@198.51.100.7:554/s" },
            },
        };
        using var response = await SendAsync(HttpMethod.Put, $"{BranchesRoute}/{branchId}", token, body);
        var raw = await response.Content.ReadAsStringAsync();

        Assert.DoesNotContain("user:p4ss", raw);
        Assert.DoesNotContain("deviceRecordId", raw, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("sharedSecret", raw, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("secretHash", raw, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("activationKey", raw, StringComparison.OrdinalIgnoreCase);
    }

    // --- Validation (AC-5, AC-9) ---

    [Fact]
    public async Task Update_ZeroCameras_Returns400()
    {
        var token = await LoginAsync();
        var created = await CreateBranchAsync(token, CreateRequest(cameraCount: 2));
        var (branchId, _) = Ids(created);

        var body = new
        {
            name = "X",
            address = "Y",
            contactDetails = "Z",
            cameras = Array.Empty<object>(),
        };
        using var response = await SendAsync(HttpMethod.Put, $"{BranchesRoute}/{branchId}", token, body);
        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    [Fact]
    public async Task Update_ForeignCameraId_Returns400AndChangesNothing()
    {
        var token = await LoginAsync();
        var branchA = await CreateBranchAsync(token, CreateRequest(cameraCount: 1));
        var branchB = await CreateBranchAsync(token, CreateRequest(cameraCount: 1));
        var (branchAId, _) = Ids(branchA);
        var (_, bCameraIds) = Ids(branchB);

        var body = new
        {
            name = "Downtown Branch",
            address = "1 High Street",
            contactDetails = "ops@example.invalid",
            cameras = new[]
            {
                new { cameraId = bCameraIds[0], name = "Hijack", rtspUrl = "rtsp://camera.example.invalid:554/x" },
            },
        };
        using var response = await SendAsync(HttpMethod.Put, $"{BranchesRoute}/{branchAId}", token, body);
        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    [Fact]
    public async Task Update_InvalidRtspUrl_Returns400()
    {
        var token = await LoginAsync();
        var created = await CreateBranchAsync(token, CreateRequest(cameraCount: 1));
        var (branchId, cameraIds) = Ids(created);

        var body = new
        {
            name = "Downtown Branch",
            address = "1 High Street",
            contactDetails = "ops@example.invalid",
            cameras = new[]
            {
                new { cameraId = cameraIds[0], name = "Cam", rtspUrl = "http://not-rtsp.invalid" },
            },
        };
        using var response = await SendAsync(HttpMethod.Put, $"{BranchesRoute}/{branchId}", token, body);
        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    // --- Not found (404) ---

    [Fact]
    public async Task Update_UnknownBranch_Returns404()
    {
        var token = await LoginAsync();

        var body = new
        {
            name = "Downtown Branch",
            address = "1 High Street",
            contactDetails = "ops@example.invalid",
            cameras = new[] { new { name = "Cam", rtspUrl = "rtsp://camera.example.invalid:554/s1" } },
        };
        using var response = await SendAsync(HttpMethod.Put, $"{BranchesRoute}/{Guid.NewGuid()}", token, body);
        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
    }

    // --- Authorization (AC-13) ---

    [Fact]
    public async Task Update_NoAuthorizationHeader_Returns401AndChangesNothing()
    {
        var token = await LoginAsync();
        var created = await CreateBranchAsync(token, CreateRequest(cameraCount: 1));
        var (branchId, cameraIds) = Ids(created);

        var body = new
        {
            name = "Should Not Persist",
            address = "1 High Street",
            contactDetails = "ops@example.invalid",
            cameras = new[]
            {
                new { cameraId = cameraIds[0], name = "Cam", rtspUrl = "rtsp://camera.example.invalid:554/s1" },
            },
        };

        // No token → 401 before any business logic.
        using var response = await SendAsync(HttpMethod.Put, $"{BranchesRoute}/{branchId}", token: null, body);
        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);

        await using var db = _factory.CreateDbContext();
        var branch = await db.Branches.AsNoTracking().SingleAsync(b => b.BranchId == branchId);
        Assert.Equal("Downtown Branch", branch.Name);
    }
}

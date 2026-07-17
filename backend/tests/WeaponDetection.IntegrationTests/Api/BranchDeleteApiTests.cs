using System;
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

// Full HTTP-pipeline integration tests for DELETE /api/v1/branches/{branchId} (IP-03 T-44; FS-03
// §5.5, §5.6, §10.2). Run against a real in-process TestServer and a real SQL Server database.
// Covers AC-10 (all dependents removed, activated and unactivated), AC-11 (other branches
// unaffected), AC-12 (deleted device no longer recognisable), AC-13 (authorization), and AC-14 (no
// secret/internal id in the response).
//
// No test prints a plaintext password or access token to console/log output.
[Collection(ApiHostCollection.Name)]
public class BranchDeleteApiTests : IDisposable
{
    private const string BranchesRoute = "/api/v1/branches";

    private readonly BranchDeviceApiFactory _factory;
    private readonly HttpClient _client;

    public BranchDeleteApiTests()
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

    private static object CreateRequest(string name = "Downtown Branch") =>
        new
        {
            name,
            address = "1 High Street",
            contactDetails = "ops@example.invalid",
            cameras = new[]
            {
                new { name = "Camera 1", rtspUrl = "rtsp://camera.example.invalid:554/s1" },
                new { name = "Camera 2", rtspUrl = "rtsp://camera.example.invalid:554/s2" },
            },
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

    private Task<HttpResponseMessage> SendAsync(HttpMethod method, string route, string? token)
    {
        var request = new HttpRequestMessage(method, route);
        if (token is not null)
        {
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        }
        return _client.SendAsync(request);
    }

    private async Task<(Guid BranchId, string ActivationKey)> CreateBranchAsync(
        string token, object? body = null)
    {
        var request = new HttpRequestMessage(HttpMethod.Post, BranchesRoute)
        {
            Content = JsonContent.Create(body ?? CreateRequest()),
        };
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        using var response = await _client.SendAsync(request);
        Assert.Equal(HttpStatusCode.Created, response.StatusCode);
        var envelope = await response.Content.ReadFromJsonAsync<ApiEnvelope>();
        var data = envelope!.Data!.Value;
        return (data.GetProperty("branchId").GetGuid(), data.GetProperty("activationKey").GetString()!);
    }

    // --- Delete unactivated branch removes every dependent (AC-10) ---

    [Fact]
    public async Task Delete_UnactivatedBranch_Returns200AndRemovesAllDependents()
    {
        var token = await LoginAsync();
        var (branchId, _) = await CreateBranchAsync(token);

        using var response = await SendAsync(HttpMethod.Delete, $"{BranchesRoute}/{branchId}", token);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);

        await using var db = _factory.CreateDbContext();
        Assert.Equal(0, await db.Branches.CountAsync(b => b.BranchId == branchId));
        Assert.Equal(0, await db.Cameras.CountAsync(c => c.BranchId == branchId));
        Assert.Equal(0, await db.Devices.CountAsync(d => d.BranchId == branchId));
        // No orphaned activation keys either.
        Assert.Equal(0, await db.ActivationKeys.CountAsync());
    }

    // --- Delete activated branch removes the activated device too (AC-10, AC-12) ---

    [Fact]
    public async Task Delete_ActivatedBranch_RemovesTheActivatedDeviceAndItsCredentials()
    {
        var token = await LoginAsync();
        var (branchId, activationKey) = await CreateBranchAsync(token);

        // Activate through the real endpoint so the Device has a DeviceId and a protected secret.
        using (var activate = new HttpRequestMessage(HttpMethod.Post, "/api/v1/activate")
        {
            Content = JsonContent.Create(new { activationKey }),
        })
        {
            using var activateResponse = await _client.SendAsync(activate);
            Assert.Equal(HttpStatusCode.OK, activateResponse.StatusCode);
        }

        Guid deviceId;
        await using (var before = _factory.CreateDbContext())
        {
            var device = await before.Devices.AsNoTracking().SingleAsync(d => d.BranchId == branchId);
            Assert.Equal(DeviceActivationStatus.Activated, device.ActivationStatus);
            Assert.NotNull(device.DeviceId);
            deviceId = device.DeviceId!.Value;
        }

        using var response = await SendAsync(HttpMethod.Delete, $"{BranchesRoute}/{branchId}", token);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);

        await using var after = _factory.CreateDbContext();
        // The activated device is gone: no record with that DeviceId remains, so the Backend can no
        // longer authenticate any request bearing it (AC-12).
        Assert.Equal(0, await after.Devices.CountAsync(d => d.DeviceId == deviceId));
        Assert.Equal(0, await after.Branches.CountAsync(b => b.BranchId == branchId));
        Assert.Equal(0, await after.ActivationKeys.CountAsync());
    }

    // --- Response carries no secret / internal id (AC-14) ---

    [Fact]
    public async Task Delete_ResponseCarriesNoSecretsOrInternalIdentifiers()
    {
        var token = await LoginAsync();
        var (branchId, activationKey) = await CreateBranchAsync(token);
        using (var activate = new HttpRequestMessage(HttpMethod.Post, "/api/v1/activate")
        {
            Content = JsonContent.Create(new { activationKey }),
        })
        {
            (await _client.SendAsync(activate)).Dispose();
        }

        using var response = await SendAsync(HttpMethod.Delete, $"{BranchesRoute}/{branchId}", token);
        var raw = await response.Content.ReadAsStringAsync();

        Assert.DoesNotContain("deviceRecordId", raw, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("sharedSecret", raw, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("secret", raw, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("activationKey", raw, StringComparison.OrdinalIgnoreCase);
        // A bare success envelope only.
        using var document = JsonDocument.Parse(raw);
        Assert.True(document.RootElement.GetProperty("success").GetBoolean());
    }

    // --- Other branches unaffected (AC-11) ---

    [Fact]
    public async Task Delete_OneBranch_LeavesAnotherBranchIntact()
    {
        var token = await LoginAsync();
        var (branchId, _) = await CreateBranchAsync(token, CreateRequest("First"));
        var (survivorId, _) = await CreateBranchAsync(token, CreateRequest("Second"));

        using var response = await SendAsync(HttpMethod.Delete, $"{BranchesRoute}/{branchId}", token);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);

        await using var db = _factory.CreateDbContext();
        Assert.Equal(1, await db.Branches.CountAsync(b => b.BranchId == survivorId));
        Assert.Equal(2, await db.Cameras.CountAsync(c => c.BranchId == survivorId));
        Assert.Equal(1, await db.Devices.CountAsync(d => d.BranchId == survivorId));
        Assert.Equal(1, await db.ActivationKeys.CountAsync());
    }

    // --- Not found (404) ---

    [Fact]
    public async Task Delete_UnknownBranch_Returns404()
    {
        var token = await LoginAsync();

        using var response = await SendAsync(HttpMethod.Delete, $"{BranchesRoute}/{Guid.NewGuid()}", token);

        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
    }

    // --- Authorization (AC-13) ---

    [Fact]
    public async Task Delete_NoAuthorizationHeader_Returns401AndDeletesNothing()
    {
        var token = await LoginAsync();
        var (branchId, _) = await CreateBranchAsync(token);

        using var response = await SendAsync(HttpMethod.Delete, $"{BranchesRoute}/{branchId}", token: null);
        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);

        await using var db = _factory.CreateDbContext();
        Assert.Equal(1, await db.Branches.CountAsync(b => b.BranchId == branchId));
    }
}

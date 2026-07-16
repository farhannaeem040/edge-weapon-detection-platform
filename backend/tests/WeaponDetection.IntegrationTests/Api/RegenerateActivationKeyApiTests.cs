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
using WeaponDetection.Infrastructure.Security;
using Xunit;

namespace WeaponDetection.IntegrationTests.Api;

// Full HTTP-pipeline integration tests for POST /api/v1/devices/{id}/activation-key/regenerate
// (IP-01 T-18, FS-02 §10.2, §15 T-03, AC-5), run against a real in-process TestServer and a real
// SQL Server database. The route's {id} is the branch id (FS-02 §1.3 — regeneration must work
// before a device has any external DeviceId); the endpoint returns the new complete plaintext key
// exactly once, invalidates the old key, and never discloses the old key, any hash, the internal
// DeviceRecordId, or the protected secret.
//
// Each test method constructs its own host and its own freshly migrated, empty database (see
// BranchApiTests for the rationale); the ApiHostCollection serialises them. Where a test captures a
// plaintext Activation Key, it does so only to assert its presence/absence — never to log it.
[Collection(ApiHostCollection.Name)]
public class RegenerateActivationKeyApiTests : IDisposable
{
    private const string BranchesRoute = "/api/v1/branches";
    private const string DevicesRoute = "/api/v1/devices";

    private readonly BranchDeviceApiFactory _factory;
    private readonly HttpClient _client;

    public RegenerateActivationKeyApiTests()
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

    private static string RegenerateRoute(Guid branchId) =>
        $"{DevicesRoute}/{branchId}/activation-key/regenerate";

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

    // Creates a branch as the Dashboard would, returning its id and the create-time plaintext key.
    private async Task<(Guid BranchId, string OriginalKey)> CreateBranchAsync(string token)
    {
        var response = await _client.SendAsync(new HttpRequestMessage(HttpMethod.Post, BranchesRoute)
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
            Content = JsonContent.Create(new
            {
                name = "Downtown Branch",
                address = "1 High Street",
                contactDetails = "ops@example.local",
                cameras = new[] { new { name = "Front Entrance", rtspUrl = "rtsp://camera.example.local:554/stream1" } },
            }),
        });

        Assert.Equal(HttpStatusCode.Created, response.StatusCode);
        var envelope = await response.Content.ReadFromJsonAsync<ApiEnvelope>();
        response.Dispose();

        var data = envelope!.Data!.Value;
        return (data.GetProperty("branchId").GetGuid(), data.GetProperty("activationKey").GetString()!);
    }

    private static (string KeyId, string Secret) SplitKey(string plaintextKey)
    {
        var parts = plaintextKey.Split(ActivationKeyGenerator.Delimiter);
        Assert.Equal(2, parts.Length);
        Assert.All(parts, part => Assert.False(string.IsNullOrWhiteSpace(part)));
        return (parts[0], parts[1]);
    }

    // --- Authentication (FS-02 §10.2 "Valid Admin JWT + active session") ---

    [Fact]
    public async Task Regenerate_NoAuthorizationHeader_Returns401()
    {
        using var response = await SendAsync(HttpMethod.Post, RegenerateRoute(Guid.NewGuid()), token: null);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    // --- Not found (FS-02 §10.2, §13 → 404) ---

    [Fact]
    public async Task Regenerate_UnknownBranch_Returns404()
    {
        var token = await LoginAsync();

        using var response = await SendAsync(HttpMethod.Post, RegenerateRoute(Guid.NewGuid()), token);

        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);

        var envelope = await response.Content.ReadFromJsonAsync<ApiEnvelope>();
        Assert.False(envelope!.Success);
        Assert.Equal("NOT_FOUND", envelope.ErrorCode);
    }

    // --- Success (FS-02 §10.2, §15 T-03, AC-5) ---

    [Fact]
    public async Task Regenerate_ExistingBranch_Returns200WithANewCompleteTwoPartKey()
    {
        var token = await LoginAsync();
        var (branchId, originalKey) = await CreateBranchAsync(token);

        using var response = await SendAsync(HttpMethod.Post, RegenerateRoute(branchId), token);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);

        var envelope = await response.Content.ReadFromJsonAsync<ApiEnvelope>();
        Assert.True(envelope!.Success);

        var newKey = envelope.Data!.Value.GetProperty("activationKey").GetString()!;
        SplitKey(newKey);
        // A fresh key is issued, not the original (FS-02 §5.3 step 4).
        Assert.NotEqual(originalKey, newKey);
    }

    [Fact]
    public async Task Regenerate_InvalidatesTheOldKeyAndPersistsANewUnconsumedOne()
    {
        var token = await LoginAsync();
        var (branchId, originalKey) = await CreateBranchAsync(token);
        var (originalKeyId, _) = SplitKey(originalKey);

        using var response = await SendAsync(HttpMethod.Post, RegenerateRoute(branchId), token);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);

        var envelope = await response.Content.ReadFromJsonAsync<ApiEnvelope>();
        var (newKeyId, _) = SplitKey(envelope!.Data!.Value.GetProperty("activationKey").GetString()!);

        await using var dbContext = _factory.CreateDbContext();
        var device = await dbContext.Devices.AsNoTracking().SingleAsync(d => d.BranchId == branchId);
        var keys = await dbContext.ActivationKeys.AsNoTracking()
            .Where(k => k.DeviceRecordId == device.DeviceRecordId)
            .ToListAsync();

        Assert.Equal(2, keys.Count);
        Assert.Equal(ActivationKeyStatus.Invalidated, keys.Single(k => k.ActivationKeyId == originalKeyId).Status);
        Assert.Equal(ActivationKeyStatus.Unconsumed, keys.Single(k => k.ActivationKeyId == newKeyId).Status);
        // The device is not activated by regeneration (FS-02 §5.3).
        Assert.Equal(DeviceActivationStatus.Unactivated, device.ActivationStatus);
        Assert.Null(device.DeviceId);
    }

    [Fact]
    public async Task Regenerate_ResponseNeverDisclosesTheOldKeyOrInternalIdentifiers()
    {
        var token = await LoginAsync();
        var (branchId, originalKey) = await CreateBranchAsync(token);

        using var response = await SendAsync(HttpMethod.Post, RegenerateRoute(branchId), token);
        var body = await response.Content.ReadAsStringAsync();

        // The now-invalidated original key must not appear in the regeneration response, nor may any
        // internal identifier or stored secret (FS-02 §1.3, §5.3, §11).
        Assert.DoesNotContain(originalKey, body);
        Assert.DoesNotContain("deviceRecordId", body, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("secretHash", body, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("protectedSharedSecret", body, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public async Task Regenerate_NewKeyIsNotSurfacedByLaterReadResponses()
    {
        var token = await LoginAsync();
        var (branchId, _) = await CreateBranchAsync(token);

        using var regenerate = await SendAsync(HttpMethod.Post, RegenerateRoute(branchId), token);
        var regenerateEnvelope = await regenerate.Content.ReadFromJsonAsync<ApiEnvelope>();
        var newKey = regenerateEnvelope!.Data!.Value.GetProperty("activationKey").GetString()!;

        // The plaintext key is disclosed only by the regeneration response; a subsequent branch read
        // must not carry it (FS-02 §5.4, §7 — single disclosure).
        using var read = await SendAsync(HttpMethod.Get, $"{BranchesRoute}/{branchId}", token);
        var readBody = await read.Content.ReadAsStringAsync();
        Assert.DoesNotContain(newKey, readBody);
        Assert.DoesNotContain("activationKey", readBody, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public async Task Regenerate_RepeatedCalls_EachReturnADistinctKeyAndInvalidateThePrevious()
    {
        var token = await LoginAsync();
        var (branchId, originalKey) = await CreateBranchAsync(token);

        using var first = await SendAsync(HttpMethod.Post, RegenerateRoute(branchId), token);
        var firstKey = (await first.Content.ReadFromJsonAsync<ApiEnvelope>())!
            .Data!.Value.GetProperty("activationKey").GetString()!;

        using var second = await SendAsync(HttpMethod.Post, RegenerateRoute(branchId), token);
        var secondKey = (await second.Content.ReadFromJsonAsync<ApiEnvelope>())!
            .Data!.Value.GetProperty("activationKey").GetString()!;

        Assert.NotEqual(originalKey, firstKey);
        Assert.NotEqual(firstKey, secondKey);

        // Exactly one live (non-Invalidated) key remains — the most recent (FS-02 §5.3).
        await using var dbContext = _factory.CreateDbContext();
        var device = await dbContext.Devices.AsNoTracking().SingleAsync(d => d.BranchId == branchId);
        var live = await dbContext.ActivationKeys.AsNoTracking()
            .CountAsync(k => k.DeviceRecordId == device.DeviceRecordId
                && k.Status != ActivationKeyStatus.Invalidated);
        Assert.Equal(1, live);
    }
}

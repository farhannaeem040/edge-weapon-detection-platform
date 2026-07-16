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
using Microsoft.Extensions.DependencyInjection;
using WeaponDetection.Application.Interfaces;
using WeaponDetection.Domain;
using WeaponDetection.Infrastructure.Security;
using Xunit;

namespace WeaponDetection.IntegrationTests.Api;

// Full HTTP-pipeline integration tests for POST /api/v1/activate (IP-01 T-20, FS-02 §10.4, §5.5-§5.7,
// §15 T-07-T-13; AC-3, AC-4, AC-9, AC-12, AC-15), run against a real in-process TestServer and a real
// SQL Server database. A plain HttpClient with no Authorization header stands in for the Jetson Agent
// (real or simulated, FS-02 §1.2) — the endpoint is authenticated by the Activation Key itself.
//
// The dedicated two-concurrent-requests test for AC-16 is a separate task (IP-01 T-21); this file
// covers single-caller behaviour. Where a test captures a plaintext key or shared secret it does so
// only to assert its presence in the one response that may carry it, or its ABSENCE elsewhere — never
// to log it. Each test constructs its own host and freshly migrated, empty database; the
// ApiHostCollection serialises them.
[Collection(ApiHostCollection.Name)]
public class ActivateApiTests : IDisposable
{
    private const string ActivateRoute = "/api/v1/activate";
    private const string BranchesRoute = "/api/v1/branches";
    private const string DevicesRoute = "/api/v1/devices";

    private readonly BranchDeviceApiFactory _factory;
    private readonly HttpClient _client;

    public ActivateApiTests()
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

    // Creates a branch as the Dashboard would (authenticated), returning the branch id and the
    // create-time plaintext Activation Key.
    private async Task<(Guid BranchId, string Key)> CreateBranchAsync(string token, string name = "Downtown Branch")
    {
        var response = await _client.SendAsync(new HttpRequestMessage(HttpMethod.Post, BranchesRoute)
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
            Content = JsonContent.Create(new
            {
                name,
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

    // Calls POST /api/v1/activate with NO Authorization header — the Agent's exact call.
    private Task<HttpResponseMessage> ActivateAsync(string? activationKey) =>
        _client.PostAsJsonAsync(ActivateRoute, new { activationKey });

    // Regenerates a branch's key (authenticated), returning the new plaintext key.
    private async Task<string> RegenerateAsync(string token, Guid branchId)
    {
        var response = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Post, $"{DevicesRoute}/{branchId}/activation-key/regenerate")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
        });
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var envelope = await response.Content.ReadFromJsonAsync<ApiEnvelope>();
        response.Dispose();
        return envelope!.Data!.Value.GetProperty("activationKey").GetString()!;
    }

    private static string KeyIdOf(string key) => key.Split(ActivationKeyGenerator.Delimiter)[0];

    // --- Success (FS-02 §10.4, §5.5; AC-3, AC-12) ---

    [Fact]
    public async Task Activate_ValidKey_WithoutJwt_Returns200WithDeviceIdSecretAndBranchId()
    {
        var token = await LoginAsync();
        var (branchId, key) = await CreateBranchAsync(token);

        using var response = await ActivateAsync(key);

        // No JWT was sent; the endpoint still succeeds — the Activation Key is the credential.
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var envelope = await response.Content.ReadFromJsonAsync<ApiEnvelope>();
        Assert.True(envelope!.Success);
        var data = envelope.Data!.Value;
        Assert.NotEqual(Guid.Empty, data.GetProperty("deviceId").GetGuid());
        Assert.False(string.IsNullOrWhiteSpace(data.GetProperty("sharedSecret").GetString()));
        Assert.Equal(branchId, data.GetProperty("branchId").GetGuid());
    }

    [Fact]
    public async Task Activate_ValidKey_PersistsActivatedDeviceWithTheReturnedDeviceIdAndConsumesTheKey()
    {
        var token = await LoginAsync();
        var (branchId, key) = await CreateBranchAsync(token);

        using var response = await ActivateAsync(key);
        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        var deviceId = data.GetProperty("deviceId").GetGuid();

        await using var db = _factory.CreateDbContext();
        var device = await db.Devices.AsNoTracking().SingleAsync(d => d.BranchId == branchId);
        Assert.Equal(DeviceActivationStatus.Activated, device.ActivationStatus);
        Assert.Equal(deviceId, device.DeviceId);

        var storedKey = await db.ActivationKeys.AsNoTracking().SingleAsync(k => k.ActivationKeyId == KeyIdOf(key));
        Assert.Equal(ActivationKeyStatus.Consumed, storedKey.Status);
    }

    [Fact]
    public async Task Activate_ValidKey_StoresOnlyTheProtectedSecretWhichUnprotectsToTheReturnedValue()
    {
        var token = await LoginAsync();
        var (branchId, key) = await CreateBranchAsync(token);

        using var response = await ActivateAsync(key);
        var returnedSecret = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!
            .Data!.Value.GetProperty("sharedSecret").GetString()!;

        await using var db = _factory.CreateDbContext();
        var device = await db.Devices.AsNoTracking().SingleAsync(d => d.BranchId == branchId);

        Assert.NotNull(device.ProtectedSharedSecret);
        // Never the plaintext (FS-02 §11) — but the protected form recovers exactly the returned
        // secret (ARCH-001 §13.3). Unprotect with the host's own protector (same key ring).
        Assert.NotEqual(returnedSecret, device.ProtectedSharedSecret);
        using var scope = _factory.Services.CreateScope();
        var protector = scope.ServiceProvider.GetRequiredService<IDeviceSecretProtector>();
        Assert.Equal(returnedSecret, protector.Unprotect(device.ProtectedSharedSecret!));
    }

    // --- Replay (FS-02 §5.7; AC-4, AC-9) ---

    [Fact]
    public async Task Activate_ReplayOfConsumedKey_IsRejected_WithNoChangeToTheDevice()
    {
        var token = await LoginAsync();
        var (branchId, key) = await CreateBranchAsync(token);

        using var first = await ActivateAsync(key);
        var firstSecret = (await first.Content.ReadFromJsonAsync<ApiEnvelope>())!
            .Data!.Value.GetProperty("sharedSecret").GetString()!;

        using var replay = await ActivateAsync(key);
        Assert.Equal(HttpStatusCode.Unauthorized, replay.StatusCode);

        // The device from the first activation is unaffected: still Activated, same protected secret.
        await using var db = _factory.CreateDbContext();
        var device = await db.Devices.AsNoTracking().SingleAsync(d => d.BranchId == branchId);
        Assert.Equal(DeviceActivationStatus.Activated, device.ActivationStatus);
        using var scope = _factory.Services.CreateScope();
        var protector = scope.ServiceProvider.GetRequiredService<IDeviceSecretProtector>();
        Assert.Equal(firstSecret, protector.Unprotect(device.ProtectedSharedSecret!));
    }

    // --- Uniform rejection (FS-02 §5.6, §10.4, §13; AC-15) ---

    [Fact]
    public async Task Activate_EveryRejectionReason_ReturnsIdentical401BodyAndErrorCode()
    {
        var token = await LoginAsync();

        // malformed (no delimiter), unknown keyId, and correct keyId with wrong secret — all on a
        // fresh, still-unconsumed branch.
        var (_, liveKey) = await CreateBranchAsync(token, "Branch Live");
        var bodies = new List<(HttpStatusCode Status, string Body)>();
        foreach (var candidate in new[]
                 {
                     "malformed-no-delimiter",
                     "unknownkeyid.somesecretvalue",
                     $"{KeyIdOf(liveKey)}.definitely-not-the-real-secret",
                 })
        {
            using var r = await ActivateAsync(candidate);
            bodies.Add((r.StatusCode, await r.Content.ReadAsStringAsync()));
        }

        // consumed: a branch whose key has been activated, then reused.
        var (_, consumedKey) = await CreateBranchAsync(token, "Branch Consumed");
        (await ActivateAsync(consumedKey)).Dispose();
        using (var consumed = await ActivateAsync(consumedKey))
        {
            bodies.Add((consumed.StatusCode, await consumed.Content.ReadAsStringAsync()));
        }

        // invalidated: a branch whose original key was superseded by regeneration.
        var (invBranchId, invalidatedKey) = await CreateBranchAsync(token, "Branch Invalidated");
        await RegenerateAsync(token, invBranchId);
        using (var invalidated = await ActivateAsync(invalidatedKey))
        {
            bodies.Add((invalidated.StatusCode, await invalidated.Content.ReadAsStringAsync()));
        }

        // Every one is 401 with a byte-identical body: the caller cannot tell which check failed.
        Assert.All(bodies, b => Assert.Equal(HttpStatusCode.Unauthorized, b.Status));
        var canonical = bodies[0].Body;
        Assert.All(bodies, b => Assert.Equal(canonical, b.Body));

        using var document = JsonDocument.Parse(canonical);
        Assert.False(document.RootElement.GetProperty("success").GetBoolean());
        Assert.Equal("INVALID_ACTIVATION_KEY", document.RootElement.GetProperty("errorCode").GetString());
        // The typed internal reason must not leak.
        Assert.DoesNotContain("Malformed", canonical, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("Consumed", canonical, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("Invalidated", canonical, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("secret", canonical, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public async Task Activate_UnknownAndWrongSecretKeys_MutateNoState()
    {
        var token = await LoginAsync();
        var (branchId, key) = await CreateBranchAsync(token);

        using (var unknown = await ActivateAsync("unknownkeyid.secretvalue"))
        {
            Assert.Equal(HttpStatusCode.Unauthorized, unknown.StatusCode);
        }
        using (var wrong = await ActivateAsync($"{KeyIdOf(key)}.wrong-secret"))
        {
            Assert.Equal(HttpStatusCode.Unauthorized, wrong.StatusCode);
        }

        // The device is still unactivated and the key still unconsumed — a rejected attempt has no
        // side effects (FS-02 §5.6).
        await using var db = _factory.CreateDbContext();
        var device = await db.Devices.AsNoTracking().SingleAsync(d => d.BranchId == branchId);
        Assert.Equal(DeviceActivationStatus.Unactivated, device.ActivationStatus);
        Assert.Null(device.DeviceId);
        var storedKey = await db.ActivationKeys.AsNoTracking().SingleAsync(k => k.ActivationKeyId == KeyIdOf(key));
        Assert.Equal(ActivationKeyStatus.Unconsumed, storedKey.Status);
    }

    // --- The AllowAnonymous exemption does not leak to other endpoints (FS-02 §11, AC-6) ---

    [Fact]
    public async Task ProtectedAdminRoutes_RemainProtected_AfterActivateIsAnonymous()
    {
        // The activate endpoint is anonymous, but the fallback policy still guards every other route.
        using var branches = await _client.GetAsync(BranchesRoute);
        Assert.Equal(HttpStatusCode.Unauthorized, branches.StatusCode);

        using var device = await _client.GetAsync($"{DevicesRoute}/{Guid.NewGuid()}");
        Assert.Equal(HttpStatusCode.Unauthorized, device.StatusCode);
    }

    // --- The shared secret never appears in a read response (FS-02 §11) ---

    [Fact]
    public async Task Activate_ThenGetDevice_NeverExposesTheSharedSecret()
    {
        var token = await LoginAsync();
        var (_, key) = await CreateBranchAsync(token);

        using var activate = await ActivateAsync(key);
        var data = (await activate.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        var deviceId = data.GetProperty("deviceId").GetGuid();
        var sharedSecret = data.GetProperty("sharedSecret").GetString()!;

        using var get = await _client.SendAsync(new HttpRequestMessage(HttpMethod.Get, $"{DevicesRoute}/{deviceId}")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
        });
        var body = await get.Content.ReadAsStringAsync();

        Assert.Equal(HttpStatusCode.OK, get.StatusCode);
        Assert.DoesNotContain(sharedSecret, body);
        Assert.DoesNotContain("sharedSecret", body, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("protectedSharedSecret", body, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("deviceRecordId", body, StringComparison.OrdinalIgnoreCase);
    }

    // --- No sensitive material in a rejection body (FS-02 §11) ---

    [Fact]
    public async Task Activate_RejectionBody_ContainsNoPresentedKeyMaterial()
    {
        var token = await LoginAsync();
        var (_, key) = await CreateBranchAsync(token);
        var wrong = $"{KeyIdOf(key)}.super-secret-guess";

        using var response = await ActivateAsync(wrong);
        var body = await response.Content.ReadAsStringAsync();

        // The rejection must not echo the presented key, its keyId, or the guessed secret.
        Assert.DoesNotContain(wrong, body);
        Assert.DoesNotContain(KeyIdOf(key), body);
        Assert.DoesNotContain("super-secret-guess", body);
    }

    // --- Reactivation over HTTP (FS-02 §5.8, §15 T-14; AC-7, AC-9, AC-12) ---

    [Fact]
    public async Task Activate_Regenerate_Reactivate_RetainsDeviceIdAndRotatesSharedSecret()
    {
        var token = await LoginAsync();
        var (branchId, firstKey) = await CreateBranchAsync(token);

        using var firstActivate = await ActivateAsync(firstKey);
        var firstData = (await firstActivate.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        var firstDeviceId = firstData.GetProperty("deviceId").GetGuid();
        var firstSecret = firstData.GetProperty("sharedSecret").GetString()!;

        // Reactivation requires a freshly regenerated key (BR-003); the old one is now consumed.
        var secondKey = await RegenerateAsync(token, branchId);
        using var secondActivate = await ActivateAsync(secondKey);
        Assert.Equal(HttpStatusCode.OK, secondActivate.StatusCode);
        var secondData = (await secondActivate.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;

        // Persistent DeviceId retained (AC-7); a new shared secret issued, replacing the previous
        // one (NFR-SEC-002, ADR-015).
        Assert.Equal(firstDeviceId, secondData.GetProperty("deviceId").GetGuid());
        Assert.NotEqual(firstSecret, secondData.GetProperty("sharedSecret").GetString());
    }
}

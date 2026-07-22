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

// Full HTTP-pipeline integration tests for POST /api/v1/device/credentials/validate (IP-05 T-52,
// FS-02 §10.5, ADR-017), against a real in-process TestServer and real SQL Server. A plain HttpClient
// with no Authorization header stands in for the running Agent — the endpoint is device-authenticated
// by the X-Device-Id / X-Device-Secret headers, not an Admin session.
//
// The device shared secret is captured from the activation response only to present it back (or a
// wrong value), and to assert its ABSENCE from responses — never to log it. Each test constructs its
// own host and freshly migrated database; ApiHostCollection serialises them.
[Collection(ApiHostCollection.Name)]
public class DeviceCredentialValidationApiTests : IDisposable
{
    private const string ValidateRoute = "/api/v1/device/credentials/validate";
    private const string ActivateRoute = "/api/v1/activate";
    private const string BranchesRoute = "/api/v1/branches";
    private const string DevicesRoute = "/api/v1/devices";
    private const string DeviceIdHeader = "X-Device-Id";
    private const string DeviceSecretHeader = "X-Device-Secret";

    private readonly BranchDeviceApiFactory _factory;
    private readonly HttpClient _client;

    public DeviceCredentialValidationApiTests()
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
        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        response.Dispose();
        return (data.GetProperty("branchId").GetGuid(), data.GetProperty("activationKey").GetString()!);
    }

    // Activates a branch's device and returns its assigned DeviceId and disclosed shared secret.
    private async Task<(Guid BranchId, Guid DeviceId, string SharedSecret)> ActivateNewDeviceAsync(
        string token, string name = "Downtown Branch")
    {
        var (branchId, key) = await CreateBranchAsync(token, name);
        using var response = await _client.PostAsJsonAsync(ActivateRoute, new { activationKey = key });
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        return (branchId, data.GetProperty("deviceId").GetGuid(), data.GetProperty("sharedSecret").GetString()!);
    }

    private async Task RegenerateAsync(string token, Guid branchId)
    {
        using var response = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Post, $"{DevicesRoute}/{branchId}/activation-key/regenerate")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
        });
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }

    // POSTs to the validation endpoint. A null header value omits that header entirely (to exercise
    // the missing-header paths); the request carries no body, exactly as the Agent's call does.
    private Task<HttpResponseMessage> ValidateAsync(string? deviceId, string? secret)
    {
        var request = new HttpRequestMessage(HttpMethod.Post, ValidateRoute);
        if (deviceId is not null)
        {
            request.Headers.Add(DeviceIdHeader, deviceId);
        }

        if (secret is not null)
        {
            request.Headers.Add(DeviceSecretHeader, secret);
        }

        return _client.SendAsync(request);
    }

    // --- Success (FS-02 §10.5) ---

    [Fact]
    public async Task Validate_ValidActiveCredentials_Returns200SuccessWithNoData()
    {
        var token = await LoginAsync();
        var (_, deviceId, secret) = await ActivateNewDeviceAsync(token);

        using var response = await ValidateAsync(deviceId.ToString(), secret);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var body = await response.Content.ReadAsStringAsync();
        using var document = JsonDocument.Parse(body);
        Assert.True(document.RootElement.GetProperty("success").GetBoolean());
        // No key, no secret, no device status, no configuration on the wire.
        Assert.DoesNotContain(secret, body);
        Assert.DoesNotContain("sharedSecret", body, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("activationKey", body, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("activationStatus", body, StringComparison.OrdinalIgnoreCase);
    }

    // --- Every invalid outcome is a uniform 401 (FS-02 §10.5, item 9) ---

    [Fact]
    public async Task Validate_MissingDeviceId_Returns401()
    {
        using var response = await ValidateAsync(deviceId: null, secret: "any-secret-placeholder");
        await AssertUniformRejectionAsync(response);
    }

    [Fact]
    public async Task Validate_MalformedDeviceId_Returns401()
    {
        using var response = await ValidateAsync("not-a-guid", "any-secret-placeholder");
        await AssertUniformRejectionAsync(response);
    }

    [Fact]
    public async Task Validate_MissingSecret_Returns401()
    {
        using var response = await ValidateAsync(Guid.NewGuid().ToString(), secret: null);
        await AssertUniformRejectionAsync(response);
    }

    [Fact]
    public async Task Validate_UnknownDeviceId_Returns401()
    {
        using var response = await ValidateAsync(Guid.NewGuid().ToString(), "any-secret-placeholder");
        await AssertUniformRejectionAsync(response);
    }

    [Fact]
    public async Task Validate_IncorrectSecret_Returns401()
    {
        var token = await LoginAsync();
        var (_, deviceId, _) = await ActivateNewDeviceAsync(token);

        using var response = await ValidateAsync(deviceId.ToString(), "definitely-not-the-real-secret");
        await AssertUniformRejectionAsync(response);
    }

    [Fact]
    public async Task Validate_UnactivatedDevice_HasNoDeviceIdToPresent_AndIsRejected()
    {
        // An unactivated device has no external DeviceId, so it is not addressable by this endpoint;
        // any DeviceId presented resolves to no active device.
        var token = await LoginAsync();
        var (branchId, _) = await CreateBranchAsync(token);

        await using (var db = _factory.CreateDbContext())
        {
            var device = await db.Devices.AsNoTracking().SingleAsync(d => d.BranchId == branchId);
            Assert.Null(device.DeviceId);
        }

        using var response = await ValidateAsync(Guid.NewGuid().ToString(), "any-secret-placeholder");
        await AssertUniformRejectionAsync(response);
    }

    [Fact]
    public async Task Validate_ActivatedDeviceWithInconsistentMissingStoredSecret_Returns401()
    {
        var token = await LoginAsync();
        var (branchId, deviceId, secret) = await ActivateNewDeviceAsync(token);

        // Force the inconsistent state: Activated but no stored secret. The endpoint must reject
        // uniformly rather than fail.
        await using (var db = _factory.CreateDbContext())
        {
            await db.Database.ExecuteSqlRawAsync(
                "UPDATE Devices SET ProtectedSharedSecret = NULL WHERE BranchId = {0}", branchId);
        }

        using var response = await ValidateAsync(deviceId.ToString(), secret);
        await AssertUniformRejectionAsync(response);
    }

    // --- End-to-end revocation (item 10; FS-02 §5.3/§5.8/§10.5) ---

    [Fact]
    public async Task Activate_ValidateSucceeds_Regenerate_ThenPreviousCredentialsAreRejected()
    {
        var token = await LoginAsync();
        var (branchId, deviceId, secret) = await ActivateNewDeviceAsync(token);

        // The just-activated credentials validate successfully.
        using (var ok = await ValidateAsync(deviceId.ToString(), secret))
        {
            Assert.Equal(HttpStatusCode.OK, ok.StatusCode);
        }

        // The Admin regenerates the key: the Backend immediately revokes the secret and moves the
        // device to ReactivationRequired (T-50).
        await RegenerateAsync(token, branchId);

        // Retrying with the previously valid DeviceId + secret is now rejected with the uniform 401 —
        // the revocation is enforced through the real HTTP request path (item 5, item 10).
        using var response = await ValidateAsync(deviceId.ToString(), secret);
        await AssertUniformRejectionAsync(response);
    }

    // --- Uniform status code and body across every invalid outcome, with no leakage (item 9) ---

    [Fact]
    public async Task Validate_EveryInvalidOutcome_ReturnsIdentical401Body_WithNoInternalReasonOrSecret()
    {
        var token = await LoginAsync();
        var (activeBranchId, activeDeviceId, activeSecret) = await ActivateNewDeviceAsync(token, "Branch Active");

        // A device whose credentials were revoked by regeneration (ReactivationRequired).
        var (revokedBranchId, revokedDeviceId, revokedSecret) = await ActivateNewDeviceAsync(token, "Branch Revoked");
        await RegenerateAsync(token, revokedBranchId);

        // A device forced into the inconsistent Activated-with-no-secret state.
        var (inconsistentBranchId, inconsistentDeviceId, inconsistentSecret) =
            await ActivateNewDeviceAsync(token, "Branch Inconsistent");
        await using (var db = _factory.CreateDbContext())
        {
            await db.Database.ExecuteSqlRawAsync(
                "UPDATE Devices SET ProtectedSharedSecret = NULL WHERE BranchId = {0}", inconsistentBranchId);
        }

        var responses = new List<(HttpStatusCode Status, string Body)>();
        async Task Collect(Task<HttpResponseMessage> call)
        {
            using var r = await call;
            responses.Add((r.StatusCode, await r.Content.ReadAsStringAsync()));
        }

        await Collect(ValidateAsync(deviceId: null, secret: "x"));                              // missing DeviceId
        await Collect(ValidateAsync("not-a-guid", "x"));                                        // malformed DeviceId
        await Collect(ValidateAsync(Guid.NewGuid().ToString(), secret: null));                  // missing secret
        await Collect(ValidateAsync(Guid.NewGuid().ToString(), "x"));                           // unknown device
        await Collect(ValidateAsync(activeDeviceId.ToString(), "wrong-secret-guess"));          // incorrect secret
        await Collect(ValidateAsync(revokedDeviceId.ToString(), revokedSecret));                // ReactivationRequired
        await Collect(ValidateAsync(inconsistentDeviceId.ToString(), inconsistentSecret));      // missing stored secret

        // Every invalid outcome: the same 401 and a byte-identical body.
        Assert.All(responses, r => Assert.Equal(HttpStatusCode.Unauthorized, r.Status));
        var canonical = responses[0].Body;
        Assert.All(responses, r => Assert.Equal(canonical, r.Body));

        using var document = JsonDocument.Parse(canonical);
        Assert.False(document.RootElement.GetProperty("success").GetBoolean());
        Assert.Equal("INVALID_DEVICE_CREDENTIALS", document.RootElement.GetProperty("errorCode").GetString());
        Assert.Equal("The device credentials are invalid.", document.RootElement.GetProperty("message").GetString());

        // No internal outcome name, and no secret material, ever reaches the body.
        foreach (var leak in new[]
                 {
                     "ReactivationRequired", "SecretMismatch", "UnknownDevice", "MissingStoredSecret",
                     "MissingDeviceId", "MissingSecret", "DeviceNotActivated", "Outcome",
                 })
        {
            Assert.DoesNotContain(leak, canonical, StringComparison.OrdinalIgnoreCase);
        }

        Assert.DoesNotContain(activeSecret, canonical);
        Assert.DoesNotContain(revokedSecret, canonical);
        Assert.DoesNotContain(inconsistentSecret, canonical);

        // Guard against a nothing-happened false positive.
        Assert.NotEqual(Guid.Empty, activeDeviceId);
    }

    // --- The AllowAnonymous exemption does not leak to other endpoints (FS-02 §11) ---

    [Fact]
    public async Task ProtectedAdminRoutes_RemainProtected_AfterValidateIsAnonymous()
    {
        using var branches = await _client.GetAsync(BranchesRoute);
        Assert.Equal(HttpStatusCode.Unauthorized, branches.StatusCode);
    }

    private static async Task AssertUniformRejectionAsync(HttpResponseMessage response)
    {
        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
        var body = await response.Content.ReadAsStringAsync();
        using var document = JsonDocument.Parse(body);
        Assert.False(document.RootElement.GetProperty("success").GetBoolean());
        Assert.Equal("INVALID_DEVICE_CREDENTIALS", document.RootElement.GetProperty("errorCode").GetString());
        Assert.Equal("The device credentials are invalid.", document.RootElement.GetProperty("message").GetString());
    }
}

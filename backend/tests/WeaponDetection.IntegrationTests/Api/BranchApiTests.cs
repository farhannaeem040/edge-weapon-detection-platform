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
using WeaponDetection.Infrastructure.Security;
using Xunit;

namespace WeaponDetection.IntegrationTests.Api;

// Full HTTP-pipeline integration tests for the branch endpoints (IP-01 T-16), run against a real
// in-process TestServer and a real SQL Server database:
//   POST /api/v1/branches, GET /api/v1/branches, GET /api/v1/branches/{id}
// Covers FS-02 AC-1, AC-2, the §13 error cases, §7 (the plaintext Activation Key and internal
// DeviceRecordId never appear in read responses), and the T-16 RTSP-credential-redaction constraint.
//
// No test prints a plaintext password or a complete access token to console/log output. Where a
// test captures a plaintext Activation Key, it does so only to assert it is ABSENT from later
// responses — never to log it.
//
// Each test method constructs its own host and its own freshly migrated, empty database (rather than
// sharing one via IClassFixture), so absolute assertions like "exactly two branches" or "nothing was
// persisted" are exact. The ApiHostCollection keeps these host instances from overlapping — the host
// factory configures itself through process-wide environment variables, so only one may be alive at
// a time (see SqlServerApiHostFactory).
[Collection(ApiHostCollection.Name)]
public class BranchApiTests : IDisposable
{
    private const string BranchesRoute = "/api/v1/branches";

    private readonly BranchDeviceApiFactory _factory;
    private readonly HttpClient _client;

    public BranchApiTests()
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

    private static object CompleteRequest(
        string name = "Downtown Branch",
        string rtspUrl = "rtsp://camera.example.local:554/stream1") =>
        new
        {
            name,
            address = "1 High Street",
            contactDetails = "ops@example.local",
            cameras = new[] { new { name = "Front Entrance", rtspUrl } },
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

    // Creates a branch as the Dashboard would and returns the parsed create-response data element.
    private async Task<JsonElement> CreateBranchAsync(string token, object? body = null)
    {
        using var response = await SendAsync(HttpMethod.Post, BranchesRoute, token, body ?? CompleteRequest());
        Assert.Equal(HttpStatusCode.Created, response.StatusCode);

        var envelope = await response.Content.ReadFromJsonAsync<ApiEnvelope>();
        return envelope!.Data!.Value.Clone();
    }

    // --- Creation (FS-02 §10.1, AC-1, AC-2) ---

    [Fact]
    public async Task Create_CompleteRequest_Returns201()
    {
        var token = await LoginAsync();

        using var response = await SendAsync(HttpMethod.Post, BranchesRoute, token, CompleteRequest());

        Assert.Equal(HttpStatusCode.Created, response.StatusCode);
    }

    [Fact]
    public async Task Create_CompleteRequest_ReturnsBranchWithCamerasAndAnUnactivatedDevice()
    {
        var token = await LoginAsync();

        var data = await CreateBranchAsync(token);

        Assert.NotEqual(Guid.Empty, data.GetProperty("branchId").GetGuid());
        Assert.Equal("Downtown Branch", data.GetProperty("name").GetString());
        Assert.Equal("1 High Street", data.GetProperty("address").GetString());
        Assert.Equal("ops@example.local", data.GetProperty("contactDetails").GetString());
        Assert.Single(data.GetProperty("cameras").EnumerateArray());

        var device = data.GetProperty("device");
        Assert.Equal("Unactivated", device.GetProperty("activationStatus").GetString());
        // An unactivated device has no external DeviceId, so the field is omitted entirely (FS-02 §1.3).
        Assert.False(device.TryGetProperty("deviceId", out _));
    }

    [Fact]
    public async Task Create_CompleteRequest_DisclosesTheCompleteTwoPartActivationKeyExactlyOnce()
    {
        var token = await LoginAsync();

        var data = await CreateBranchAsync(token);

        var key = data.GetProperty("activationKey").GetString();
        Assert.False(string.IsNullOrWhiteSpace(key));

        // The single-disclosure key is the complete two-part `keyId.secret` (FS-02 §1.4, §5.1 step 6).
        var parts = key!.Split(ActivationKeyGenerator.Delimiter);
        Assert.Equal(2, parts.Length);
        Assert.All(parts, part => Assert.False(string.IsNullOrWhiteSpace(part)));
    }

    [Fact]
    public async Task Create_CompleteRequest_PersistsBranchOneCameraAndOneUnactivatedDeviceWithAnUnconsumedKey()
    {
        var token = await LoginAsync();

        var data = await CreateBranchAsync(token);
        var branchId = data.GetProperty("branchId").GetGuid();

        await using var dbContext = _factory.CreateDbContext();

        var branch = await dbContext.Branches.AsNoTracking().SingleAsync(b => b.BranchId == branchId);
        Assert.Equal("Downtown Branch", branch.Name);

        Assert.Equal(1, await dbContext.Cameras.CountAsync(c => c.BranchId == branchId));

        var device = await dbContext.Devices.AsNoTracking().SingleAsync(d => d.BranchId == branchId);
        Assert.Null(device.DeviceId);
        Assert.Equal(DeviceActivationStatus.Unactivated, device.ActivationStatus);

        var activationKey = await dbContext.ActivationKeys.AsNoTracking()
            .SingleAsync(k => k.DeviceRecordId == device.DeviceRecordId);
        Assert.Equal(ActivationKeyStatus.Unconsumed, activationKey.Status);
    }

    [Fact]
    public async Task Create_EmbeddedRtspCredentials_AreRedactedFromTheResponse()
    {
        var token = await LoginAsync();

        using var response = await SendAsync(
            HttpMethod.Post, BranchesRoute, token,
            CompleteRequest(rtspUrl: "rtsp://admin:s3cr3t@10.0.0.5:554/stream"));
        Assert.Equal(HttpStatusCode.Created, response.StatusCode);

        var body = await response.Content.ReadAsStringAsync();

        // The credential material must not leave the Backend on the wire (ARCH-001 §15.6); the
        // host/port/path survive so the camera is still recognisable.
        Assert.DoesNotContain("admin:s3cr3t", body);
        Assert.DoesNotContain("s3cr3t@", body);

        using var document = JsonDocument.Parse(body);
        var rtspUrl = document.RootElement
            .GetProperty("data").GetProperty("cameras")[0].GetProperty("rtspUrl").GetString();
        Assert.Equal("rtsp://***@10.0.0.5:554/stream", rtspUrl);
    }

    // --- Creation rejection (FS-02 §13) ---

    [Fact]
    public async Task Create_MissingRequiredField_Returns400()
    {
        var token = await LoginAsync();
        var incomplete = new
        {
            // name omitted
            address = "1 High Street",
            contactDetails = "ops@example.local",
            cameras = new[] { new { name = "Front Entrance", rtspUrl = "rtsp://camera.example.local/stream" } },
        };

        using var response = await SendAsync(HttpMethod.Post, BranchesRoute, token, incomplete);

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        await AssertNothingPersistedAsync();
    }

    [Fact]
    public async Task Create_NoCameras_Returns400()
    {
        var token = await LoginAsync();
        var noCameras = new
        {
            name = "Downtown Branch",
            address = "1 High Street",
            contactDetails = "ops@example.local",
            cameras = Array.Empty<object>(),
        };

        using var response = await SendAsync(HttpMethod.Post, BranchesRoute, token, noCameras);

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        await AssertNothingPersistedAsync();
    }

    [Fact]
    public async Task Create_WellFormedButNonRtspUrl_Returns400()
    {
        var token = await LoginAsync();

        // Passes DTO validation (present, within length) but fails the Application-layer RTSP format
        // check, which the controller turns into a 400 (IP-01 §11).
        using var response = await SendAsync(
            HttpMethod.Post, BranchesRoute, token,
            CompleteRequest(rtspUrl: "http://camera.example.local/stream"));

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        await AssertNothingPersistedAsync();
    }

    [Fact]
    public async Task Create_NoAuthorizationHeader_Returns401AndCreatesNothing()
    {
        using var response = await SendAsync(HttpMethod.Post, BranchesRoute, token: null, CompleteRequest());

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
        await AssertNothingPersistedAsync();
    }

    [Fact]
    public async Task Create_WithOnlyADeviceSecretHeaderAndNoJwt_Returns401AndCreatesNothing()
    {
        // FS-02 §13 / AC-6: a caller presenting only a device-style secret (as a future Agent might)
        // and no Admin session cannot create a branch — the fallback policy accepts only a valid JWT.
        var request = new HttpRequestMessage(HttpMethod.Post, BranchesRoute)
        {
            Content = JsonContent.Create(CompleteRequest()),
        };
        request.Headers.Add("X-Device-Secret", "not-a-real-secret");

        using var response = await _client.SendAsync(request);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
        await AssertNothingPersistedAsync();
    }

    // --- List (FS-02 §10.3, §5.4) ---

    [Fact]
    public async Task List_NoAuthorizationHeader_Returns401()
    {
        using var response = await SendAsync(HttpMethod.Get, BranchesRoute, token: null);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task List_ReturnsEveryCreatedBranchWithItsUnactivatedDevice()
    {
        var token = await LoginAsync();
        await CreateBranchAsync(token, CompleteRequest(name: "Branch A"));
        await CreateBranchAsync(token, CompleteRequest(name: "Branch B"));

        using var response = await SendAsync(HttpMethod.Get, BranchesRoute, token);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);

        var envelope = await response.Content.ReadFromJsonAsync<ApiEnvelope>();
        var branches = envelope!.Data!.Value.EnumerateArray().ToList();

        Assert.Equal(2, branches.Count);
        Assert.Contains(branches, b => b.GetProperty("name").GetString() == "Branch A");
        Assert.Contains(branches, b => b.GetProperty("name").GetString() == "Branch B");
        Assert.All(branches, b =>
            Assert.Equal("Unactivated", b.GetProperty("device").GetProperty("activationStatus").GetString()));
    }

    [Fact]
    public async Task List_NeverExposesTheActivationKeyOrInternalIdentifiers()
    {
        var token = await LoginAsync();
        var created = await CreateBranchAsync(token);
        var disclosedKey = created.GetProperty("activationKey").GetString()!;

        using var response = await SendAsync(HttpMethod.Get, BranchesRoute, token);
        var body = await response.Content.ReadAsStringAsync();

        AssertNoSecretsOrInternalIds(body, disclosedKey);
    }

    // --- Detail (FS-02 §10.3) ---

    [Fact]
    public async Task GetById_NoAuthorizationHeader_Returns401()
    {
        using var response = await SendAsync(HttpMethod.Get, $"{BranchesRoute}/{Guid.NewGuid()}", token: null);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task GetById_ExistingBranch_Returns200WithTheBranch()
    {
        var token = await LoginAsync();
        var created = await CreateBranchAsync(token);
        var branchId = created.GetProperty("branchId").GetGuid();

        using var response = await SendAsync(HttpMethod.Get, $"{BranchesRoute}/{branchId}", token);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);

        var envelope = await response.Content.ReadFromJsonAsync<ApiEnvelope>();
        var data = envelope!.Data!.Value;
        Assert.Equal(branchId, data.GetProperty("branchId").GetGuid());
        Assert.Equal("Unactivated", data.GetProperty("device").GetProperty("activationStatus").GetString());
    }

    [Fact]
    public async Task GetById_UnknownBranch_Returns404()
    {
        var token = await LoginAsync();

        using var response = await SendAsync(HttpMethod.Get, $"{BranchesRoute}/{Guid.NewGuid()}", token);

        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);

        var envelope = await response.Content.ReadFromJsonAsync<ApiEnvelope>();
        Assert.False(envelope!.Success);
        Assert.Equal("NOT_FOUND", envelope.ErrorCode);
    }

    [Fact]
    public async Task GetById_NeverExposesTheActivationKeyOrInternalIdentifiers()
    {
        var token = await LoginAsync();
        var created = await CreateBranchAsync(token);
        var branchId = created.GetProperty("branchId").GetGuid();
        var disclosedKey = created.GetProperty("activationKey").GetString()!;

        using var response = await SendAsync(HttpMethod.Get, $"{BranchesRoute}/{branchId}", token);
        var body = await response.Content.ReadAsStringAsync();

        AssertNoSecretsOrInternalIds(body, disclosedKey);
    }

    // --- Shared assertions ---

    // A read response must never carry the plaintext Activation Key, the internal DeviceRecordId, or
    // any stored secret material (FS-02 §1.3, §7, §11).
    private static void AssertNoSecretsOrInternalIds(string body, string disclosedActivationKey)
    {
        Assert.DoesNotContain(disclosedActivationKey, body);
        Assert.DoesNotContain("activationKey", body, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("deviceRecordId", body, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("secretHash", body, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("protectedSharedSecret", body, StringComparison.OrdinalIgnoreCase);
    }

    private async Task AssertNothingPersistedAsync()
    {
        await using var dbContext = _factory.CreateDbContext();
        Assert.Equal(0, await dbContext.Branches.CountAsync());
        Assert.Equal(0, await dbContext.Cameras.CountAsync());
        Assert.Equal(0, await dbContext.Devices.CountAsync());
        Assert.Equal(0, await dbContext.ActivationKeys.CountAsync());
    }
}

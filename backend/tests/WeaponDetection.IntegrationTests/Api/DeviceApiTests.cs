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

// Full HTTP-pipeline integration tests for GET /api/v1/devices/{id} (IP-01 T-16), run against a real
// in-process TestServer and a real SQL Server database. Covers FS-02 §10.3 for the device read path
// and the §1.3 identity model: a device is addressable only by its external DeviceId (assigned on
// activation), never by the internal DeviceRecordId, and an unactivated device — which has no
// DeviceId — is not addressable here at all.
//
// Device activation (POST /api/v1/activate) is out of this task's scope (T-19/T-20), so the one test
// that needs an *activated* device sets that state directly through the Device domain entity's
// Activate method as test data — exactly the kind of "created in a prior feature's test data"
// correlation FS-02 §15 T-15 anticipates — rather than driving an activation endpoint that does not
// exist yet. No plaintext secret is logged; the protected secret value is a fixed test placeholder.
//
// Each test method constructs its own host and freshly migrated database (see BranchApiTests for the
// rationale); the ApiHostCollection serialises them.
[Collection(ApiHostCollection.Name)]
public class DeviceApiTests : IDisposable
{
    private const string DevicesRoute = "/api/v1/devices";
    private const string BranchesRoute = "/api/v1/branches";

    private readonly BranchDeviceApiFactory _factory;
    private readonly HttpClient _client;

    public DeviceApiTests()
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

    private Task<HttpResponseMessage> GetDeviceAsync(string route, string? token)
    {
        var request = new HttpRequestMessage(HttpMethod.Get, route);

        if (token is not null)
        {
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        }

        return _client.SendAsync(request);
    }

    // Creates a branch (with its reserved, unactivated device) as the Dashboard would, returning the
    // new branch id.
    private async Task<Guid> CreateBranchAsync(string token)
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
        return envelope!.Data!.Value.GetProperty("branchId").GetGuid();
    }

    // Activates the branch's reserved device directly through the domain entity (see class remarks),
    // returning the assigned external DeviceId.
    private async Task<Guid> ActivateDeviceOfBranchAsync(Guid branchId)
    {
        await using var dbContext = _factory.CreateDbContext();
        var device = await dbContext.Devices.SingleAsync(d => d.BranchId == branchId);

        device.Activate("protected-test-secret");
        await dbContext.SaveChangesAsync();

        return device.DeviceId!.Value;
    }

    // --- Authentication (FS-02 §10.3 "Valid Admin JWT + active session") ---

    [Fact]
    public async Task GetById_NoAuthorizationHeader_Returns401()
    {
        using var response = await GetDeviceAsync($"{DevicesRoute}/{Guid.NewGuid()}", token: null);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    // --- Not found (FS-02 §10.3) ---

    [Fact]
    public async Task GetById_UnknownDeviceId_Returns404()
    {
        var token = await LoginAsync();

        using var response = await GetDeviceAsync($"{DevicesRoute}/{Guid.NewGuid()}", token);

        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);

        var envelope = await response.Content.ReadFromJsonAsync<ApiEnvelope>();
        Assert.False(envelope!.Success);
        Assert.Equal("NOT_FOUND", envelope.ErrorCode);
    }

    [Fact]
    public async Task GetById_UnactivatedDevice_IsNotAddressableAndYields404()
    {
        // A freshly created branch has a device with a NULL external DeviceId (FS-02 §1.3). It is
        // therefore not addressable on /devices/{id}: there is no external id to present, and the
        // internal DeviceRecordId — the only id it does have — is never exposed and, if guessed,
        // does not match the DeviceId lookup key. Presenting that internal id must not return it.
        var token = await LoginAsync();
        var branchId = await CreateBranchAsync(token);

        Guid deviceRecordId;
        await using (var dbContext = _factory.CreateDbContext())
        {
            var device = await dbContext.Devices.AsNoTracking().SingleAsync(d => d.BranchId == branchId);
            Assert.Null(device.DeviceId);
            deviceRecordId = device.DeviceRecordId;
        }

        using var response = await GetDeviceAsync($"{DevicesRoute}/{deviceRecordId}", token);

        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
    }

    // --- Found, once activated (FS-02 §10.3, §1.3) ---

    [Fact]
    public async Task GetById_ActivatedDevice_Returns200WithDeviceIdBranchIdAndStatus()
    {
        var token = await LoginAsync();
        var branchId = await CreateBranchAsync(token);
        var deviceId = await ActivateDeviceOfBranchAsync(branchId);

        using var response = await GetDeviceAsync($"{DevicesRoute}/{deviceId}", token);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);

        var envelope = await response.Content.ReadFromJsonAsync<ApiEnvelope>();
        var data = envelope!.Data!.Value;

        Assert.Equal(deviceId, data.GetProperty("deviceId").GetGuid());
        Assert.Equal(branchId, data.GetProperty("branchId").GetGuid());
        Assert.Equal("Activated", data.GetProperty("activationStatus").GetString());
    }

    [Fact]
    public async Task GetById_ActivatedDevice_NeverExposesInternalIdOrProtectedSecret()
    {
        var token = await LoginAsync();
        var branchId = await CreateBranchAsync(token);
        var deviceId = await ActivateDeviceOfBranchAsync(branchId);

        using var response = await GetDeviceAsync($"{DevicesRoute}/{deviceId}", token);
        var body = await response.Content.ReadAsStringAsync();

        // The internal DeviceRecordId and the protected shared secret are never on the wire
        // (FS-02 §1.3, §11).
        Assert.DoesNotContain("deviceRecordId", body, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("protectedSharedSecret", body, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("protected-test-secret", body);
    }
}

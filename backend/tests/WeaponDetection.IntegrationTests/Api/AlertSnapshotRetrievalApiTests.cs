using System;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using System.Threading.Tasks;
using Xunit;

namespace WeaponDetection.IntegrationTests.Api;

// Full HTTP-pipeline integration tests for GET /api/v1/alerts/{alertId}/snapshot (FS-08 §12, IP-10
// T-161), against a real in-process TestServer, real SQL Server, and a real temp-directory
// IAlertSnapshotStorage — the same storage location the existing POST upload path (FS-08 §9) writes
// through, proven by uploading a real snapshot via that production endpoint and reading it back
// through this one.
[Collection(ApiHostCollection.Name)]
public class AlertSnapshotRetrievalApiTests : IDisposable
{
    private const string DeviceIdHeader = "X-Device-Id";
    private const string DeviceSecretHeader = "X-Device-Secret";

    private readonly AlertSnapshotRetrievalApiFactory _factory;
    private readonly HttpClient _client;

    public AlertSnapshotRetrievalApiTests()
    {
        _factory = new AlertSnapshotRetrievalApiFactory();
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

    private async Task<string> CreateBranchAsync(string token, string name)
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
        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        return data.GetProperty("activationKey").GetString()!;
    }

    private async Task<(Guid DeviceId, string Secret)> ActivateDeviceAsync(string activationKey)
    {
        using var response = await _client.PostAsJsonAsync("/api/v1/activate", new { activationKey });
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        return (data.GetProperty("deviceId").GetGuid(), data.GetProperty("sharedSecret").GetString()!);
    }

    private async Task<Guid> SyncOneEventAsync(Guid deviceId, string secret, Guid eventId)
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
                        eventId,
                        cameraId = "camera1",
                        detectedAtUtc = DateTime.UtcNow,
                        createdAtUtc = DateTime.UtcNow,
                        classId = 0,
                        className = "gun",
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
        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        return data.GetProperty("results")[0].GetProperty("alertId").GetGuid();
    }

    // A minimal, structurally valid JPEG (SOI + SOF0 + EOI) — matches AlertSnapshotUploadApiTests'
    // own fixture, sufficient for the Backend's own JpegInspector validation on the upload side.
    private static readonly byte[] JpegBytes =
    [
        0xFF, 0xD8, 0xFF, 0xC0, 0x00, 0x0B, 0x08,
        0x02, 0xD0, 0x05, 0x00, 0x01, 0x01, 0x11, 0x00,
        0x01,
        0xFF, 0xD9,
    ];

    private async Task UploadSnapshotAsync(Guid alertId, Guid deviceId, string secret, Guid eventId)
    {
        var request = new HttpRequestMessage(HttpMethod.Post, $"/api/v1/alerts/{alertId}/snapshot")
        {
            Headers = { { DeviceIdHeader, deviceId.ToString() }, { DeviceSecretHeader, secret } },
        };
        var form = new MultipartFormDataContent();
        var fileContent = new ByteArrayContent(JpegBytes);
        fileContent.Headers.ContentType = new MediaTypeHeaderValue("image/jpeg");
        form.Add(fileContent, "file", "snapshot.jpg");
        form.Add(new StringContent(eventId.ToString()), "eventId");
        request.Content = form;

        using var response = await _client.SendAsync(request);
        Assert.Equal(HttpStatusCode.Created, response.StatusCode);
    }

    private async Task<(Guid AlertId, Guid DeviceId, string Secret)> SeedAlertWithSnapshotAsync(
        string token, string branchName)
    {
        var key = await CreateBranchAsync(token, branchName);
        var (deviceId, secret) = await ActivateDeviceAsync(key);
        var eventId = Guid.NewGuid();
        var alertId = await SyncOneEventAsync(deviceId, secret, eventId);
        await UploadSnapshotAsync(alertId, deviceId, secret, eventId);
        return (alertId, deviceId, secret);
    }

    // --- Happy path (FS-08 §12) ---

    [Fact]
    public async Task GetSnapshot_KnownAlertWithSnapshot_ReturnsJpegBytes()
    {
        var token = await LoginAsync();
        var (alertId, _, _) = await SeedAlertWithSnapshotAsync(token, "Retrieval Branch " + Guid.NewGuid());

        using var response = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Get, $"/api/v1/alerts/{alertId}/snapshot")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
        });

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Equal("image/jpeg", response.Content.Headers.ContentType?.MediaType);
        var bytes = await response.Content.ReadAsByteArrayAsync();
        Assert.Equal(JpegBytes, bytes);
    }

    [Fact]
    public async Task GetSnapshot_ResponseNeverContainsAFilesystemPath()
    {
        var token = await LoginAsync();
        var (alertId, _, _) = await SeedAlertWithSnapshotAsync(token, "Path Leak Branch " + Guid.NewGuid());

        using var response = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Get, $"/api/v1/alerts/{alertId}/snapshot")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
        });

        var raw = await response.Content.ReadAsStringAsync();
        Assert.DoesNotContain(":\\", raw);
        Assert.DoesNotContain("/tmp/", raw);
        Assert.DoesNotContain("/var/", raw);
    }

    // --- Authentication ---

    [Fact]
    public async Task GetSnapshot_WithoutAuthentication_Returns401()
    {
        using var response = await _client.GetAsync($"/api/v1/alerts/{Guid.NewGuid()}/snapshot");

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task GetSnapshot_WithDeviceCredentials_Returns401()
    {
        var token = await LoginAsync();
        var (alertId, deviceId, secret) =
            await SeedAlertWithSnapshotAsync(token, "Device Cred Branch " + Guid.NewGuid());

        var request = new HttpRequestMessage(HttpMethod.Get, $"/api/v1/alerts/{alertId}/snapshot");
        request.Headers.Add(DeviceIdHeader, deviceId.ToString());
        request.Headers.Add(DeviceSecretHeader, secret);
        using var response = await _client.SendAsync(request);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    // --- 404s (missing snapshot, missing Alert — never a 500) ---

    [Fact]
    public async Task GetSnapshot_AlertWithoutSnapshot_Returns404()
    {
        var token = await LoginAsync();
        var key = await CreateBranchAsync(token, "No Snapshot Branch " + Guid.NewGuid());
        var (deviceId, secret) = await ActivateDeviceAsync(key);
        var alertId = await SyncOneEventAsync(deviceId, secret, Guid.NewGuid());

        using var response = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Get, $"/api/v1/alerts/{alertId}/snapshot")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
        });

        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
        var envelope = await response.Content.ReadFromJsonAsync<ApiEnvelope>();
        Assert.Equal("NOT_FOUND", envelope!.ErrorCode);
    }

    [Fact]
    public async Task GetSnapshot_UnknownAlert_Returns404()
    {
        var token = await LoginAsync();

        using var response = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Get, $"/api/v1/alerts/{Guid.NewGuid()}/snapshot")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
        });

        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
        var envelope = await response.Content.ReadFromJsonAsync<ApiEnvelope>();
        Assert.Equal("NOT_FOUND", envelope!.ErrorCode);
    }

    // --- Cache-Control (FS-08 §12: never cached) ---

    [Fact]
    public async Task GetSnapshot_Success_SetsNoStorePrivateCacheControl()
    {
        var token = await LoginAsync();
        var (alertId, _, _) = await SeedAlertWithSnapshotAsync(token, "Cache Header Branch " + Guid.NewGuid());

        using var response = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Get, $"/api/v1/alerts/{alertId}/snapshot")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
        });

        Assert.True(response.Headers.CacheControl?.NoStore);
        Assert.True(response.Headers.CacheControl?.Private);
    }
}

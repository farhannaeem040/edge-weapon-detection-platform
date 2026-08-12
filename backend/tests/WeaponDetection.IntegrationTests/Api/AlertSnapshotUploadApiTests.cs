using System;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using System.Threading.Tasks;
using Xunit;

namespace WeaponDetection.IntegrationTests.Api;

// Full HTTP-pipeline integration tests for POST /api/v1/alerts/{alertId}/snapshot (FS-08 §9, IP-10
// T-154/T-156), against a real in-process TestServer, real SQL Server, and a real temp-directory
// IAlertSnapshotStorage (wired through AlertSnapshotUploadApiFactory -> SqlServerApiHostFactory's
// AlertSnapshots:StoragePath environment variable). A plain HttpClient with no Authorization header
// stands in for the Agent's SnapshotUploadWorker — this endpoint is device-authenticated by
// X-Device-Id/X-Device-Secret, not an Admin session, exactly like /api/v1/sync/events.
[Collection(ApiHostCollection.Name)]
public class AlertSnapshotUploadApiTests : IDisposable
{
    private const string DeviceIdHeader = "X-Device-Id";
    private const string DeviceSecretHeader = "X-Device-Secret";

    private readonly AlertSnapshotUploadApiFactory _factory;
    private readonly HttpClient _client;

    public AlertSnapshotUploadApiTests()
    {
        _factory = new AlertSnapshotUploadApiFactory();
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
        using var response = await _client.PostAsJsonAsync(
            "/api/v1/activate", new { activationKey });
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        return (data.GetProperty("deviceId").GetGuid(), data.GetProperty("sharedSecret").GetString()!);
    }

    // Syncs one detection event through the real (already shipped, IP-08) endpoint to create a real
    // Alert row this test can then upload a snapshot for — deliberately reusing the production path
    // rather than reaching into the database directly, so this test proves the two features compose.
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

    // A minimal, structurally valid JPEG (SOI + SOF0 + EOI) — see JpegInspectorTests for why this is
    // sufficient for the Backend's own validation, which never fully decodes pixel data.
    private static byte[] BuildJpeg(byte marker = 1) =>
    [
        0xFF, 0xD8, 0xFF, 0xC0, 0x00, 0x0B, 0x08,
        0x02, 0xD0, 0x05, 0x00, 0x01, 0x01, 0x11, 0x00,
        marker,
        0xFF, 0xD9,
    ];

    private static HttpRequestMessage BuildUploadRequest(
        Guid alertId, Guid? deviceId, string? secret, Guid eventId, byte[] content, string contentType = "image/jpeg")
    {
        var request = new HttpRequestMessage(HttpMethod.Post, $"/api/v1/alerts/{alertId}/snapshot");
        if (deviceId is not null)
        {
            request.Headers.Add(DeviceIdHeader, deviceId.Value.ToString());
        }

        if (secret is not null)
        {
            request.Headers.Add(DeviceSecretHeader, secret);
        }

        var form = new MultipartFormDataContent();
        var fileContent = new ByteArrayContent(content);
        fileContent.Headers.ContentType = new MediaTypeHeaderValue(contentType);
        form.Add(fileContent, "file", "snapshot.jpg");
        form.Add(new StringContent(eventId.ToString()), "eventId");

        request.Content = form;
        return request;
    }

    // --- Happy path (FS-08 §9) ---

    [Fact]
    public async Task UploadSnapshot_ValidUpload_Returns201_WithSnapshotReference()
    {
        var token = await LoginAsync();
        var key = await CreateBranchAsync(token, "Branch " + Guid.NewGuid());
        var (deviceId, secret) = await ActivateDeviceAsync(key);
        var eventId = Guid.NewGuid();
        var alertId = await SyncOneEventAsync(deviceId, secret, eventId);

        using var response = await _client.SendAsync(
            BuildUploadRequest(alertId, deviceId, secret, eventId, BuildJpeg()));

        Assert.Equal(HttpStatusCode.Created, response.StatusCode);
        var body = await response.Content.ReadAsStringAsync();
        using var document = JsonDocument.Parse(body);
        Assert.True(document.RootElement.GetProperty("success").GetBoolean());
        var data = document.RootElement.GetProperty("data");
        Assert.Equal("accepted", data.GetProperty("outcome").GetString());
        Assert.False(string.IsNullOrEmpty(data.GetProperty("snapshotReference").GetString()));
        // Never a filesystem path (FS-08 §8/§9).
        Assert.DoesNotContain(":\\", body);
        Assert.DoesNotContain("/tmp/", body);
    }

    [Fact]
    public async Task UploadSnapshot_SameBytesRetried_Returns200Duplicate()
    {
        var token = await LoginAsync();
        var key = await CreateBranchAsync(token, "Branch " + Guid.NewGuid());
        var (deviceId, secret) = await ActivateDeviceAsync(key);
        var eventId = Guid.NewGuid();
        var alertId = await SyncOneEventAsync(deviceId, secret, eventId);
        var content = BuildJpeg();

        using (var first = await _client.SendAsync(BuildUploadRequest(alertId, deviceId, secret, eventId, content)))
        {
            Assert.Equal(HttpStatusCode.Created, first.StatusCode);
        }

        using var second = await _client.SendAsync(BuildUploadRequest(alertId, deviceId, secret, eventId, content));

        Assert.Equal(HttpStatusCode.OK, second.StatusCode);
        var body = await second.Content.ReadAsStringAsync();
        using var document = JsonDocument.Parse(body);
        Assert.Equal("duplicate", document.RootElement.GetProperty("data").GetProperty("outcome").GetString());
    }

    [Fact]
    public async Task UploadSnapshot_DifferentBytesForTheSameAlert_Returns409Conflict()
    {
        var token = await LoginAsync();
        var key = await CreateBranchAsync(token, "Branch " + Guid.NewGuid());
        var (deviceId, secret) = await ActivateDeviceAsync(key);
        var eventId = Guid.NewGuid();
        var alertId = await SyncOneEventAsync(deviceId, secret, eventId);

        using (var first = await _client.SendAsync(
            BuildUploadRequest(alertId, deviceId, secret, eventId, BuildJpeg(marker: 1))))
        {
            Assert.Equal(HttpStatusCode.Created, first.StatusCode);
        }

        using var second = await _client.SendAsync(
            BuildUploadRequest(alertId, deviceId, secret, eventId, BuildJpeg(marker: 2)));

        Assert.Equal(HttpStatusCode.Conflict, second.StatusCode);
    }

    // --- Authentication (byte-identical to every other device-authenticated endpoint) ---

    [Fact]
    public async Task UploadSnapshot_WrongSecret_Returns401UniformFailure()
    {
        var token = await LoginAsync();
        var key = await CreateBranchAsync(token, "Branch " + Guid.NewGuid());
        var (deviceId, _) = await ActivateDeviceAsync(key);

        using var response = await _client.SendAsync(
            BuildUploadRequest(Guid.NewGuid(), deviceId, "definitely-wrong-secret", Guid.NewGuid(), BuildJpeg()));

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
        var body = await response.Content.ReadAsStringAsync();
        using var document = JsonDocument.Parse(body);
        Assert.Equal("INVALID_DEVICE_CREDENTIALS", document.RootElement.GetProperty("errorCode").GetString());
    }

    // --- Ownership / non-disclosure ---

    [Fact]
    public async Task UploadSnapshot_AlertOwnedByAnotherDevice_Returns404()
    {
        var token = await LoginAsync();
        var keyA = await CreateBranchAsync(token, "Branch A " + Guid.NewGuid());
        var (deviceA, secretA) = await ActivateDeviceAsync(keyA);
        var eventId = Guid.NewGuid();
        var alertId = await SyncOneEventAsync(deviceA, secretA, eventId);

        var keyB = await CreateBranchAsync(token, "Branch B " + Guid.NewGuid());
        var (deviceB, secretB) = await ActivateDeviceAsync(keyB);

        using var response = await _client.SendAsync(
            BuildUploadRequest(alertId, deviceB, secretB, eventId, BuildJpeg()));

        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
    }

    [Fact]
    public async Task UploadSnapshot_UnsupportedContentType_Returns415()
    {
        var token = await LoginAsync();
        var key = await CreateBranchAsync(token, "Branch " + Guid.NewGuid());
        var (deviceId, secret) = await ActivateDeviceAsync(key);
        var eventId = Guid.NewGuid();
        var alertId = await SyncOneEventAsync(deviceId, secret, eventId);

        using var response = await _client.SendAsync(
            BuildUploadRequest(alertId, deviceId, secret, eventId, BuildJpeg(), contentType: "image/png"));

        Assert.Equal(HttpStatusCode.UnsupportedMediaType, response.StatusCode);
    }
}

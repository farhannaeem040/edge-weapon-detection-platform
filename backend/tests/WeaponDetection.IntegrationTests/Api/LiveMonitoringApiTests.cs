using System;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using System.Threading.Tasks;
using Xunit;

namespace WeaponDetection.IntegrationTests.Api;

// Full HTTP-pipeline integration tests for GET /api/v1/branches/{branchId}/live-monitoring/cameras
// and POST /api/v1/live-streams (FS-14 §5, IP-16 T-9), against a real in-process TestServer, real
// SQL Server, and a recording stub media-gateway client (LiveMonitoringApiFactory) standing in for
// MediaMTX — there is no real gateway in the test environment, but the stub still proves the
// server-side-only source resolution the SSRF-protection contract depends on.
[Collection(ApiHostCollection.Name)]
public class LiveMonitoringApiTests : IDisposable
{
    private readonly LiveMonitoringApiFactory _factory;
    private readonly HttpClient _client;

    public LiveMonitoringApiTests()
    {
        _factory = new LiveMonitoringApiFactory();
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

    private async Task<(Guid BranchId, Guid CameraId, string RtspUrl)> CreateBranchWithCameraAsync(
        string token, string name, bool withDeviceNetwork)
    {
        // CreateBranchRequestDto.JetsonHost is required (non-nullable) — every branch is created
        // with a configured Device network. A "no network configured" Device is reached by clearing
        // it afterward through the existing PUT /api/v1/devices/{branchId}/network endpoint
        // (SetNetworkConfiguration, DeviceController), the same clear-by-null-host path the real
        // Admin UI already uses.
        var rtspUrl = $"rtsp://user:secret-password@camera.example.local:554/{Guid.NewGuid():N}";
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
                cameras = new[] { new { name = "Front Camera", rtspUrl, cameraKey = $"cam-{Guid.NewGuid():N}" } },
            }),
        });
        Assert.Equal(HttpStatusCode.Created, response.StatusCode);
        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        var branchId = data.GetProperty("branchId").GetGuid();
        var cameraId = data.GetProperty("cameras")[0].GetProperty("cameraId").GetGuid();

        if (!withDeviceNetwork)
        {
            using var clearResponse = await _client.SendAsync(new HttpRequestMessage(
                HttpMethod.Put, $"/api/v1/devices/{branchId}/network")
            {
                Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
                Content = JsonContent.Create(new { jetsonHost = (string?)null, rtspOutputPort = (int?)null }),
            });
            Assert.Equal(HttpStatusCode.OK, clearResponse.StatusCode);
        }

        return (branchId, cameraId, rtspUrl);
    }

    // --- Camera listing ---

    [Fact]
    public async Task ListCameras_WithoutAuthentication_Returns401()
    {
        using var response = await _client.GetAsync($"/api/v1/branches/{Guid.NewGuid()}/live-monitoring/cameras");

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task ListCameras_ReturnsOnlyThisBranchsCameras()
    {
        var token = await LoginAsync();
        var (branchId, cameraId, _) =
            await CreateBranchWithCameraAsync(token, "List Branch " + Guid.NewGuid(), withDeviceNetwork: true);

        using var response = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Get, $"/api/v1/branches/{branchId}/live-monitoring/cameras")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
        });

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        Assert.Equal(1, data.GetArrayLength());
        var camera = data[0];
        Assert.Equal(cameraId, camera.GetProperty("cameraId").GetGuid());
        Assert.True(camera.GetProperty("monitoringAvailable").GetBoolean());
        Assert.True(camera.GetProperty("inferenceAvailable").GetBoolean());
    }

    [Fact]
    public async Task ListCameras_DeviceWithoutNetworkConfig_InferenceUnavailable()
    {
        var token = await LoginAsync();
        var (branchId, _, _) =
            await CreateBranchWithCameraAsync(token, "No Network Branch " + Guid.NewGuid(), withDeviceNetwork: false);

        using var response = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Get, $"/api/v1/branches/{branchId}/live-monitoring/cameras")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
        });

        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        Assert.False(data[0].GetProperty("inferenceAvailable").GetBoolean());
        Assert.True(data[0].GetProperty("monitoringAvailable").GetBoolean());
    }

    [Fact]
    public async Task ListCameras_ResponseNeverContainsRtspUrlOrCameraKeyAsSelectionField()
    {
        var token = await LoginAsync();
        var (branchId, _, rtspUrl) =
            await CreateBranchWithCameraAsync(token, "No Leak Branch " + Guid.NewGuid(), withDeviceNetwork: true);

        using var response = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Get, $"/api/v1/branches/{branchId}/live-monitoring/cameras")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
        });

        var raw = await response.Content.ReadAsStringAsync();
        Assert.DoesNotContain(rtspUrl, raw);
        Assert.DoesNotContain("secret-password", raw);
    }

    // --- Stream creation ---

    [Fact]
    public async Task CreateStream_WithoutAuthentication_Returns401()
    {
        using var response = await _client.PostAsJsonAsync("/api/v1/live-streams", new
        {
            branchId = Guid.NewGuid(), cameraId = Guid.NewGuid(), mode = "monitoring",
        });

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task CreateStream_UnknownCamera_Returns404()
    {
        var token = await LoginAsync();

        using var response = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Post, "/api/v1/live-streams")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
            Content = JsonContent.Create(new
            {
                branchId = Guid.NewGuid(), cameraId = Guid.NewGuid(), mode = "monitoring",
            }),
        });

        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
        var envelope = await response.Content.ReadFromJsonAsync<ApiEnvelope>();
        Assert.Equal("NOT_FOUND", envelope!.ErrorCode);
    }

    [Fact]
    public async Task CreateStream_CameraBelongsToAnotherBranch_Returns404()
    {
        var token = await LoginAsync();
        var (branchA, _, _) = await CreateBranchWithCameraAsync(token, "Branch A " + Guid.NewGuid(), true);
        var (_, cameraB, _) = await CreateBranchWithCameraAsync(token, "Branch B " + Guid.NewGuid(), true);

        using var response = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Post, "/api/v1/live-streams")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
            Content = JsonContent.Create(new { branchId = branchA, cameraId = cameraB, mode = "monitoring" }),
        });

        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
    }

    [Fact]
    public async Task CreateStream_InvalidMode_Returns400()
    {
        var token = await LoginAsync();
        var (branchId, cameraId, _) = await CreateBranchWithCameraAsync(token, "Mode Branch " + Guid.NewGuid(), true);

        using var response = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Post, "/api/v1/live-streams")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
            Content = JsonContent.Create(new { branchId, cameraId, mode = "recording" }),
        });

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    [Fact]
    public async Task CreateStream_Monitoring_ResolvesCameraRtspUrlServerSideOnly()
    {
        var token = await LoginAsync();
        var (branchId, cameraId, rtspUrl) =
            await CreateBranchWithCameraAsync(token, "Monitoring Branch " + Guid.NewGuid(), true);

        using var response = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Post, "/api/v1/live-streams")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
            Content = JsonContent.Create(new { branchId, cameraId, mode = "monitoring" }),
        });

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var raw = await response.Content.ReadAsStringAsync();
        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        Assert.False(string.IsNullOrEmpty(data.GetProperty("playbackUrl").GetString()));
        Assert.StartsWith("/media/", data.GetProperty("playbackUrl").GetString());

        // The response never carries the raw RTSP URL or its embedded credential...
        Assert.DoesNotContain(rtspUrl, raw);
        Assert.DoesNotContain("secret-password", raw);
        // ...but the gateway (stub) really did receive the correct real source, proving server-side
        // resolution actually happened rather than silently no-oping.
        Assert.Contains(_factory.GatewayClient.Calls, call => call.SourceUrl == rtspUrl);
    }

    [Fact]
    public async Task CreateStream_Inference_ResolvesJetsonHostPortCameraKey()
    {
        var token = await LoginAsync();
        var (branchId, cameraId, _) =
            await CreateBranchWithCameraAsync(token, "Inference Branch " + Guid.NewGuid(), true);

        using var response = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Post, "/api/v1/live-streams")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
            Content = JsonContent.Create(new { branchId, cameraId, mode = "inference" }),
        });

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Contains(
            _factory.GatewayClient.Calls,
            call => call.SourceUrl.StartsWith("rtsp://100.98.226.80:8554/cameras/", StringComparison.Ordinal));
    }

    [Fact]
    public async Task CreateStream_InferenceWithoutDeviceNetwork_ReturnsDeviceUnavailable()
    {
        var token = await LoginAsync();
        var (branchId, cameraId, _) =
            await CreateBranchWithCameraAsync(token, "No Device Network Branch " + Guid.NewGuid(), withDeviceNetwork: false);

        using var response = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Post, "/api/v1/live-streams")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
            Content = JsonContent.Create(new { branchId, cameraId, mode = "inference" }),
        });

        Assert.Equal(HttpStatusCode.UnprocessableEntity, response.StatusCode);
        var envelope = await response.Content.ReadFromJsonAsync<ApiEnvelope>();
        Assert.Equal("DEVICE_UNAVAILABLE", envelope!.ErrorCode);
    }

    [Fact]
    public async Task CreateStream_GatewayFailure_ReturnsBadGateway()
    {
        var token = await LoginAsync();
        var (branchId, cameraId, _) = await CreateBranchWithCameraAsync(token, "Gateway Down Branch " + Guid.NewGuid(), true);
        _factory.GatewayClient.ThrowOnEnsure = true;

        using var response = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Post, "/api/v1/live-streams")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
            Content = JsonContent.Create(new { branchId, cameraId, mode = "monitoring" }),
        });

        Assert.Equal(HttpStatusCode.BadGateway, response.StatusCode);
    }

    [Fact]
    public async Task CreateStream_SamePathNameForRepeatedRequests_SameCameraAndMode()
    {
        var token = await LoginAsync();
        var (branchId, cameraId, _) = await CreateBranchWithCameraAsync(token, "Idempotent Branch " + Guid.NewGuid(), true);

        async Task<string> RequestAsync()
        {
            using var response = await _client.SendAsync(new HttpRequestMessage(
                HttpMethod.Post, "/api/v1/live-streams")
            {
                Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
                Content = JsonContent.Create(new { branchId, cameraId, mode = "monitoring" }),
            });
            var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
            return data.GetProperty("playbackUrl").GetString()!;
        }

        var first = await RequestAsync();
        var second = await RequestAsync();

        // Same Camera+mode always maps to the same gateway path (FS-14 §5: shared upstream pull, no
        // per-session path multiplication), even though sessionId differs on each call.
        Assert.Equal(first, second);
    }

    // --- GET /api/v1/auth/session (FS-14 §5, IP-16 T-3 — Nginx auth_request target) ---

    [Fact]
    public async Task Session_WithoutAuthentication_Returns401()
    {
        using var response = await _client.GetAsync("/api/v1/auth/session");

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task Session_WithValidToken_Returns200()
    {
        var token = await LoginAsync();

        using var response = await _client.SendAsync(new HttpRequestMessage(
            HttpMethod.Get, "/api/v1/auth/session")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
        });

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }
}

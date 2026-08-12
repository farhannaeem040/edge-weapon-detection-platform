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

// FS-12 / IP-14 T-277 — end-to-end coverage of the CameraKey and Jetson-network contract through the
// real API against real SQL Server.
//
// Uses the serialized ApiHostCollection because SqlServerApiHostFactory mutates process-wide
// environment variables; running these in parallel with another host would race on that state.
[Collection(ApiHostCollection.Name)]
public class CameraKeyAndDeviceNetworkApiTests : IDisposable
{
    private readonly BranchDeviceApiFactory _factory;
    private readonly HttpClient _client;

    public CameraKeyAndDeviceNetworkApiTests()
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

    private static object Request(
        string name = "Downtown Branch",
        string jetsonHost = "100.98.226.80",
        int? rtspOutputPort = 8554,
        params (string Name, string Key)[] cameras)
    {
        var effective = cameras.Length == 0
            ? new[] { ("Front Camera", "front-camera") }
            : cameras;

        return new
        {
            name,
            address = "1 High Street",
            contactDetails = "ops@example.local",
            jetsonHost,
            rtspOutputPort,
            cameras = effective
                .Select(c => new
                {
                    name = c.Item1,
                    rtspUrl = $"rtsp://camera.example.local:554/{c.Item2}",
                    cameraKey = c.Item2,
                })
                .ToArray(),
        };
    }

    private async Task<HttpResponseMessage> PostBranchAsync(string token, object body) =>
        await _client.SendAsync(new HttpRequestMessage(HttpMethod.Post, "/api/v1/branches")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
            Content = JsonContent.Create(body),
        });

    private static async Task<JsonElement> DataOf(HttpResponseMessage response) =>
        (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;

    private async Task<JsonElement> CreateBranchAsync(string token, object body)
    {
        using var response = await PostBranchAsync(token, body);
        Assert.Equal(HttpStatusCode.Created, response.StatusCode);
        return await DataOf(response);
    }

    private static async Task<string> ErrorCodeOf(HttpResponseMessage response)
    {
        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        return body.GetProperty("errorCode").GetString()!;
    }

    // --- Happy path (items 1-9) -----------------------------------------------------------------

    [Fact]
    public async Task Create_WithHostPortAndKey_Succeeds_AndComposesTheOutputUrl()
    {
        var token = await LoginAsync();

        var response = await PostBranchAsync(token, Request());

        Assert.Equal(HttpStatusCode.Created, response.StatusCode);
        var data = await DataOf(response);

        Assert.Equal("100.98.226.80", data.GetProperty("device").GetProperty("jetsonHost").GetString());
        Assert.Equal(8554, data.GetProperty("device").GetProperty("rtspOutputPort").GetInt32());
        Assert.Equal(
            "rtsp://100.98.226.80:8554",
            data.GetProperty("device").GetProperty("annotatedOutputBaseUrl").GetString());

        var camera = data.GetProperty("cameras")[0];
        Assert.Equal("front-camera", camera.GetProperty("cameraKey").GetString());
        Assert.Equal("cameras/front-camera", camera.GetProperty("outputPath").GetString());
        Assert.Equal(
            "rtsp://100.98.226.80:8554/cameras/front-camera",
            camera.GetProperty("outputStreamUrl").GetString());
    }

    [Fact]
    public async Task Create_StoresExactlyOneDeviceWithTheNetworkConfiguration()
    {
        var token = await LoginAsync();
        var data = await CreateBranchAsync(token, Request());
        var branchId = data.GetProperty("branchId").GetGuid();

        await using var db = _factory.CreateDbContext();

        var devices = await db.Devices.AsNoTracking().Where(d => d.BranchId == branchId).ToListAsync();
        Assert.Single(devices);
        Assert.Equal("100.98.226.80", devices[0].JetsonHost);
        Assert.Equal(8554, devices[0].RtspOutputPort);

        var camera = await db.Cameras.AsNoTracking().SingleAsync(c => c.BranchId == branchId);
        Assert.Equal("front-camera", camera.CameraKey);
    }

    [Theory]
    [InlineData("192.168.1.50")]
    [InlineData("jetson-ljmu.local")]
    [InlineData("jetson-branch-01.example.internal")]
    public async Task Create_AcceptsIpv4AndHostnames(string host)
    {
        var token = await LoginAsync();

        var response = await PostBranchAsync(token, Request(jetsonHost: host));

        Assert.Equal(HttpStatusCode.Created, response.StatusCode);
        var data = await DataOf(response);
        Assert.Equal(
            $"rtsp://{host}:8554",
            data.GetProperty("device").GetProperty("annotatedOutputBaseUrl").GetString());
    }

    [Fact]
    public async Task Create_BracketsIpv6InTheComposedUrl()
    {
        var token = await LoginAsync();

        var data = await CreateBranchAsync(token, Request(jetsonHost: "2001:db8::1"));

        Assert.Equal(
            "rtsp://[2001:db8::1]:8554/cameras/front-camera",
            data.GetProperty("cameras")[0].GetProperty("outputStreamUrl").GetString());
    }

    // --- Rejected hosts and ports (items 10-14) -------------------------------------------------

    [Theory]
    [InlineData("rtsp://100.98.226.80")]
    [InlineData("100.98.226.80:8554")]
    [InlineData("host/cameras")]
    [InlineData("user:password@host")]
    [InlineData("host?token=x")]
    public async Task Create_RejectsNonBareHost(string host)
    {
        var token = await LoginAsync();

        var response = await PostBranchAsync(token, Request(jetsonHost: host));

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        Assert.Equal("JETSON_HOST_INVALID", await ErrorCodeOf(response));
    }

    [Theory]
    [InlineData(0)]
    [InlineData(-1)]
    [InlineData(70000)]
    public async Task Create_RejectsInvalidPort(int port)
    {
        var token = await LoginAsync();

        var response = await PostBranchAsync(token, Request(rtspOutputPort: port));

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        Assert.Equal("RTSP_OUTPUT_PORT_INVALID", await ErrorCodeOf(response));
    }

    // --- Rejected keys (items 15-19) ------------------------------------------------------------

    [Theory]
    [InlineData("Front-Camera")]
    [InlineData("front camera")]
    [InlineData("front/camera")]
    [InlineData("../etc")]
    [InlineData("ab")]
    public async Task Create_RejectsMalformedKey(string key)
    {
        var token = await LoginAsync();

        var response = await PostBranchAsync(token, Request(cameras: [("Front", key)]));

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        // A blank/oversized key is caught by DataAnnotations first; the rest carry the named code.
        Assert.Contains(
            await ErrorCodeOf(response), new[] { "CAMERA_KEY_INVALID", "VALIDATION_ERROR" });
    }

    [Theory]
    [InlineData("ds-test")]
    [InlineData("api")]
    [InlineData("admin")]
    [InlineData("cameras")]
    public async Task Create_RejectsReservedKey(string key)
    {
        var token = await LoginAsync();

        var response = await PostBranchAsync(token, Request(cameras: [("Front", key)]));

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        Assert.Equal("CAMERA_KEY_RESERVED", await ErrorCodeOf(response));
    }

    [Fact]
    public async Task Create_RejectsDuplicateKeyWithinOneBranch()
    {
        var token = await LoginAsync();

        var response = await PostBranchAsync(
            token, Request(cameras: [("Front", "shared-key"), ("Rear", "shared-key")]));

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        Assert.Equal("CAMERA_KEY_ALREADY_EXISTS", await ErrorCodeOf(response));
    }

    [Fact]
    public async Task Create_AllowsTheSameKeyInADifferentBranch()
    {
        var token = await LoginAsync();

        Assert.Equal(
            HttpStatusCode.Created,
            (await PostBranchAsync(token, Request(name: "Branch A"))).StatusCode);
        Assert.Equal(
            HttpStatusCode.Created,
            (await PostBranchAsync(token, Request(name: "Branch B"))).StatusCode);
    }

    // --- Transactional rollback (items 20-23) ---------------------------------------------------

    [Fact]
    public async Task Create_InvalidKey_LeavesNoBranchDeviceCameraOrActivationKeyRow()
    {
        var token = await LoginAsync();

        var response = await PostBranchAsync(
            token, Request(name: "Doomed", cameras: [("Front", "front-camera"), ("Rear", "INVALID")]));

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);

        await using var db = _factory.CreateDbContext();

        Assert.Empty(await db.Branches.AsNoTracking().Where(b => b.Name == "Doomed").ToListAsync());
        Assert.Empty(await db.Cameras.AsNoTracking().ToListAsync());
        Assert.Empty(await db.Devices.AsNoTracking().ToListAsync());
        Assert.Empty(await db.ActivationKeys.AsNoTracking().ToListAsync());
    }

    // --- Device network update (items 24-28) ----------------------------------------------------

    [Fact]
    public async Task NetworkUpdate_ChangesOutputUrlButPreservesEverythingElse()
    {
        var token = await LoginAsync();
        var created = await CreateBranchAsync(token, Request());
        var branchId = created.GetProperty("branchId").GetGuid();
        var cameraId = created.GetProperty("cameras")[0].GetProperty("cameraId").GetGuid();

        var update = await _client.SendAsync(
            new HttpRequestMessage(HttpMethod.Put, $"/api/v1/devices/{branchId}/network")
            {
                Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
                Content = JsonContent.Create(new { jetsonHost = "10.20.0.15", rtspOutputPort = 9554 }),
            });

        Assert.Equal(HttpStatusCode.OK, update.StatusCode);

        var read = await _client.SendAsync(
            new HttpRequestMessage(HttpMethod.Get, $"/api/v1/branches/{branchId}")
            {
                Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
            });
        var data = await DataOf(read);
        var camera = data.GetProperty("cameras")[0];

        // The client-facing URL moved...
        Assert.Equal(
            "rtsp://10.20.0.15:9554/cameras/front-camera",
            camera.GetProperty("outputStreamUrl").GetString());
        // ...while identity, key and local mount are all untouched (FS-12 §9 item 10).
        Assert.Equal(cameraId, camera.GetProperty("cameraId").GetGuid());
        Assert.Equal("front-camera", camera.GetProperty("cameraKey").GetString());
        Assert.Equal("cameras/front-camera", camera.GetProperty("outputPath").GetString());
    }

    [Fact]
    public async Task NetworkUpdate_DoesNotChangeTheCameraMountOrKey()
    {
        // FS-12 §6: host/port are not configurationVersion inputs, so the Agent sees no pipeline
        // change at all. The observable proof through the Admin API is that neither the local mount
        // nor the key moves — only the externally-composed URL does (asserted above).
        var token = await LoginAsync();
        var created = await CreateBranchAsync(token, Request());
        var branchId = created.GetProperty("branchId").GetGuid();
        var before = created.GetProperty("cameras")[0].GetProperty("outputPath").GetString();

        using var update = await _client.SendAsync(
            new HttpRequestMessage(HttpMethod.Put, $"/api/v1/devices/{branchId}/network")
            {
                Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
                Content = JsonContent.Create(new { jetsonHost = "10.20.0.15", rtspOutputPort = 9554 }),
            });
        Assert.Equal(HttpStatusCode.OK, update.StatusCode);

        using var read = await _client.SendAsync(
            new HttpRequestMessage(HttpMethod.Get, $"/api/v1/branches/{branchId}")
            {
                Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
            });
        var camera = (await DataOf(read)).GetProperty("cameras")[0];

        Assert.Equal(before, camera.GetProperty("outputPath").GetString());
        Assert.Equal("front-camera", camera.GetProperty("cameraKey").GetString());
    }

    // --- Key immutability (item 33) -------------------------------------------------------------

    [Fact]
    public async Task Update_AttemptingToChangeAnExistingCameraKey_IsRejected()
    {
        var token = await LoginAsync();
        var created = await CreateBranchAsync(token, Request());
        var branchId = created.GetProperty("branchId").GetGuid();
        var cameraId = created.GetProperty("cameras")[0].GetProperty("cameraId").GetGuid();

        var response = await _client.SendAsync(
            new HttpRequestMessage(HttpMethod.Put, $"/api/v1/branches/{branchId}")
            {
                Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
                Content = JsonContent.Create(new
                {
                    name = "Downtown Branch",
                    address = "1 High Street",
                    contactDetails = "ops@example.local",
                    cameras = new[]
                    {
                        new
                        {
                            cameraId,
                            name = "Front Camera",
                            rtspUrl = "rtsp://camera.example.local:554/front-camera",
                            cameraKey = "a-different-key",
                        },
                    },
                }),
            });

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        Assert.Equal("CAMERA_KEY_IMMUTABLE", await ErrorCodeOf(response));
    }

    [Fact]
    public async Task Update_RenamingACamera_LeavesKeyAndOutputPathUntouched()
    {
        var token = await LoginAsync();
        var created = await CreateBranchAsync(token, Request());
        var branchId = created.GetProperty("branchId").GetGuid();
        var cameraId = created.GetProperty("cameras")[0].GetProperty("cameraId").GetGuid();

        var response = await _client.SendAsync(
            new HttpRequestMessage(HttpMethod.Put, $"/api/v1/branches/{branchId}")
            {
                Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
                Content = JsonContent.Create(new
                {
                    name = "Downtown Branch",
                    address = "1 High Street",
                    contactDetails = "ops@example.local",
                    cameras = new[]
                    {
                        new
                        {
                            cameraId,
                            name = "A Completely New Label",
                            rtspUrl = "rtsp://camera.example.local:554/front-camera",
                        },
                    },
                }),
            });

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var camera = (await DataOf(response)).GetProperty("cameras")[0];
        Assert.Equal("front-camera", camera.GetProperty("cameraKey").GetString());
        Assert.Equal("cameras/front-camera", camera.GetProperty("outputPath").GetString());
    }
}

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

// Full HTTP-pipeline, real-SQL-Server tests for GET /api/v1/device/configuration (FS-11 §3/§16,
// IP-13 T-228) — a real, activated Device, device-authenticated exactly as
// DeviceConfigurationCoordinator would authenticate, against the production
// DeviceConfigurationController/DeviceConfigurationService/SQL Server path.
//
// Every RTSP value here is a non-routable placeholder (.invalid, RFC 2606).
[Collection(ApiHostCollection.Name)]
public class DeviceConfigurationApiTests : IDisposable
{
    private const string DeviceIdHeader = "X-Device-Id";
    private const string DeviceSecretHeader = "X-Device-Secret";

    private readonly DeviceConfigurationApiFactory _factory;
    private readonly HttpClient _client;

    public DeviceConfigurationApiTests()
    {
        _factory = new DeviceConfigurationApiFactory();
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

    private async Task<Guid> CreateBranchAsync(string token, string name, params (string Name, string RtspUrl)[] cameras)
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
                cameras = cameras.Select(c => new { name = c.Name, rtspUrl = c.RtspUrl, cameraKey = $"cam-{Guid.NewGuid():N}" }).ToArray(),
            }),
        });
        Assert.Equal(HttpStatusCode.Created, response.StatusCode);
        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        return data.GetProperty("branchId").GetGuid();
    }

    private async Task<string> GetActivationKeyAsync(string token, Guid branchId)
    {
        using var request = new HttpRequestMessage(
            HttpMethod.Post, $"/api/v1/devices/{branchId}/activation-key/regenerate")
        {
            Headers = { Authorization = new AuthenticationHeaderValue("Bearer", token) },
        };
        using var response = await _client.SendAsync(request);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
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

    private async Task<(Guid BranchId, Guid DeviceId, string Secret)> SeedActivatedBranchAsync(
        string branchName, params (string Name, string RtspUrl)[] cameras)
    {
        var token = await LoginAsync();
        var branchId = await CreateBranchAsync(token, branchName, cameras);
        // Branch creation's own response already carried an Activation Key, but regenerating keeps
        // this helper independent of that response shape and matches the pattern used for
        // activation-key regeneration elsewhere in the test suite.
        var key = await GetActivationKeyAsync(token, branchId);
        var (deviceId, secret) = await ActivateDeviceAsync(key);
        return (branchId, deviceId, secret);
    }

    private async Task<HttpResponseMessage> GetConfigurationAsync(Guid deviceId, string secret)
    {
        var request = new HttpRequestMessage(HttpMethod.Get, "/api/v1/device/configuration")
        {
            Headers = { { DeviceIdHeader, deviceId.ToString() }, { DeviceSecretHeader, secret } },
        };
        return await _client.SendAsync(request);
    }

    // --- FS-11 §16 item 1/2/3: only this Device's Cameras, immutable id, name may contain spaces ---

    [Fact]
    public async Task GetConfiguration_ReturnsOnlyThisDevicesEnabledCameras_WithImmutableIdAndSpacedName()
    {
        var (branchId, deviceId, secret) = await SeedActivatedBranchAsync(
            "Config API Branch A " + Guid.NewGuid(),
            ("Front Camera", "rtsp://camera.example.invalid:554/stream1"));

        using var response = await GetConfigurationAsync(deviceId, secret);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);

        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        Assert.Equal(1, data.GetProperty("schemaVersion").GetInt32());
        Assert.Equal(deviceId, data.GetProperty("deviceId").GetGuid());
        Assert.Equal(branchId, data.GetProperty("branchId").GetGuid());

        var cameras = data.GetProperty("cameras").EnumerateArray().ToList();
        Assert.Single(cameras);
        Assert.Equal("Front Camera", cameras[0].GetProperty("name").GetString());
        Assert.NotEqual(Guid.Empty, cameras[0].GetProperty("cameraId").GetGuid());
        Assert.Equal(0, cameras[0].GetProperty("sourceOrder").GetInt32());
    }

    [Fact]
    public async Task GetConfiguration_EmptyCameraListIsImpossibleAtBranchCreation_ButServiceHandlesZeroEnabledCameras()
    {
        // FS-11 §16 item 13: every Branch requires >= 1 camera at creation (FS-02 §12), so an empty
        // set can only arise once a disable workflow exists. Proven here by disabling the only
        // camera directly through the DbContext (no admin endpoint to do this yet, per Camera.cs).
        var (_, deviceId, secret) = await SeedActivatedBranchAsync(
            "Config API Branch Empty " + Guid.NewGuid(),
            ("Only Camera", "rtsp://camera.example.invalid:554/stream1"));

        await using (var dbContext = _factory.CreateDbContext())
        {
            var camera = await dbContext.Cameras.SingleAsync();
            dbContext.Entry(camera).Property("Enabled").CurrentValue = false;
            await dbContext.SaveChangesAsync();
        }

        using var response = await GetConfigurationAsync(deviceId, secret);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        Assert.Empty(data.GetProperty("cameras").EnumerateArray());
    }

    // --- FS-11 §16 items 4-7: configurationVersion hash behaviour -----------------------------------

    [Fact]
    public async Task GetConfiguration_NameOnlyChange_DoesNotChangeConfigurationVersion()
    {
        var (branchId, deviceId, secret) = await SeedActivatedBranchAsync(
            "Config API Branch Rename " + Guid.NewGuid(),
            ("Front Camera", "rtsp://camera.example.invalid:554/stream1"));

        var before = await GetConfigurationVersionAsync(deviceId, secret);

        await using (var dbContext = _factory.CreateDbContext())
        {
            var camera = await dbContext.Cameras.SingleAsync(c => c.BranchId == branchId);
            camera.UpdateConfiguration("Main Entrance Camera", camera.RtspUrl);
            await dbContext.SaveChangesAsync();
        }

        var after = await GetConfigurationVersionAsync(deviceId, secret);
        Assert.Equal(before, after);
    }

    [Fact]
    public async Task GetConfiguration_StreamUrlChange_ChangesConfigurationVersion()
    {
        var (branchId, deviceId, secret) = await SeedActivatedBranchAsync(
            "Config API Branch UrlChange " + Guid.NewGuid(),
            ("Front Camera", "rtsp://camera.example.invalid:554/stream1"));

        var before = await GetConfigurationVersionAsync(deviceId, secret);

        await using (var dbContext = _factory.CreateDbContext())
        {
            var camera = await dbContext.Cameras.SingleAsync(c => c.BranchId == branchId);
            camera.UpdateConfiguration(camera.Name, "rtsp://camera.example.invalid:554/stream-new");
            await dbContext.SaveChangesAsync();
        }

        var after = await GetConfigurationVersionAsync(deviceId, secret);
        Assert.NotEqual(before, after);
    }

    [Fact]
    public async Task GetConfiguration_EnabledChange_ChangesConfigurationVersion()
    {
        var (branchId, deviceId, secret) = await SeedActivatedBranchAsync(
            "Config API Branch EnabledChange " + Guid.NewGuid(),
            ("Front Camera", "rtsp://camera.example.invalid:554/stream1"),
            ("Rear Camera", "rtsp://camera.example.invalid:554/stream2"));

        var before = await GetConfigurationVersionAsync(deviceId, secret);

        await using (var dbContext = _factory.CreateDbContext())
        {
            var camera = await dbContext.Cameras
                .Where(c => c.BranchId == branchId)
                .OrderBy(c => c.SourceOrder)
                .Skip(1)
                .SingleAsync();
            dbContext.Entry(camera).Property("Enabled").CurrentValue = false;
            await dbContext.SaveChangesAsync();
        }

        var after = await GetConfigurationVersionAsync(deviceId, secret);
        Assert.NotEqual(before, after);
    }

    [Fact]
    public async Task GetConfiguration_SourceOrderChange_ChangesConfigurationVersion()
    {
        var (branchId, deviceId, secret) = await SeedActivatedBranchAsync(
            "Config API Branch OrderChange " + Guid.NewGuid(),
            ("Front Camera", "rtsp://camera.example.invalid:554/stream1"),
            ("Rear Camera", "rtsp://camera.example.invalid:554/stream2"));

        var before = await GetConfigurationVersionAsync(deviceId, secret);

        await using (var dbContext = _factory.CreateDbContext())
        {
            // Move the second camera to a SourceOrder no other enabled camera on this branch holds
            // (a true 0<->1 swap in one SaveChanges would transiently collide with the filtered
            // unique index — no admin reordering workflow exists yet to do this atomically).
            var camera = await dbContext.Cameras
                .Where(c => c.BranchId == branchId)
                .OrderBy(c => c.SourceOrder)
                .Skip(1)
                .SingleAsync();
            dbContext.Entry(camera).Property("SourceOrder").CurrentValue = 5;
            await dbContext.SaveChangesAsync();
        }

        var after = await GetConfigurationVersionAsync(deviceId, secret);
        Assert.NotEqual(before, after);
    }

    // --- FS-11 §16 item 9: deterministic ordering ---------------------------------------------------

    [Fact]
    public async Task GetConfiguration_ReturnsCamerasOrderedBySourceOrder_NotInsertionOrder()
    {
        var (branchId, deviceId, secret) = await SeedActivatedBranchAsync(
            "Config API Branch Order " + Guid.NewGuid(),
            ("Camera Zero", "rtsp://camera.example.invalid:554/s0"),
            ("Camera One", "rtsp://camera.example.invalid:554/s1"));

        // Reverse the two SourceOrders so returned order can only match if the service truly sorts
        // by SourceOrder rather than relying on database/insertion order.
        await using (var dbContext = _factory.CreateDbContext())
        {
            var cameras = await dbContext.Cameras
                .Where(c => c.BranchId == branchId)
                .OrderBy(c => c.SourceOrder)
                .ToListAsync();
            dbContext.Entry(cameras[0]).Property("SourceOrder").CurrentValue = 5;
            dbContext.Entry(cameras[1]).Property("SourceOrder").CurrentValue = 1;
            await dbContext.SaveChangesAsync();
        }

        using var response = await GetConfigurationAsync(deviceId, secret);
        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        var cameras2 = data.GetProperty("cameras").EnumerateArray().ToList();

        Assert.Equal("Camera One", cameras2[0].GetProperty("name").GetString());
        Assert.Equal(1, cameras2[0].GetProperty("sourceOrder").GetInt32());
        Assert.Equal("Camera Zero", cameras2[1].GetProperty("name").GetString());
        Assert.Equal(5, cameras2[1].GetProperty("sourceOrder").GetInt32());
    }

    // --- FS-11 §16 item 10: invalid credentials -> 401 --------------------------------------------

    [Fact]
    public async Task GetConfiguration_InvalidDeviceCredentials_ReturnsUniform401()
    {
        using var response = await GetConfigurationAsync(Guid.NewGuid(), "wrong-secret");

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
        var envelope = await response.Content.ReadFromJsonAsync<ApiEnvelope>();
        Assert.Equal("INVALID_DEVICE_CREDENTIALS", envelope!.ErrorCode);
    }

    // --- FS-11 §16 item 12: cannot fetch another Branch's configuration -----------------------------

    [Fact]
    public async Task GetConfiguration_NeverReturnsAnotherBranchsCameras()
    {
        var (branchA, deviceA, secretA) = await SeedActivatedBranchAsync(
            "Config API Branch Cross A " + Guid.NewGuid(),
            ("Branch A Camera", "rtsp://camera.example.invalid:554/a"));
        var (branchB, _, _) = await SeedActivatedBranchAsync(
            "Config API Branch Cross B " + Guid.NewGuid(),
            ("Branch B Camera", "rtsp://camera.example.invalid:554/b"));

        using var response = await GetConfigurationAsync(deviceA, secretA);
        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;

        Assert.Equal(branchA, data.GetProperty("branchId").GetGuid());
        Assert.NotEqual(branchB, data.GetProperty("branchId").GetGuid());
        var cameraNames = data.GetProperty("cameras").EnumerateArray()
            .Select(c => c.GetProperty("name").GetString())
            .ToList();
        Assert.DoesNotContain("Branch B Camera", cameraNames);
    }

    // --- FS-11 §16 item 14: no Device secret in the response ---------------------------------------

    [Fact]
    public async Task GetConfiguration_ResponseNeverContainsADeviceSecretOrCredentialField()
    {
        var (_, deviceId, secret) = await SeedActivatedBranchAsync(
            "Config API Branch NoSecret " + Guid.NewGuid(),
            ("Front Camera", "rtsp://camera.example.invalid:554/stream1"));

        using var response = await GetConfigurationAsync(deviceId, secret);
        var raw = await response.Content.ReadAsStringAsync();

        Assert.DoesNotContain(secret, raw);
        Assert.DoesNotContain("sharedSecret", raw, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("activationKey", raw, StringComparison.OrdinalIgnoreCase);
    }

    // --- FS-11 §11 (per-camera annotated outputs): OutputPath contract ------------------------------

    [Fact]
    public async Task GetConfiguration_OneCamera_ReturnsOneOutputPathDerivedFromImmutableCameraId()
    {
        var (_, deviceId, secret) = await SeedActivatedBranchAsync(
            "Config API Branch Out1 " + Guid.NewGuid(),
            ("Front Camera", "rtsp://camera.example.invalid:554/stream1"));

        using var response = await GetConfigurationAsync(deviceId, secret);
        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;

        var cameras = data.GetProperty("cameras").EnumerateArray().ToList();
        var camera = Assert.Single(cameras);
        var cameraId = camera.GetProperty("cameraId").GetGuid();

        // FS-12 §2.1 — derived, never stored, and now from the administrator-defined CameraKey
        // rather than the CameraId. The immutable CameraId is still returned separately, which is
        // the whole point: public mount identity and detection identity are different things.
        var cameraKey = camera.GetProperty("cameraKey").GetString()!;
        Assert.Equal($"cameras/{cameraKey}", camera.GetProperty("outputPath").GetString());
        Assert.NotEqual(cameraId.ToString("D"), cameraKey);
    }

    [Fact]
    public async Task GetConfiguration_MultipleCameras_ReturnUniqueOutputPathsOnePerEnabledCamera()
    {
        var (_, deviceId, secret) = await SeedActivatedBranchAsync(
            "Config API Branch OutN " + Guid.NewGuid(),
            ("Front Camera", "rtsp://camera.example.invalid:554/stream1"),
            ("Rear Entrance", "rtsp://camera.example.invalid:554/stream2"),
            ("Side Door", "rtsp://camera.example.invalid:554/stream3"));

        using var response = await GetConfigurationAsync(deviceId, secret);
        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;

        var outputPaths = data.GetProperty("cameras").EnumerateArray()
            .Select(c => c.GetProperty("outputPath").GetString()!)
            .ToList();

        // N enabled Cameras => N outputs, and no two Cameras may ever share a mount.
        Assert.Equal(3, outputPaths.Count);
        Assert.Equal(outputPaths.Count, outputPaths.Distinct().Count());
        Assert.All(outputPaths, p => Assert.StartsWith("cameras/", p));
    }

    [Fact]
    public async Task GetConfiguration_CameraRename_DoesNotChangeOutputPath()
    {
        var (branchId, deviceId, secret) = await SeedActivatedBranchAsync(
            "Config API Branch OutRename " + Guid.NewGuid(),
            ("Front Camera", "rtsp://camera.example.invalid:554/stream1"));

        var before = await GetOutputPathsAsync(deviceId, secret);

        await using (var dbContext = _factory.CreateDbContext())
        {
            var camera = await dbContext.Cameras.SingleAsync(c => c.BranchId == branchId);
            camera.UpdateConfiguration("Totally Different Name", camera.RtspUrl);
            await dbContext.SaveChangesAsync();
        }

        // The output mount is an identity, not a label — a rename must never move a published stream.
        Assert.Equal(before, await GetOutputPathsAsync(deviceId, secret));
    }

    [Fact]
    public async Task GetConfiguration_StreamUrlChange_DoesNotChangeOutputPath()
    {
        var (branchId, deviceId, secret) = await SeedActivatedBranchAsync(
            "Config API Branch OutUrl " + Guid.NewGuid(),
            ("Front Camera", "rtsp://camera.example.invalid:554/stream1"));

        var before = await GetOutputPathsAsync(deviceId, secret);

        await using (var dbContext = _factory.CreateDbContext())
        {
            var camera = await dbContext.Cameras.SingleAsync(c => c.BranchId == branchId);
            camera.UpdateConfiguration(camera.Name, "rtsp://camera.example.invalid:554/relocated");
            await dbContext.SaveChangesAsync();
        }

        // The input moved; the output identity did not.
        Assert.Equal(before, await GetOutputPathsAsync(deviceId, secret));
    }

    [Fact]
    public async Task GetConfiguration_OutputPathContainsNoSecretAndNoInputStreamCredentials()
    {
        var (_, deviceId, secret) = await SeedActivatedBranchAsync(
            "Config API Branch OutSafe " + Guid.NewGuid(),
            ("Front Camera", "rtsp://someuser:somepassword@camera.example.invalid:554/stream1"));

        using var response = await GetConfigurationAsync(deviceId, secret);
        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;

        foreach (var camera in data.GetProperty("cameras").EnumerateArray())
        {
            var outputPath = camera.GetProperty("outputPath").GetString()!;
            Assert.DoesNotContain("somepassword", outputPath);
            Assert.DoesNotContain("someuser", outputPath);
            Assert.DoesNotContain(secret, outputPath);
            // A mount is a URL path segment set, never an absolute URL and never an escape upwards.
            Assert.DoesNotContain("..", outputPath);
            Assert.DoesNotContain("://", outputPath);
        }
    }

    // --- FS-11 §11: Device annotated-output base URL is NOT pipeline-relevant ----------------------

    [Fact]
    public async Task DeviceOutputBaseUrlChange_DoesNotChangeConfigurationVersionOrOutputPaths()
    {
        var (branchId, deviceId, secret) = await SeedActivatedBranchAsync(
            "Config API Branch BaseUrl " + Guid.NewGuid(),
            ("Front Camera", "rtsp://camera.example.invalid:554/stream1"),
            ("Rear Entrance", "rtsp://camera.example.invalid:554/stream2"));

        var versionBefore = await GetConfigurationVersionAsync(deviceId, secret);
        var pathsBefore = await GetOutputPathsAsync(deviceId, secret);

        await using (var dbContext = _factory.CreateDbContext())
        {
            var device = await dbContext.Devices.SingleAsync(d => d.BranchId == branchId);
            device.SetAnnotatedOutputBaseUrl("rtsp://100.98.226.80:8554");
            await dbContext.SaveChangesAsync();
        }

        // Only the advertised host/port moved; every Bridge mount path is identical, so the Agent
        // must see no pipeline change at all and therefore must not restart.
        Assert.Equal(versionBefore, await GetConfigurationVersionAsync(deviceId, secret));
        Assert.Equal(pathsBefore, await GetOutputPathsAsync(deviceId, secret));
    }

    [Fact]
    public async Task DeviceConfigurationResponse_NeverCarriesTheAnnotatedOutputBaseUrl()
    {
        // The Agent publishes locally and must not need the public address (it composes nothing).
        var (branchId, deviceId, secret) = await SeedActivatedBranchAsync(
            "Config API Branch NoBase " + Guid.NewGuid(),
            ("Front Camera", "rtsp://camera.example.invalid:554/stream1"));

        await using (var dbContext = _factory.CreateDbContext())
        {
            var device = await dbContext.Devices.SingleAsync(d => d.BranchId == branchId);
            device.SetAnnotatedOutputBaseUrl("rtsp://100.98.226.80:8554");
            await dbContext.SaveChangesAsync();
        }

        using var response = await GetConfigurationAsync(deviceId, secret);
        var raw = await response.Content.ReadAsStringAsync();

        Assert.DoesNotContain("100.98.226.80", raw);
        Assert.DoesNotContain("annotatedOutputBaseUrl", raw, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("outputStreamUrl", raw, StringComparison.OrdinalIgnoreCase);
    }

    private async Task<List<string>> GetOutputPathsAsync(Guid deviceId, string secret)
    {
        using var response = await GetConfigurationAsync(deviceId, secret);
        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        return data.GetProperty("cameras").EnumerateArray()
            .Select(c => c.GetProperty("outputPath").GetString()!)
            .ToList();
    }

    private async Task<string> GetConfigurationVersionAsync(Guid deviceId, string secret)
    {
        using var response = await GetConfigurationAsync(deviceId, secret);
        var data = (await response.Content.ReadFromJsonAsync<ApiEnvelope>())!.Data!.Value;
        return data.GetProperty("configurationVersion").GetString()!;
    }
}

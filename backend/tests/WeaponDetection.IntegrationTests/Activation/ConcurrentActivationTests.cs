using System;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.EntityFrameworkCore;
using WeaponDetection.Domain;
using WeaponDetection.Infrastructure.Security;
using WeaponDetection.IntegrationTests.Api;
using Xunit;

namespace WeaponDetection.IntegrationTests.Activation;

// The dedicated concurrency verification for POST /api/v1/activate (IP-01 T-21, FS-02 §12, AC-16),
// run against the real in-process HTTP pipeline and a real SQL Server database. It proves the
// single-use guarantee under genuine concurrency, not simulated in-process locking: two activation
// requests presenting the SAME valid, unconsumed key are released simultaneously by a Barrier(2), so
// they genuinely contend for the update lock the activation transaction takes on the Activation Key
// row (DeviceService.ActivateAsync, T-19). Exactly one may consume the key.
//
// The assertions are stable regardless of the exact scheduling because the UPDLOCK makes the outcome
// deterministic — one request acquires the row lock and consumes the key; the other blocks until the
// first commits, then observes the key as Consumed and receives the uniform rejection. The barrier
// exists to force real lock contention (so the test exercises the concurrent path, not a lucky
// sequential ordering); the correctness does not depend on winning a timing race. The scenario is
// repeated several times, and the whole test is safe to run repeatedly, to demonstrate it is not
// flaky.
//
// This class joins ApiHostCollection: SqlServerApiHostFactory configures itself via process-wide
// environment variables, so only one host may be alive at a time and xUnit must run these host-based
// classes sequentially. No plaintext key or shared secret is logged; captured values are compared in
// memory only.
[Collection(ApiHostCollection.Name)]
public class ConcurrentActivationTests : IDisposable
{
    private const string ActivateRoute = "/api/v1/activate";
    private const string BranchesRoute = "/api/v1/branches";

    private readonly BranchDeviceApiFactory _factory;
    private readonly HttpClient _clientA;
    private readonly HttpClient _clientB;

    public ConcurrentActivationTests()
    {
        _factory = new BranchDeviceApiFactory();
        // Two independent clients so neither request can be serialised behind the other on a shared
        // connection; the contention we are testing must occur at the database, not the client.
        _clientA = _factory.CreateClient();
        _clientB = _factory.CreateClient();
    }

    public void Dispose()
    {
        _clientA.Dispose();
        _clientB.Dispose();
        _factory.Dispose();
    }

    private sealed record ApiEnvelope(bool Success, string? Message, JsonElement? Data, string? ErrorCode);

    private async Task<string> LoginAsync()
    {
        var response = await _clientA.PostAsJsonAsync("/api/v1/auth/login", new
        {
            credentialIdentifier = SqlServerApiHostFactory.AdminIdentifier,
            password = SqlServerApiHostFactory.AdminPassword,
        });
        response.EnsureSuccessStatusCode();
        var envelope = await response.Content.ReadFromJsonAsync<ApiEnvelope>();
        return envelope!.Data!.Value.GetProperty("token").GetString()!;
    }

    private async Task<(Guid BranchId, string Key)> CreateBranchAsync(string token, string name)
    {
        var response = await _clientA.SendAsync(new HttpRequestMessage(HttpMethod.Post, BranchesRoute)
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

    private static string KeyIdOf(string key) => key.Split(ActivationKeyGenerator.Delimiter)[0];

    // Fires both requests through the barrier so they contend at the database, and returns both
    // responses' materialised (status, body).
    private async Task<(HttpStatusCode Status, string Body)[]> RaceAsync(string key)
    {
        using var barrier = new Barrier(2);

        async Task<(HttpStatusCode, string)> Fire(HttpClient client)
        {
            var request = new HttpRequestMessage(HttpMethod.Post, ActivateRoute)
            {
                Content = JsonContent.Create(new { activationKey = key }),
            };

            // Both threads block here until both are ready, then are released together — the requests
            // hit the wire simultaneously rather than one after the other.
            barrier.SignalAndWait();

            using var response = await client.SendAsync(request);
            return (response.StatusCode, await response.Content.ReadAsStringAsync());
        }

        var a = Task.Run(() => Fire(_clientA));
        var b = Task.Run(() => Fire(_clientB));
        var results = await Task.WhenAll(a, b);
        return results;
    }

    [Fact]
    public async Task TwoConcurrentActivations_OfTheSameKey_ExactlyOneSucceeds_AndNoDuplicateCredentialsAreIssued()
    {
        var token = await LoginAsync();

        // Several independent races, each on a fresh branch/key, to demonstrate the guarantee holds
        // repeatedly and is not a one-off timing outcome.
        for (var iteration = 0; iteration < 5; iteration++)
        {
            var (branchId, key) = await CreateBranchAsync(token, $"Race Branch {iteration}");

            var results = await RaceAsync(key);

            // Exactly one 200 and exactly one 401 — never two successes, never two failures.
            Assert.Equal(1, results.Count(r => r.Status == HttpStatusCode.OK));
            Assert.Equal(1, results.Count(r => r.Status == HttpStatusCode.Unauthorized));

            var success = results.Single(r => r.Status == HttpStatusCode.OK);
            var rejection = results.Single(r => r.Status == HttpStatusCode.Unauthorized);

            using var successDoc = JsonDocument.Parse(success.Body);
            var successData = successDoc.RootElement.GetProperty("data");
            var winnerDeviceId = successData.GetProperty("deviceId").GetGuid();
            Assert.NotEqual(Guid.Empty, winnerDeviceId);

            // The loser receives the same uniform activation rejection as any consumed-key reuse, and
            // its body exposes nothing about the race, the database, the lock, or the internal reason.
            using var rejectionDoc = JsonDocument.Parse(rejection.Body);
            Assert.False(rejectionDoc.RootElement.GetProperty("success").GetBoolean());
            Assert.Equal("INVALID_ACTIVATION_KEY", rejectionDoc.RootElement.GetProperty("errorCode").GetString());
            Assert.Equal("The activation key is invalid.", rejectionDoc.RootElement.GetProperty("message").GetString());
            foreach (var leak in new[]
                     {
                         "race", "sql", "lock", "updlock", "deadlock", "concurren",
                         "transaction", "Consumed", "Invalidated", "Malformed", "secret",
                     })
            {
                Assert.DoesNotContain(leak, rejection.Body, StringComparison.OrdinalIgnoreCase);
            }

            // Database truth: the key was consumed exactly once, exactly one device exists for the
            // branch with exactly one persistent DeviceId (matching the winner), and no duplicate or
            // partial rows were created.
            await using var db = _factory.CreateDbContext();

            var devices = await db.Devices.AsNoTracking().Where(d => d.BranchId == branchId).ToListAsync();
            var device = Assert.Single(devices);
            Assert.Equal(DeviceActivationStatus.Activated, device.ActivationStatus);
            Assert.Equal(winnerDeviceId, device.DeviceId);

            var keys = await db.ActivationKeys.AsNoTracking()
                .Where(k => k.DeviceRecordId == device.DeviceRecordId)
                .ToListAsync();
            var storedKey = Assert.Single(keys);
            Assert.Equal(KeyIdOf(key), storedKey.ActivationKeyId);
            Assert.Equal(ActivationKeyStatus.Consumed, storedKey.Status);

            // A post-race replay of the same key is still rejected — the key remains single-use.
            using var replay = await _clientA.PostAsJsonAsync(ActivateRoute, new { activationKey = key });
            Assert.Equal(HttpStatusCode.Unauthorized, replay.StatusCode);
        }
    }
}

using System;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.EntityFrameworkCore;
using WeaponDetection.Domain;
using WeaponDetection.IntegrationTests.Api.TestEndpoints;
using Xunit;

namespace WeaponDetection.IntegrationTests.Api;

// Full HTTP-pipeline integration tests for the JWT + session-revocation authentication middleware
// (IP-01 T-10, §12), run against a real in-process TestServer and a real SQL Server database.
// They exercise the middleware exactly as a client would: over HTTP, through the real Program.cs
// pipeline, against placeholder protected endpoints (ProtectedTestController).
//
// Covers FS-01 AC-3, AC-4, AC-7 and test scenarios T-03, T-04, T-05, T-09, T-13, T-14, T-15, T-16,
// plus the revoked-session rejection that AC-5/AC-6 will build on once T-11 adds the logout
// endpoint (revocation is applied directly to the AdminSession record here, because no logout
// route exists yet).
//
// No test in this file prints a plaintext password or a complete access token to console/log
// output.
[Collection(ApiHostCollection.Name)]
public class SessionAuthorizationApiTests : IClassFixture<ProtectedEndpointApiFactory>, IDisposable
{
    private const string ProtectedRoute = "/api/v1/test/protected";
    private const string UnannotatedRoute = "/api/v1/test/unannotated";

    private readonly ProtectedEndpointApiFactory _factory;
    private readonly HttpClient _client;

    public SessionAuthorizationApiTests(ProtectedEndpointApiFactory factory)
    {
        _factory = factory;
        _client = factory.CreateClient();
    }

    public void Dispose() => _client.Dispose();

    private sealed record ApiEnvelope(bool Success, string? Message, JsonElement? Data, string? ErrorCode);

    // A genuinely valid session: issued by the real login endpoint, exactly as the Dashboard
    // would obtain one.
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

    private Task<HttpResponseMessage> GetProtectedAsync(string? token, string route = ProtectedRoute)
    {
        using var request = new HttpRequestMessage(HttpMethod.Get, route);

        if (token is not null)
        {
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        }

        return _client.SendAsync(request);
    }

    private async Task<Guid> GetAdminUserIdAsync()
    {
        await using var dbContext = _factory.CreateDbContext();
        return (await dbContext.AdminUsers.AsNoTracking().SingleAsync()).UserId;
    }

    private async Task<Guid> SeedSessionAsync(
        Guid userId, DateTimeOffset issuedAt, DateTimeOffset expiresAt, bool revoked = false)
    {
        var sessionId = Guid.NewGuid();
        var session = new AdminSession(sessionId, userId, issuedAt, expiresAt);

        if (revoked)
        {
            session.Revoke();
        }

        await using var dbContext = _factory.CreateDbContext();
        dbContext.AdminSessions.Add(session);
        await dbContext.SaveChangesAsync();

        return sessionId;
    }

    private async Task RevokeAllSessionsAsync()
    {
        await using var dbContext = _factory.CreateDbContext();
        var sessions = await dbContext.AdminSessions.ToListAsync();

        foreach (var session in sessions)
        {
            session.Revoke();
        }

        await dbContext.SaveChangesAsync();
    }

    // --- Positive path: a real session reaches the controller (FS-01 §14 T-01) ---

    [Fact]
    public async Task ProtectedEndpoint_WithSessionIssuedByLogin_Returns200()
    {
        var token = await LoginAsync();

        using var response = await GetProtectedAsync(token);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }

    [Fact]
    public async Task ProtectedEndpoint_WithSessionIssuedByLogin_ReachesTheControllerAndUsesTheEnvelope()
    {
        var token = await LoginAsync();

        using var response = await GetProtectedAsync(token);
        var envelope = await response.Content.ReadFromJsonAsync<ApiEnvelope>();

        Assert.True(envelope!.Success);
        Assert.Null(envelope.ErrorCode);
        Assert.True(envelope.Data!.Value.GetProperty("reached").GetBoolean());
    }

    // --- Rejection paths (FS-01 AC-3, AC-7; §12; §14 T-03/T-04/T-05/T-09/T-13/T-14/T-15/T-16) ---

    [Fact]
    public async Task ProtectedEndpoint_NoAuthorizationHeader_Returns401()
    {
        using var response = await GetProtectedAsync(token: null);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task ProtectedEndpoint_MalformedToken_Returns401()
    {
        using var response = await GetProtectedAsync("this-is-not-a-jwt");

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task ProtectedEndpoint_TokenSignedWithAnotherKey_Returns401()
    {
        var userId = await GetAdminUserIdAsync();
        var now = DateTimeOffset.UtcNow;
        var sessionId = await SeedSessionAsync(userId, now, now.AddMinutes(60));

        // Everything about this token is correct except the signature — proving the signing key,
        // not merely the claim contents, is what the middleware trusts.
        var forged = TestTokenBuilder.Create(
            userId, sessionId, now, now.AddMinutes(60), signingKey: new string('x', 32));

        using var response = await GetProtectedAsync(forged);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task ProtectedEndpoint_ExpiredToken_Returns401_EvenWhenItsSessionIsActive()
    {
        // FS-01 §14 T-05: expiry is rejected *independent of revocation state*. The session row
        // backing this token is deliberately live and non-revoked, so token expiry is the only
        // check that can fail.
        var userId = await GetAdminUserIdAsync();
        var now = DateTimeOffset.UtcNow;
        var sessionId = await SeedSessionAsync(userId, now.AddMinutes(-120), now.AddMinutes(60));

        var expiredToken = TestTokenBuilder.Create(
            userId, sessionId, now.AddMinutes(-120), now.AddMinutes(-60));

        using var response = await GetProtectedAsync(expiredToken);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task ProtectedEndpoint_TokenWithoutJtiClaim_Returns401()
    {
        // FS-01 §14 T-14 / §11: rejected before any session lookup is attempted.
        var userId = await GetAdminUserIdAsync();
        var now = DateTimeOffset.UtcNow;

        var tokenWithoutJti = TestTokenBuilder.Create(userId, sessionId: null, now, now.AddMinutes(60));

        using var response = await GetProtectedAsync(tokenWithoutJti);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task ProtectedEndpoint_JtiWithNoMatchingSession_Returns401()
    {
        // FS-01 §14 T-15: a perfectly valid signature over a session that was never created.
        var userId = await GetAdminUserIdAsync();
        var now = DateTimeOffset.UtcNow;

        var token = TestTokenBuilder.Create(userId, Guid.NewGuid(), now, now.AddMinutes(60));

        using var response = await GetProtectedAsync(token);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task ProtectedEndpoint_SessionBelongingToAnotherUser_Returns401()
    {
        // FS-01 §14 T-16: the session exists and is active, but it was issued to a different
        // user than the one this token names — a mismatched user/session association.
        var adminUserId = await GetAdminUserIdAsync();
        var now = DateTimeOffset.UtcNow;
        var sessionId = await SeedSessionAsync(adminUserId, now, now.AddMinutes(60));

        var token = TestTokenBuilder.Create(Guid.NewGuid(), sessionId, now, now.AddMinutes(60));

        using var response = await GetProtectedAsync(token);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task ProtectedEndpoint_RevokedSession_Returns401_DespiteAnOtherwiseValidToken()
    {
        // FS-01 AC-3/§5.4 step 5, ADR-013: the token below was issued by the real login endpoint
        // and remains perfectly valid by signature and expiry — only the server-side revocation
        // flag makes it unusable. (T-11 will drive this through POST /api/v1/auth/logout; the
        // route does not exist yet, so revocation is applied to the record directly.)
        var token = await LoginAsync();

        using (var beforeRevocation = await GetProtectedAsync(token))
        {
            Assert.Equal(HttpStatusCode.OK, beforeRevocation.StatusCode);
        }

        await RevokeAllSessionsAsync();

        using var afterRevocation = await GetProtectedAsync(token);

        Assert.Equal(HttpStatusCode.Unauthorized, afterRevocation.StatusCode);
    }

    [Fact]
    public async Task ProtectedEndpoint_TokenFromAnotherIssuer_Returns401()
    {
        var userId = await GetAdminUserIdAsync();
        var now = DateTimeOffset.UtcNow;
        var sessionId = await SeedSessionAsync(userId, now, now.AddMinutes(60));

        var token = TestTokenBuilder.Create(
            userId, sessionId, now, now.AddMinutes(60), issuer: "some-other-issuer");

        using var response = await GetProtectedAsync(token);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task ProtectedEndpoint_TokenForAnotherAudience_Returns401()
    {
        var userId = await GetAdminUserIdAsync();
        var now = DateTimeOffset.UtcNow;
        var sessionId = await SeedSessionAsync(userId, now, now.AddMinutes(60));

        var token = TestTokenBuilder.Create(
            userId, sessionId, now, now.AddMinutes(60), audience: "some-other-audience");

        using var response = await GetProtectedAsync(token);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    // --- Rejection happens before controller logic (FS-01 §5.3 step 5, §12) ---

    [Fact]
    public async Task ProtectedEndpoint_RejectedRequests_NeverReachTheControllerAction()
    {
        var before = ProtectedTestController.Invocations;

        using (await GetProtectedAsync(token: null))
        using (await GetProtectedAsync("this-is-not-a-jwt"))
        using (await GetProtectedAsync(TestTokenBuilder.Create(
            await GetAdminUserIdAsync(), Guid.NewGuid(), DateTimeOffset.UtcNow,
            DateTimeOffset.UtcNow.AddMinutes(60))))
        {
        }

        Assert.Equal(before, ProtectedTestController.Invocations);
    }

    // --- Uniform application and uniform failure response (FS-01 §6, §9.3, §11) ---

    [Fact]
    public async Task UnannotatedEndpoint_IsStillProtectedByTheFallbackPolicy()
    {
        using var response = await GetProtectedAsync(token: null, route: UnannotatedRoute);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task UnannotatedEndpoint_WithValidSession_Returns200()
    {
        var token = await LoginAsync();

        using var response = await GetProtectedAsync(token, UnannotatedRoute);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }

    [Fact]
    public async Task ProtectedEndpoint_Unauthorized_UsesTheStandardErrorEnvelope()
    {
        using var response = await GetProtectedAsync(token: null);
        var envelope = await response.Content.ReadFromJsonAsync<ApiEnvelope>();

        Assert.False(envelope!.Success);
        Assert.Equal("UNAUTHORIZED", envelope.ErrorCode);
        Assert.False(string.IsNullOrWhiteSpace(envelope.Message));

        // {success, message, errorCode} — no `data` key at all on a failure (IP-01 §11).
        var body = await response.Content.ReadAsStringAsync();
        using var document = JsonDocument.Parse(body);
        Assert.False(document.RootElement.TryGetProperty("data", out _));
    }

    [Fact]
    public async Task ProtectedEndpoint_EveryRejectionReason_ProducesAnIdenticalResponse()
    {
        // FS-01 §11: an absent, malformed, expired, unknown, mismatched, or revoked session must
        // be indistinguishable to the caller — the response must never reveal which check failed.
        var userId = await GetAdminUserIdAsync();
        var now = DateTimeOffset.UtcNow;
        var otherUsersSessionId = await SeedSessionAsync(userId, now, now.AddMinutes(60));

        var revokedToken = await LoginAsync();
        await RevokeAllSessionsAsync();

        string[] tokens =
        [
            "this-is-not-a-jwt",
            TestTokenBuilder.Create(userId, sessionId: null, now, now.AddMinutes(60)),
            TestTokenBuilder.Create(userId, Guid.NewGuid(), now, now.AddMinutes(60)),
            TestTokenBuilder.Create(userId, Guid.NewGuid(), now.AddMinutes(-120), now.AddMinutes(-60)),
            TestTokenBuilder.Create(Guid.NewGuid(), otherUsersSessionId, now, now.AddMinutes(60)),
            TestTokenBuilder.Create(userId, otherUsersSessionId, now, now.AddMinutes(60),
                signingKey: new string('x', 32)),
            revokedToken,
        ];

        using var noTokenResponse = await GetProtectedAsync(token: null);
        var expectedStatus = noTokenResponse.StatusCode;
        var expectedBody = await noTokenResponse.Content.ReadAsStringAsync();

        Assert.Equal(HttpStatusCode.Unauthorized, expectedStatus);

        foreach (var token in tokens)
        {
            using var response = await GetProtectedAsync(token);
            var body = await response.Content.ReadAsStringAsync();

            Assert.Equal(expectedStatus, response.StatusCode);
            Assert.Equal(expectedBody, body);
        }
    }

    // --- The two documented exemptions remain reachable (FS-01 AC-4) ---

    [Fact]
    public async Task LoginEndpoint_RemainsAnonymouslyAccessible()
    {
        var response = await _client.PostAsJsonAsync("/api/v1/auth/login", new
        {
            credentialIdentifier = SqlServerApiHostFactory.AdminIdentifier,
            password = SqlServerApiHostFactory.AdminPassword,
        });

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }

    [Fact]
    public async Task HealthEndpoint_RemainsAnonymouslyAccessible()
    {
        using var response = await _client.GetAsync("/api/v1/health");

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }
}

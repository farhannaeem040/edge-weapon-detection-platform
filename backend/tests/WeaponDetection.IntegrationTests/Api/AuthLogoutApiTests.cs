using System;
using System.IdentityModel.Tokens.Jwt;
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

// Full HTTP-pipeline integration tests for POST /api/v1/auth/logout (IP-01 T-11), run against a
// real in-process TestServer and a real SQL Server database. Covers FS-01 AC-5 and AC-6, and test
// scenarios T-06, T-07, T-10, T-11, T-12.
//
// The whole point of this endpoint is that revocation is enforced *server-side*: every test that
// claims a token is dead after logout proves it by replaying that exact token over HTTP against a
// protected route, never by inspecting client state. ProtectedEndpointApiFactory is reused (rather
// than a new factory) precisely because it already serves such a route — ProtectedTestController —
// alongside the real auth endpoints, on an otherwise production-identical host.
//
// No test in this file prints a plaintext password or a complete access token to console/log output.
[Collection(ApiHostCollection.Name)]
public class AuthLogoutApiTests : IClassFixture<ProtectedEndpointApiFactory>, IDisposable
{
    private const string LogoutRoute = "/api/v1/auth/logout";
    private const string ProtectedRoute = "/api/v1/test/protected";

    private readonly ProtectedEndpointApiFactory _factory;
    private readonly HttpClient _client;

    public AuthLogoutApiTests(ProtectedEndpointApiFactory factory)
    {
        _factory = factory;
        _client = factory.CreateClient();
    }

    public void Dispose() => _client.Dispose();

    private sealed record ApiEnvelope(bool Success, string? Message, JsonElement? Data, string? ErrorCode);

    // A genuinely valid session, obtained exactly as the Dashboard would obtain one.
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

    private Task<HttpResponseMessage> PostLogoutAsync(string? token)
    {
        using var request = new HttpRequestMessage(HttpMethod.Post, LogoutRoute);

        if (token is not null)
        {
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        }

        return _client.SendAsync(request);
    }

    private Task<HttpResponseMessage> GetProtectedAsync(string? token)
    {
        using var request = new HttpRequestMessage(HttpMethod.Get, ProtectedRoute);

        if (token is not null)
        {
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        }

        return _client.SendAsync(request);
    }

    private static Guid SessionIdOf(string token) =>
        Guid.Parse(new JwtSecurityTokenHandler()
            .ReadJwtToken(token)
            .Claims
            .Single(c => c.Type == JwtRegisteredClaimNames.Jti)
            .Value);

    private async Task<AdminSession> GetSessionAsync(Guid sessionId)
    {
        await using var dbContext = _factory.CreateDbContext();
        return await dbContext.AdminSessions.AsNoTracking().SingleAsync(s => s.SessionId == sessionId);
    }

    private async Task<int> CountSessionsAsync()
    {
        await using var dbContext = _factory.CreateDbContext();
        return await dbContext.AdminSessions.CountAsync();
    }

    private async Task<Guid> GetAdminUserIdAsync()
    {
        await using var dbContext = _factory.CreateDbContext();
        return (await dbContext.AdminUsers.AsNoTracking().SingleAsync()).UserId;
    }

    // --- Successful logout (FS-01 §9.2, §14 T-10) ---

    [Fact]
    public async Task Logout_WithActiveSession_Returns200()
    {
        var token = await LoginAsync();

        using var response = await PostLogoutAsync(token);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }

    [Fact]
    public async Task Logout_WithActiveSession_UsesTheStandardSuccessEnvelope()
    {
        var token = await LoginAsync();

        using var response = await PostLogoutAsync(token);
        var envelope = await response.Content.ReadFromJsonAsync<ApiEnvelope>();

        Assert.True(envelope!.Success);
        Assert.Null(envelope.ErrorCode);
        Assert.False(string.IsNullOrWhiteSpace(envelope.Message));
    }

    [Fact]
    public async Task Logout_WithActiveSession_MarksThatSessionRevoked()
    {
        var token = await LoginAsync();

        using var response = await PostLogoutAsync(token);
        response.EnsureSuccessStatusCode();

        var session = await GetSessionAsync(SessionIdOf(token));

        Assert.True(session.Revoked);
    }

    [Fact]
    public async Task Logout_SuccessResponse_DoesNotEchoTheAccessToken()
    {
        var token = await LoginAsync();

        using var response = await PostLogoutAsync(token);
        var body = await response.Content.ReadAsStringAsync();

        Assert.DoesNotContain(token, body);
    }

    // --- The token is dead afterwards, server-side (FS-01 AC-5, AC-6; §14 T-06, T-07, T-11) ---

    [Fact]
    public async Task Logout_ThenTheSameTokenOnAProtectedRequest_Returns401()
    {
        // FS-01 AC-5 / §14 T-06, T-11. The token below is still perfectly valid by signature and
        // expiry — only the server-side AdminSession revocation makes it unusable.
        var token = await LoginAsync();

        using (var beforeLogout = await GetProtectedAsync(token))
        {
            Assert.Equal(HttpStatusCode.OK, beforeLogout.StatusCode);
        }

        using (var logout = await PostLogoutAsync(token))
        {
            Assert.Equal(HttpStatusCode.OK, logout.StatusCode);
        }

        using var afterLogout = await GetProtectedAsync(token);

        Assert.Equal(HttpStatusCode.Unauthorized, afterLogout.StatusCode);
    }

    [Fact]
    public async Task Logout_TokenCopiedBeforeLogout_IsAlsoRejectedAfterwards()
    {
        // FS-01 AC-6 / §14 T-07: the decisive test for ADR-013. This copy is captured *before*
        // logout and never passes through any client-side discard — if revocation were enforced by
        // the Dashboard deleting its own token, this request would still succeed. It must not.
        var token = await LoginAsync();

        // Captured before logout and held independently of whatever the client does with its own
        // copy afterwards — which is exactly the threat AC-6 describes.
        var copyMadeBeforeLogout = token;

        using (var logout = await PostLogoutAsync(token))
        {
            logout.EnsureSuccessStatusCode();
        }

        using var response = await GetProtectedAsync(copyMadeBeforeLogout);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task Logout_RevokesOnlyTheSessionNamedByThePresentedToken()
    {
        // Two independent sessions for the one Admin. Logging out of the first must not disturb
        // the second — revocation is keyed by the presented token's own `jti`.
        var firstToken = await LoginAsync();
        var secondToken = await LoginAsync();

        using (var logout = await PostLogoutAsync(firstToken))
        {
            logout.EnsureSuccessStatusCode();
        }

        using var firstAfter = await GetProtectedAsync(firstToken);
        using var secondAfter = await GetProtectedAsync(secondToken);

        Assert.Equal(HttpStatusCode.Unauthorized, firstAfter.StatusCode);
        Assert.Equal(HttpStatusCode.OK, secondAfter.StatusCode);
    }

    // --- Second logout with the already-revoked token (FS-01 §5.4 step 6, §12, §14 T-12) ---

    [Fact]
    public async Task SecondLogout_WithTheAlreadyRevokedToken_Returns401()
    {
        var token = await LoginAsync();

        using (var first = await PostLogoutAsync(token))
        {
            Assert.Equal(HttpStatusCode.OK, first.StatusCode);
        }

        using var second = await PostLogoutAsync(token);

        Assert.Equal(HttpStatusCode.Unauthorized, second.StatusCode);
    }

    [Fact]
    public async Task SecondLogout_CreatesNoNewSessionAndDoesNotReactivateTheRevokedOne()
    {
        var token = await LoginAsync();
        var sessionId = SessionIdOf(token);

        using (var first = await PostLogoutAsync(token))
        {
            first.EnsureSuccessStatusCode();
        }

        var sessionCountAfterFirstLogout = await CountSessionsAsync();

        using (var second = await PostLogoutAsync(token))
        {
            Assert.Equal(HttpStatusCode.Unauthorized, second.StatusCode);
        }

        var session = await GetSessionAsync(sessionId);

        Assert.Equal(sessionCountAfterFirstLogout, await CountSessionsAsync());
        Assert.True(session.Revoked);
    }

    // --- Logout is itself a protected endpoint (FS-01 §9.2) ---

    [Fact]
    public async Task Logout_NoAuthorizationHeader_Returns401()
    {
        using var response = await PostLogoutAsync(token: null);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task Logout_MalformedToken_Returns401()
    {
        using var response = await PostLogoutAsync("this-is-not-a-jwt");

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task Logout_ExpiredToken_Returns401()
    {
        var userId = await GetAdminUserIdAsync();
        var now = DateTimeOffset.UtcNow;

        var expiredToken = TestTokenBuilder.Create(
            userId, Guid.NewGuid(), now.AddMinutes(-120), now.AddMinutes(-60));

        using var response = await PostLogoutAsync(expiredToken);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task Logout_JtiWithNoMatchingSession_Returns401()
    {
        var userId = await GetAdminUserIdAsync();
        var now = DateTimeOffset.UtcNow;

        var token = TestTokenBuilder.Create(userId, Guid.NewGuid(), now, now.AddMinutes(60));

        using var response = await PostLogoutAsync(token);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task Logout_TokenSignedWithAnotherKey_Returns401_AndRevokesNothing()
    {
        // A forged token naming a real, active session. The signature check must reject it before
        // anything is revoked — otherwise an unauthenticated caller could log the Admin out.
        var victimToken = await LoginAsync();
        var sessionId = SessionIdOf(victimToken);
        var userId = await GetAdminUserIdAsync();
        var now = DateTimeOffset.UtcNow;

        var forged = TestTokenBuilder.Create(
            userId, sessionId, now, now.AddMinutes(60), signingKey: new string('x', 32));

        using var response = await PostLogoutAsync(forged);
        var session = await GetSessionAsync(sessionId);

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
        Assert.False(session.Revoked);
    }

    // --- Uniform failure response (FS-01 §9.3, §11) ---

    [Fact]
    public async Task Logout_EveryRejectionReason_IsIndistinguishableFromAnyOtherUnauthorized()
    {
        // FS-01 §11: a caller must not be able to tell *why* it was refused — and, specifically,
        // must not be able to tell a rejected logout apart from any other rejected request. The
        // controller's own defensive 401 shares one envelope definition with the middleware's
        // (AuthenticationFailure), which is what makes these bodies byte-identical.
        var userId = await GetAdminUserIdAsync();
        var now = DateTimeOffset.UtcNow;

        var revokedToken = await LoginAsync();
        using (var logout = await PostLogoutAsync(revokedToken))
        {
            logout.EnsureSuccessStatusCode();
        }

        string?[] tokens =
        [
            null,
            "this-is-not-a-jwt",
            TestTokenBuilder.Create(userId, sessionId: null, now, now.AddMinutes(60)),
            TestTokenBuilder.Create(userId, Guid.NewGuid(), now, now.AddMinutes(60)),
            TestTokenBuilder.Create(userId, Guid.NewGuid(), now.AddMinutes(-120), now.AddMinutes(-60)),
            revokedToken,
        ];

        using var protectedBaseline = await GetProtectedAsync(token: null);
        var expectedStatus = protectedBaseline.StatusCode;
        var expectedBody = await protectedBaseline.Content.ReadAsStringAsync();

        Assert.Equal(HttpStatusCode.Unauthorized, expectedStatus);

        foreach (var token in tokens)
        {
            using var response = await PostLogoutAsync(token);
            var body = await response.Content.ReadAsStringAsync();

            Assert.Equal(expectedStatus, response.StatusCode);
            Assert.Equal(expectedBody, body);
        }
    }

    [Fact]
    public async Task Logout_Unauthorized_UsesTheStandardErrorEnvelopeWithNoDataKey()
    {
        using var response = await PostLogoutAsync(token: null);
        var envelope = await response.Content.ReadFromJsonAsync<ApiEnvelope>();

        Assert.False(envelope!.Success);
        Assert.Equal("UNAUTHORIZED", envelope.ErrorCode);

        // {success, message, errorCode} — no `data` key at all on a failure (IP-01 §11).
        var body = await response.Content.ReadAsStringAsync();
        using var document = JsonDocument.Parse(body);
        Assert.False(document.RootElement.TryGetProperty("data", out _));
    }
}

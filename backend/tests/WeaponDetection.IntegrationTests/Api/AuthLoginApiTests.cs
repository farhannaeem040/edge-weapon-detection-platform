using System;
using System.Collections.Generic;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.EntityFrameworkCore;
using WeaponDetection.IntegrationTests.Api;
using Xunit;

namespace WeaponDetection.IntegrationTests.Api;

// Full HTTP-endpoint integration tests for POST /api/v1/auth/login (IP-01 §9/T-09), run against
// a real in-process TestServer + real SQL Server database — not a mocked HTTP pipeline.
//
// No test in this file prints a plaintext password or complete access token to console/log
// output; assertions compare values in-memory only, and one test explicitly asserts the
// password never appears in a response body.
[Collection(ApiHostCollection.Name)]
public class AuthLoginApiTests : IClassFixture<AuthLoginApiFactory>, IDisposable
{
    private readonly AuthLoginApiFactory _factory;
    private readonly HttpClient _client;

    public AuthLoginApiTests(AuthLoginApiFactory factory)
    {
        _factory = factory;
        _client = factory.CreateClient();
    }

    public void Dispose() => _client.Dispose();

    private sealed record ApiEnvelope(bool Success, string? Message, JsonElement? Data, string? ErrorCode);

    private static async Task<(HttpResponseMessage Response, ApiEnvelope Envelope)> PostLoginAsync(
        HttpClient client, object body)
    {
        var response = await client.PostAsJsonAsync("/api/v1/auth/login", body);
        var envelope = await response.Content.ReadFromJsonAsync<ApiEnvelope>();
        return (response, envelope!);
    }

    private async Task<int> CountSessionsAsync()
    {
        await using var dbContext = _factory.CreateDbContext();
        return await dbContext.AdminSessions.CountAsync();
    }

    [Fact]
    public async Task Login_ValidCredentials_Returns200()
    {
        var (response, _) = await PostLoginAsync(_client, new
        {
            credentialIdentifier = AuthLoginApiFactory.AdminIdentifier,
            password = AuthLoginApiFactory.AdminPassword,
        });

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }

    [Fact]
    public async Task Login_ValidCredentials_UsesApprovedEnvelope()
    {
        var (_, envelope) = await PostLoginAsync(_client, new
        {
            credentialIdentifier = AuthLoginApiFactory.AdminIdentifier,
            password = AuthLoginApiFactory.AdminPassword,
        });

        Assert.True(envelope.Success);
        Assert.Null(envelope.ErrorCode);
        Assert.NotNull(envelope.Data);
    }

    [Fact]
    public async Task Login_ValidCredentials_ReturnsNonEmptyAccessToken()
    {
        var (_, envelope) = await PostLoginAsync(_client, new
        {
            credentialIdentifier = AuthLoginApiFactory.AdminIdentifier,
            password = AuthLoginApiFactory.AdminPassword,
        });

        var token = envelope.Data!.Value.GetProperty("token").GetString();

        Assert.False(string.IsNullOrWhiteSpace(token));
    }

    [Fact]
    public async Task Login_ValidCredentials_PersistsAnAdminSession()
    {
        var before = await CountSessionsAsync();

        await PostLoginAsync(_client, new
        {
            credentialIdentifier = AuthLoginApiFactory.AdminIdentifier,
            password = AuthLoginApiFactory.AdminPassword,
        });

        var after = await CountSessionsAsync();

        Assert.Equal(before + 1, after);
    }

    [Fact]
    public async Task Login_WrongPassword_Returns401()
    {
        var (response, _) = await PostLoginAsync(_client, new
        {
            credentialIdentifier = AuthLoginApiFactory.AdminIdentifier,
            password = "definitely-not-the-password",
        });

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task Login_UnknownIdentifier_ReturnsSame401ResponseBodyAsWrongPassword()
    {
        var (wrongPasswordResponse, wrongPasswordEnvelope) = await PostLoginAsync(_client, new
        {
            credentialIdentifier = AuthLoginApiFactory.AdminIdentifier,
            password = "definitely-not-the-password",
        });

        var (unknownUserResponse, unknownUserEnvelope) = await PostLoginAsync(_client, new
        {
            credentialIdentifier = $"no-such-admin-{Guid.NewGuid()}",
            password = "any-password-at-all",
        });

        Assert.Equal(wrongPasswordResponse.StatusCode, unknownUserResponse.StatusCode);
        Assert.Equal(wrongPasswordEnvelope.ErrorCode, unknownUserEnvelope.ErrorCode);
        Assert.Equal(wrongPasswordEnvelope.Message, unknownUserEnvelope.Message);
        Assert.Equal("INVALID_CREDENTIALS", unknownUserEnvelope.ErrorCode);
    }

    [Fact]
    public async Task Login_InvalidCredentials_CreatesNoSession()
    {
        var before = await CountSessionsAsync();

        await PostLoginAsync(_client, new
        {
            credentialIdentifier = $"no-such-admin-{Guid.NewGuid()}",
            password = "wrong-password",
        });

        var after = await CountSessionsAsync();

        Assert.Equal(before, after);
    }

    [Fact]
    public async Task Login_MissingRequestBody_Returns400()
    {
        var content = new StringContent(string.Empty, Encoding.UTF8, "application/json");
        var response = await _client.PostAsync("/api/v1/auth/login", content);

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    [Fact]
    public async Task Login_MissingCredentialIdentifier_Returns400()
    {
        var (response, _) = await PostLoginAsync(_client, new { password = AuthLoginApiFactory.AdminPassword });

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    [Fact]
    public async Task Login_BlankCredentialIdentifier_Returns400()
    {
        var (response, _) = await PostLoginAsync(_client, new
        {
            credentialIdentifier = "   ",
            password = AuthLoginApiFactory.AdminPassword,
        });

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    [Fact]
    public async Task Login_MissingPassword_Returns400()
    {
        var (response, _) = await PostLoginAsync(
            _client, new { credentialIdentifier = AuthLoginApiFactory.AdminIdentifier });

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    [Fact]
    public async Task Login_BlankPassword_Returns400()
    {
        var (response, _) = await PostLoginAsync(_client, new
        {
            credentialIdentifier = AuthLoginApiFactory.AdminIdentifier,
            password = "   ",
        });

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    [Fact]
    public async Task Login_MalformedJson_Returns400()
    {
        var content = new StringContent("{ not valid json ", Encoding.UTF8, "application/json");
        var response = await _client.PostAsync("/api/v1/auth/login", content);

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    [Fact]
    public async Task Login_BlankCredentialIdentifier_ValidationResponseDoesNotContainPassword()
    {
        const string password = "some-plaintext-password-value";

        var response = await _client.PostAsJsonAsync("/api/v1/auth/login", new
        {
            credentialIdentifier = "   ",
            password,
        });

        var body = await response.Content.ReadAsStringAsync();

        Assert.DoesNotContain(password, body);
    }

    [Fact]
    public async Task Login_AnyOutcome_ResponseNeverContainsPlaintextPassword()
    {
        var response = await _client.PostAsJsonAsync("/api/v1/auth/login", new
        {
            credentialIdentifier = AuthLoginApiFactory.AdminIdentifier,
            password = AuthLoginApiFactory.AdminPassword,
        });

        var body = await response.Content.ReadAsStringAsync();

        Assert.DoesNotContain(AuthLoginApiFactory.AdminPassword, body);
    }

    [Fact]
    public async Task Login_WrongPassword_ResponseDoesNotExposeStackTraceOrSqlDetails()
    {
        var response = await _client.PostAsJsonAsync("/api/v1/auth/login", new
        {
            credentialIdentifier = AuthLoginApiFactory.AdminIdentifier,
            password = "wrong-password",
        });

        var body = await response.Content.ReadAsStringAsync();

        Assert.DoesNotContain("Exception", body);
        Assert.DoesNotContain("StackTrace", body);
        Assert.DoesNotContain("SqlException", body);
        Assert.DoesNotContain("Server=", body);
    }

    [Fact]
    public async Task HealthEndpoint_StillReturns200()
    {
        var response = await _client.GetAsync("/api/v1/health");

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }

    [Fact]
    public async Task Login_EndpointIsAnonymouslyAccessible_NoBearerTokenRequired()
    {
        // FS-01 AC-4. No Authorization header attached at all — the request must still be
        // evaluated by the endpoint itself, not rejected upstream by the bearer-authentication
        // middleware that T-10 applies to every other endpoint by default.
        var (response, _) = await PostLoginAsync(_client, new
        {
            credentialIdentifier = AuthLoginApiFactory.AdminIdentifier,
            password = AuthLoginApiFactory.AdminPassword,
        });

        Assert.NotEqual(HttpStatusCode.Unauthorized, response.StatusCode);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }
}

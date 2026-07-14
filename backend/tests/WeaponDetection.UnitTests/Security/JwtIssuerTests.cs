using System.IdentityModel.Tokens.Jwt;
using System.Linq;
using System.Text;
using Microsoft.Extensions.Options;
using Microsoft.IdentityModel.Tokens;
using WeaponDetection.Infrastructure.Security;
using Xunit;

namespace WeaponDetection.UnitTests.Security;

// No test in this file prints the signing key to console/log output — assertions compare
// values in-memory only, and one test explicitly asserts the key never appears in the
// serialized token.
public class JwtIssuerTests
{
    private const string Issuer = "test-issuer";
    private const string Audience = "test-audience";
    private const string SigningKey = "0123456789abcdef0123456789abcdef"; // 33 UTF-8 bytes, >= 32-byte minimum
    private const int LifetimeMinutes = 30;

    private static readonly DateTimeOffset FixedNow = new(2026, 1, 1, 12, 0, 0, TimeSpan.Zero);

    // Deterministic clock — avoids uncontrolled DateTimeOffset.UtcNow in timestamp assertions.
    private sealed class FixedTimeProvider(DateTimeOffset now) : TimeProvider
    {
        public override DateTimeOffset GetUtcNow() => now;
    }

    private static JwtIssuer CreateIssuer(
        string issuer = Issuer,
        string audience = Audience,
        string signingKey = SigningKey,
        int lifetimeMinutes = LifetimeMinutes,
        DateTimeOffset? now = null)
    {
        var options = Options.Create(new JwtOptions
        {
            Issuer = issuer,
            Audience = audience,
            SigningKey = signingKey,
            AccessTokenLifetimeMinutes = lifetimeMinutes,
        });

        return new JwtIssuer(options, new FixedTimeProvider(now ?? FixedNow));
    }

    private static TokenValidationParameters ValidationParameters(
        string issuer = Issuer, string audience = Audience, string signingKey = SigningKey) => new()
    {
        ValidateIssuer = true,
        ValidIssuer = issuer,
        ValidateAudience = true,
        ValidAudience = audience,
        ValidateIssuerSigningKey = true,
        IssuerSigningKey = new SymmetricSecurityKey(Encoding.UTF8.GetBytes(signingKey)),
        // Lifetime validation is deliberately disabled here: these tests use a fixed
        // (deterministic) issuance time in the past relative to the real system clock, and
        // exercise issuer/audience/signing-key validation specifically, not expiry — expiry
        // itself is covered by the dedicated iat/exp assertions above.
        ValidateLifetime = false,
    };

    [Fact]
    public void Issue_ValidUserId_ReturnsNonEmptyAccessToken()
    {
        var issuer = CreateIssuer();

        var result = issuer.Issue(Guid.NewGuid());

        Assert.False(string.IsNullOrWhiteSpace(result.AccessToken));
    }

    [Fact]
    public void Issue_ValidUserId_TokenIsSignedWithConfiguredAlgorithm()
    {
        var issuer = CreateIssuer();

        var result = issuer.Issue(Guid.NewGuid());
        var token = new JwtSecurityTokenHandler().ReadJwtToken(result.AccessToken);

        Assert.Equal(SecurityAlgorithms.HmacSha256, token.SignatureAlgorithm);
    }

    [Fact]
    public void Issue_ValidUserId_TokenContainsCorrectSubClaim()
    {
        var userId = Guid.NewGuid();
        var issuer = CreateIssuer();

        var result = issuer.Issue(userId);
        var token = new JwtSecurityTokenHandler().ReadJwtToken(result.AccessToken);

        Assert.Equal(userId.ToString(), token.Subject);
    }

    [Fact]
    public void Issue_ValidUserId_TokenContainsNonEmptyJti()
    {
        var issuer = CreateIssuer();

        var result = issuer.Issue(Guid.NewGuid());
        var token = new JwtSecurityTokenHandler().ReadJwtToken(result.AccessToken);
        var jti = token.Claims.Single(c => c.Type == JwtRegisteredClaimNames.Jti).Value;

        Assert.False(string.IsNullOrWhiteSpace(jti));
    }

    [Fact]
    public void Issue_ValidUserId_ReturnedSessionIdMatchesEncodedJti()
    {
        var issuer = CreateIssuer();

        var result = issuer.Issue(Guid.NewGuid());
        var token = new JwtSecurityTokenHandler().ReadJwtToken(result.AccessToken);
        var jti = token.Claims.Single(c => c.Type == JwtRegisteredClaimNames.Jti).Value;

        Assert.Equal(result.SessionId.ToString(), jti);
    }

    [Fact]
    public void Issue_ValidUserId_TokenContainsValidIatAndExp()
    {
        var issuer = CreateIssuer(now: FixedNow);

        var result = issuer.Issue(Guid.NewGuid());
        var token = new JwtSecurityTokenHandler().ReadJwtToken(result.AccessToken);

        Assert.Equal(FixedNow.UtcDateTime, token.IssuedAt);
        Assert.Equal(FixedNow.UtcDateTime.AddMinutes(LifetimeMinutes), token.ValidTo);
    }

    [Fact]
    public void Issue_ValidUserId_ReturnedTimestampsMatchEncodedTimestamps()
    {
        var issuer = CreateIssuer(now: FixedNow);

        var result = issuer.Issue(Guid.NewGuid());
        var token = new JwtSecurityTokenHandler().ReadJwtToken(result.AccessToken);

        Assert.Equal(token.IssuedAt, result.IssuedAt.UtcDateTime);
        Assert.Equal(token.ValidTo, result.ExpiresAt.UtcDateTime);
    }

    [Fact]
    public void Issue_ValidUserId_ExpiryEqualsConfiguredLifetime()
    {
        var issuer = CreateIssuer(now: FixedNow, lifetimeMinutes: 45);

        var result = issuer.Issue(Guid.NewGuid());

        Assert.Equal(FixedNow.AddMinutes(45), result.ExpiresAt);
    }

    [Fact]
    public void Issue_CalledTwice_ProducesDifferentJti()
    {
        var issuer = CreateIssuer();

        var first = issuer.Issue(Guid.NewGuid());
        var second = issuer.Issue(Guid.NewGuid());

        Assert.NotEqual(first.SessionId, second.SessionId);
    }

    [Fact]
    public void Issue_EmptyUserId_Throws()
    {
        var issuer = CreateIssuer();

        Assert.Throws<ArgumentException>(() => issuer.Issue(Guid.Empty));
    }

    [Fact]
    public void Issue_ValidUserId_TokenValidatesWithConfiguredIssuerAudienceAndSigningKey()
    {
        var issuer = CreateIssuer();
        var result = issuer.Issue(Guid.NewGuid());

        var principal = new JwtSecurityTokenHandler().ValidateToken(
            result.AccessToken, ValidationParameters(), out _);

        Assert.NotNull(principal);
    }

    [Fact]
    public void Issue_ValidUserId_ValidationFails_WithWrongSigningKey()
    {
        var issuer = CreateIssuer();
        var result = issuer.Issue(Guid.NewGuid());

        Assert.ThrowsAny<SecurityTokenException>(() =>
            new JwtSecurityTokenHandler().ValidateToken(
                result.AccessToken, ValidationParameters(signingKey: new string('x', 32)), out _));
    }

    [Fact]
    public void Issue_ValidUserId_ValidationFails_WithWrongIssuer()
    {
        var issuer = CreateIssuer();
        var result = issuer.Issue(Guid.NewGuid());

        Assert.Throws<SecurityTokenInvalidIssuerException>(() =>
            new JwtSecurityTokenHandler().ValidateToken(
                result.AccessToken, ValidationParameters(issuer: "wrong-issuer"), out _));
    }

    [Fact]
    public void Issue_ValidUserId_ValidationFails_WithWrongAudience()
    {
        var issuer = CreateIssuer();
        var result = issuer.Issue(Guid.NewGuid());

        Assert.Throws<SecurityTokenInvalidAudienceException>(() =>
            new JwtSecurityTokenHandler().ValidateToken(
                result.AccessToken, ValidationParameters(audience: "wrong-audience"), out _));
    }

    [Fact]
    public void Issue_EncodedToken_DoesNotContainSigningKeyLiterally()
    {
        var issuer = CreateIssuer();

        var result = issuer.Issue(Guid.NewGuid());

        Assert.DoesNotContain(SigningKey, result.AccessToken);
    }
}

using System.Linq;
using WeaponDetection.Infrastructure.Security;
using Xunit;

namespace WeaponDetection.UnitTests.Security;

// No test in this file prints a real/production signing key — the deliberately "leaked" value
// used below is a test literal, and the final test asserts it never appears in a failure
// message.
public class JwtOptionsValidatorTests
{
    private static JwtOptions ValidOptions() => new()
    {
        Issuer = "test-issuer",
        Audience = "test-audience",
        SigningKey = new string('k', 32),
        AccessTokenLifetimeMinutes = 60,
    };

    [Fact]
    public void Validate_ValidOptions_Succeeds()
    {
        var result = new JwtOptionsValidator().Validate(null, ValidOptions());

        Assert.True(result.Succeeded);
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    [InlineData(null)]
    public void Validate_MissingOrBlankIssuer_Fails(string? issuer)
    {
        var options = ValidOptions();
        options.Issuer = issuer!;

        var result = new JwtOptionsValidator().Validate(null, options);

        Assert.True(result.Failed);
        Assert.Contains(result.Failures!, f => f.Contains("Jwt:Issuer"));
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    [InlineData(null)]
    public void Validate_MissingOrBlankAudience_Fails(string? audience)
    {
        var options = ValidOptions();
        options.Audience = audience!;

        var result = new JwtOptionsValidator().Validate(null, options);

        Assert.True(result.Failed);
        Assert.Contains(result.Failures!, f => f.Contains("Jwt:Audience"));
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    [InlineData(null)]
    public void Validate_MissingOrBlankSigningKey_Fails(string? signingKey)
    {
        var options = ValidOptions();
        options.SigningKey = signingKey!;

        var result = new JwtOptionsValidator().Validate(null, options);

        Assert.True(result.Failed);
        Assert.Contains(result.Failures!, f => f.Contains("Jwt:SigningKey"));
    }

    [Fact]
    public void Validate_SigningKeyBelowMinimumStrength_Fails()
    {
        var options = ValidOptions();
        options.SigningKey = new string('k', 16); // below the 32-byte minimum

        var result = new JwtOptionsValidator().Validate(null, options);

        Assert.True(result.Failed);
        Assert.Contains(result.Failures!, f => f.Contains("Jwt:SigningKey"));
    }

    [Theory]
    [InlineData(0)]
    [InlineData(-5)]
    [InlineData(24 * 60 + 1)]
    public void Validate_InvalidLifetime_Fails(int lifetimeMinutes)
    {
        var options = ValidOptions();
        options.AccessTokenLifetimeMinutes = lifetimeMinutes;

        var result = new JwtOptionsValidator().Validate(null, options);

        Assert.True(result.Failed);
        Assert.Contains(result.Failures!, f => f.Contains("Jwt:AccessTokenLifetimeMinutes"));
    }

    [Fact]
    public void Validate_FailureMessages_DoNotContainActualSigningKeyValue()
    {
        var options = ValidOptions();
        options.SigningKey = "super-secret-signing-key-value-do-not-log-me!!";
        options.AccessTokenLifetimeMinutes = -1; // force a failure unrelated to the key itself

        var result = new JwtOptionsValidator().Validate(null, options);

        Assert.True(result.Failed);
        Assert.DoesNotContain(result.Failures!, f => f.Contains(options.SigningKey));
    }
}

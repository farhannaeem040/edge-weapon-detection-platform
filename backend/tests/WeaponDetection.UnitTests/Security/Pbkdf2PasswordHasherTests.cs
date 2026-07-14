using WeaponDetection.Application.Interfaces;
using WeaponDetection.Infrastructure.Security;
using Xunit;

namespace WeaponDetection.UnitTests.Security;

// No test in this file prints a password or hash to console/log output — assertions compare
// values in-memory only (xUnit's own failure-message rendering of raw strings is an accepted,
// standard testing-tool behavior, not "normal" application logging).
public class Pbkdf2PasswordHasherTests
{
    private const string TestPassword = "Correct-Horse-Battery-Staple-1";
    private const string WrongPassword = "not-the-right-password";

    private static Pbkdf2PasswordHasher CreateHasher() => new();

    [Fact]
    public void Hash_ValidPassword_ReturnsNonEmptyStoredHash()
    {
        var hasher = CreateHasher();

        var hashed = hasher.Hash(TestPassword);

        Assert.False(string.IsNullOrWhiteSpace(hashed));
    }

    [Fact]
    public void Hash_ValidPassword_StoredHashIsNotEqualToPlaintext()
    {
        var hasher = CreateHasher();

        var hashed = hasher.Hash(TestPassword);

        Assert.NotEqual(TestPassword, hashed);
    }

    [Fact]
    public void Hash_SamePasswordTwice_ProducesDifferentlySaltedHashes()
    {
        var hasher = CreateHasher();

        var first = hasher.Hash(TestPassword);
        var second = hasher.Hash(TestPassword);

        Assert.NotEqual(first, second);
    }

    [Fact]
    public void Verify_BothIndependentlyGeneratedHashes_VerifyTheCorrectPassword()
    {
        var hasher = CreateHasher();
        var first = hasher.Hash(TestPassword);
        var second = hasher.Hash(TestPassword);

        Assert.Equal(PasswordVerificationResult.Success, hasher.Verify(TestPassword, first));
        Assert.Equal(PasswordVerificationResult.Success, hasher.Verify(TestPassword, second));
    }

    [Fact]
    public void Verify_IncorrectPassword_Fails()
    {
        var hasher = CreateHasher();
        var hashed = hasher.Hash(TestPassword);

        var result = hasher.Verify(WrongPassword, hashed);

        Assert.Equal(PasswordVerificationResult.Failed, result);
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    public void Hash_EmptyOrWhitespacePassword_Throws(string password)
    {
        var hasher = CreateHasher();

        Assert.Throws<ArgumentException>(() => hasher.Hash(password));
    }

    [Fact]
    public void Hash_NullPassword_Throws()
    {
        var hasher = CreateHasher();

        Assert.Throws<ArgumentException>(() => hasher.Hash(null!));
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    public void Verify_EmptyOrWhitespacePassword_Throws(string password)
    {
        var hasher = CreateHasher();
        var hashed = hasher.Hash(TestPassword);

        Assert.Throws<ArgumentException>(() => hasher.Verify(password, hashed));
    }

    [Fact]
    public void Verify_NullPassword_Throws()
    {
        var hasher = CreateHasher();
        var hashed = hasher.Hash(TestPassword);

        Assert.Throws<ArgumentException>(() => hasher.Verify(null!, hashed));
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("   ")]
    [InlineData("not-base64!!!")]
    [InlineData("dGhpcyBpcyBub3QgYSB2YWxpZCBoYXNoIHBheWxvYWQ=")] // valid base64, wrong length/version
    public void Verify_MalformedOrUnsupportedStoredHash_FailsSafely(string? storedHash)
    {
        var hasher = CreateHasher();

        var result = hasher.Verify(TestPassword, storedHash!);

        Assert.Equal(PasswordVerificationResult.Failed, result);
    }
}

using WeaponDetection.Application.Interfaces;
using WeaponDetection.Domain;
using WeaponDetection.Infrastructure.Security;
using Xunit;

namespace WeaponDetection.UnitTests.Security;

// No test in this file prints an Activation Key, its secret half, or a secret hash to console/log
// output — assertions compare values in-memory only (xUnit's own failure-message rendering of raw
// strings is an accepted testing-tool behavior, not application logging). The generator is exercised
// with its real IPasswordHasher collaborator rather than a mock, since the secret round-trip is the
// behavior under test (FS-02 §1.4, AC-2, AC-14).
public class ActivationKeyGeneratorTests
{
    private static ActivationKeyGenerator CreateGenerator() => new(new Pbkdf2PasswordHasher());

    private static (string KeyId, string Secret) SplitPlaintext(string plaintextKey)
    {
        var parts = plaintextKey.Split(ActivationKeyGenerator.Delimiter);
        Assert.Equal(2, parts.Length);
        return (parts[0], parts[1]);
    }

    [Fact]
    public void Generate_ProducesNonEmptyKeyIdSecretHashAndPlaintextKey()
    {
        var generator = CreateGenerator();

        var generated = generator.Generate();

        Assert.False(string.IsNullOrWhiteSpace(generated.KeyId));
        Assert.False(string.IsNullOrWhiteSpace(generated.SecretHash));
        Assert.False(string.IsNullOrWhiteSpace(generated.PlaintextKey));
    }

    [Fact]
    public void Generate_PlaintextKeyIsKeyIdDelimiterThenASecretThatVerifies()
    {
        var generator = CreateGenerator();

        var generated = generator.Generate();
        var (keyId, secret) = SplitPlaintext(generated.PlaintextKey);

        // The keyId half of the disclosed plaintext is exactly the stored, indexed lookup value...
        Assert.Equal(generated.KeyId, keyId);
        // ...and the secret half verifies against the stored hash (the two halves belong together).
        Assert.True(generator.VerifySecret(secret, generated.SecretHash));
    }

    [Fact]
    public void Generate_KeyIdFitsActivationKeyEntityMaxLength()
    {
        var generator = CreateGenerator();

        var generated = generator.Generate();

        Assert.True(generated.KeyId.Length <= ActivationKey.ActivationKeyIdMaxLength);
    }

    [Fact]
    public void Generate_NeitherHalfContainsTheDelimiter_SoTheKeySplitsIntoExactlyTwoParts()
    {
        var generator = CreateGenerator();

        var generated = generator.Generate();
        var (keyId, secret) = SplitPlaintext(generated.PlaintextKey);

        Assert.DoesNotContain(ActivationKeyGenerator.Delimiter, keyId);
        Assert.DoesNotContain(ActivationKeyGenerator.Delimiter, secret);
    }

    [Fact]
    public void Generate_SecretHashIsNotThePlaintextSecret()
    {
        var generator = CreateGenerator();

        var generated = generator.Generate();
        var (_, secret) = SplitPlaintext(generated.PlaintextKey);

        Assert.NotEqual(secret, generated.SecretHash);
    }

    [Fact]
    public void Generate_ManyInvocations_ProduceUniqueKeyIdsSecretsAndPlaintextKeys()
    {
        var generator = CreateGenerator();
        const int iterations = 1_000;

        var keyIds = new HashSet<string>();
        var secrets = new HashSet<string>();
        var plaintextKeys = new HashSet<string>();

        for (var i = 0; i < iterations; i++)
        {
            var generated = generator.Generate();
            var (keyId, secret) = SplitPlaintext(generated.PlaintextKey);

            Assert.True(keyIds.Add(keyId), "keyId collided across invocations.");
            Assert.True(secrets.Add(secret), "secret collided across invocations.");
            Assert.True(plaintextKeys.Add(generated.PlaintextKey), "plaintext key collided across invocations.");
        }
    }

    [Fact]
    public void Generate_SamePlaintextSecretNeverProducesTheSameStoredHash()
    {
        var generator = CreateGenerator();

        var first = generator.Generate();
        var second = generator.Generate();

        // Independent salting means two records never share a stored hash, even if by astronomically
        // unlikely chance their secrets matched.
        Assert.NotEqual(first.SecretHash, second.SecretHash);
    }

    [Fact]
    public void VerifySecret_CorrectSecret_ReturnsTrue()
    {
        var generator = CreateGenerator();
        var generated = generator.Generate();
        var (_, secret) = SplitPlaintext(generated.PlaintextKey);

        Assert.True(generator.VerifySecret(secret, generated.SecretHash));
    }

    [Fact]
    public void VerifySecret_SecretFromADifferentKey_ReturnsFalse()
    {
        var generator = CreateGenerator();
        var first = generator.Generate();
        var second = generator.Generate();
        var (_, secondSecret) = SplitPlaintext(second.PlaintextKey);

        // A secret is only valid against its own key's stored hash.
        Assert.False(generator.VerifySecret(secondSecret, first.SecretHash));
    }

    [Fact]
    public void VerifySecret_WholeKeyIdDotSecretValueInsteadOfJustTheSecret_ReturnsFalse()
    {
        var generator = CreateGenerator();
        var generated = generator.Generate();

        // The caller must split first; passing the complete `keyId.secret` must not verify.
        Assert.False(generator.VerifySecret(generated.PlaintextKey, generated.SecretHash));
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("   ")]
    public void VerifySecret_MissingOrBlankSecret_FailsSafelyWithoutThrowing(string? secret)
    {
        var generator = CreateGenerator();
        var generated = generator.Generate();

        Assert.False(generator.VerifySecret(secret!, generated.SecretHash));
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("   ")]
    [InlineData("not-a-real-hash")]
    public void VerifySecret_MissingOrMalformedStoredHash_FailsSafelyWithoutThrowing(string? secretHash)
    {
        var generator = CreateGenerator();
        var generated = generator.Generate();
        var (_, secret) = SplitPlaintext(generated.PlaintextKey);

        Assert.False(generator.VerifySecret(secret, secretHash!));
    }
}

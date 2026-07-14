using System;
using WeaponDetection.Domain;
using Xunit;

namespace WeaponDetection.UnitTests.Domain;

// ActivationKey invariants (FS-02 §1.4, §5.3, §5.5, §5.7).
//
// The hash values below are obvious placeholders standing in for the output of the hashing utility
// (T-04/T-14). No real key, secret, or hash appears in a committed test.
public class ActivationKeyTests
{
    private const string KeyId = "placeholder-key-id";
    private const string SecretHash = "placeholder-secret-hash";

    private static ActivationKey CreateKey() =>
        new(KeyId, Guid.NewGuid(), SecretHash);

    [Fact]
    public void Constructor_StoresTheKeyIdAndSecretHash_Unconsumed()
    {
        var deviceRecordId = Guid.NewGuid();

        var key = new ActivationKey(KeyId, deviceRecordId, SecretHash);

        Assert.Equal(KeyId, key.ActivationKeyId);
        Assert.Equal(deviceRecordId, key.DeviceRecordId);
        Assert.Equal(SecretHash, key.SecretHash);

        // A newly issued key is always usable (FS-02 §5.1 step 5).
        Assert.Equal(ActivationKeyStatus.Unconsumed, key.Status);
    }

    [Fact]
    public void Constructor_TrimsTheKeyId()
    {
        var key = new ActivationKey($"  {KeyId}  ", Guid.NewGuid(), SecretHash);

        Assert.Equal(KeyId, key.ActivationKeyId);
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    [InlineData(null)]
    public void Constructor_MissingKeyId_IsRejected(string? activationKeyId)
    {
        Assert.Throws<ArgumentException>(
            () => new ActivationKey(activationKeyId!, Guid.NewGuid(), SecretHash));
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    [InlineData(null)]
    public void Constructor_MissingSecretHash_IsRejected(string? secretHash)
    {
        Assert.Throws<ArgumentException>(
            () => new ActivationKey(KeyId, Guid.NewGuid(), secretHash!));
    }

    [Fact]
    public void Constructor_EmptyDeviceRecordId_IsRejected()
    {
        // The FK is to the internal DeviceRecordId, which always exists by the time a key is
        // issued (FS-02 §1.3) — an empty one is a programming error, not a valid state.
        Assert.Throws<ArgumentException>(() => new ActivationKey(KeyId, Guid.Empty, SecretHash));
    }

    [Fact]
    public void Constructor_OverlongKeyId_IsRejected()
    {
        var overlong = new string('x', ActivationKey.ActivationKeyIdMaxLength + 1);

        Assert.Throws<ArgumentException>(
            () => new ActivationKey(overlong, Guid.NewGuid(), SecretHash));
    }

    [Fact]
    public void Constructor_OverlongSecretHash_IsRejected()
    {
        var overlong = new string('x', ActivationKey.SecretHashMaxLength + 1);

        Assert.Throws<ArgumentException>(
            () => new ActivationKey(KeyId, Guid.NewGuid(), overlong));
    }

    [Fact]
    public void Consume_MarksTheKeyConsumed()
    {
        // FS-02 §5.5 step 7 / AC-4.
        var key = CreateKey();

        key.Consume();

        Assert.Equal(ActivationKeyStatus.Consumed, key.Status);
    }

    [Fact]
    public void Consume_ASecondTime_IsRejected()
    {
        // BR-003: a one-time credential. The transition out of Unconsumed never reverses.
        var key = CreateKey();
        key.Consume();

        Assert.Throws<InvalidOperationException>(() => key.Consume());
        Assert.Equal(ActivationKeyStatus.Consumed, key.Status);
    }

    [Fact]
    public void Consume_AnInvalidatedKey_IsRejected()
    {
        // FS-02 §5.7: a key invalidated by a regeneration can never be consumed afterwards.
        var key = CreateKey();
        key.Invalidate();

        Assert.Throws<InvalidOperationException>(() => key.Consume());
        Assert.Equal(ActivationKeyStatus.Invalidated, key.Status);
    }

    [Fact]
    public void Invalidate_MarksAnUnconsumedKeyInvalidated()
    {
        var key = CreateKey();

        key.Invalidate();

        Assert.Equal(ActivationKeyStatus.Invalidated, key.Status);
    }

    [Fact]
    public void Invalidate_MarksAnAlreadyConsumedKeyInvalidated()
    {
        // FS-02 §5.3 step 3: regeneration invalidates the current key "regardless of its prior
        // consumption state" — which is what makes reactivation (§5.8) possible.
        var key = CreateKey();
        key.Consume();

        key.Invalidate();

        Assert.Equal(ActivationKeyStatus.Invalidated, key.Status);
    }

    [Fact]
    public void Invalidate_IsIdempotent()
    {
        var key = CreateKey();
        key.Invalidate();

        key.Invalidate();

        Assert.Equal(ActivationKeyStatus.Invalidated, key.Status);
    }
}

using WeaponDetection.Domain;
using WeaponDetection.Infrastructure.Security;
using WeaponDetection.Infrastructure.Services;
using Xunit;

namespace WeaponDetection.UnitTests.Services;

// Unit tests for DeviceService's provisioning (FS-02 §5.1 steps 4–5, IP-01 T-15). Provisioning
// performs no I/O, so these are genuine unit tests with no database. The service is exercised with
// its real ActivationKeyGenerator/Pbkdf2PasswordHasher collaborators rather than a mock, because
// the "generated secret verifies against the stored hash" behavior is part of what is under test.
//
// No test prints an Activation Key, its secret half, or a secret hash to console/log output;
// assertions compare values in memory only.
public class DeviceServiceTests
{
    private static DeviceService CreateService() =>
        new(new ActivationKeyGenerator(new Pbkdf2PasswordHasher()));

    private static (string KeyId, string Secret) SplitPlaintext(string plaintextKey)
    {
        var parts = plaintextKey.Split(ActivationKeyGenerator.Delimiter);
        Assert.Equal(2, parts.Length);
        return (parts[0], parts[1]);
    }

    [Fact]
    public void ProvisionForBranch_ProducesDeviceReservedForTheBranch()
    {
        var service = CreateService();
        var branchId = Guid.NewGuid();

        var provisioning = service.ProvisionForBranch(branchId);

        Assert.Equal(branchId, provisioning.Device.BranchId);
    }

    [Fact]
    public void ProvisionForBranch_DeviceIsUnactivated_WithNoExternalIdentityOrSecret()
    {
        var service = CreateService();

        var device = service.ProvisionForBranch(Guid.NewGuid()).Device;

        // The reserved, pre-activation state (FS-02 §1.3, §5.1 step 4, AC-1).
        Assert.Null(device.DeviceId);
        Assert.Equal(DeviceActivationStatus.Unactivated, device.ActivationStatus);
        Assert.Null(device.ProtectedSharedSecret);
        Assert.Null(device.LastKnownAddress);
    }

    [Fact]
    public void ProvisionForBranch_DeviceHasAnInternalRecordId()
    {
        var service = CreateService();

        var device = service.ProvisionForBranch(Guid.NewGuid()).Device;

        Assert.NotEqual(Guid.Empty, device.DeviceRecordId);
    }

    [Fact]
    public void ProvisionForBranch_ActivationKeyIsUnconsumed()
    {
        var service = CreateService();

        var activationKey = service.ProvisionForBranch(Guid.NewGuid()).ActivationKey;

        Assert.Equal(ActivationKeyStatus.Unconsumed, activationKey.Status);
    }

    [Fact]
    public void ProvisionForBranch_ActivationKeyPointsAtTheProvisionedDevicesRecordId()
    {
        var service = CreateService();

        var provisioning = service.ProvisionForBranch(Guid.NewGuid());

        // The FK is to the internal DeviceRecordId, never the (still-null) external DeviceId
        // (FS-02 §1.3, §9).
        Assert.Equal(provisioning.Device.DeviceRecordId, provisioning.ActivationKey.DeviceRecordId);
    }

    [Fact]
    public void ProvisionForBranch_StoresOnlyASecretHash_NotThePlaintextKeyOrSecret()
    {
        var service = CreateService();

        var provisioning = service.ProvisionForBranch(Guid.NewGuid());
        var (_, secret) = SplitPlaintext(provisioning.PlaintextActivationKey);

        // Only a salted hash of the secret is stored; never the plaintext secret or the complete
        // key (FS-02 §1.4, §11, AC-14).
        Assert.False(string.IsNullOrWhiteSpace(provisioning.ActivationKey.SecretHash));
        Assert.NotEqual(secret, provisioning.ActivationKey.SecretHash);
        Assert.NotEqual(provisioning.PlaintextActivationKey, provisioning.ActivationKey.SecretHash);
    }

    [Fact]
    public void ProvisionForBranch_PlaintextKeyIdMatchesTheStoredActivationKeyId()
    {
        var service = CreateService();

        var provisioning = service.ProvisionForBranch(Guid.NewGuid());
        var (keyId, _) = SplitPlaintext(provisioning.PlaintextActivationKey);

        // The disclosed keyId is exactly the stored, indexed lookup value — the two must agree or an
        // activation could never resolve the record (AC-14).
        Assert.Equal(provisioning.ActivationKey.ActivationKeyId, keyId);
    }

    [Fact]
    public void ProvisionForBranch_PlaintextSecretVerifiesAgainstTheStoredHash()
    {
        var generator = new ActivationKeyGenerator(new Pbkdf2PasswordHasher());
        var service = new DeviceService(generator);

        var provisioning = service.ProvisionForBranch(Guid.NewGuid());
        var (_, secret) = SplitPlaintext(provisioning.PlaintextActivationKey);

        Assert.True(generator.VerifySecret(secret, provisioning.ActivationKey.SecretHash));
    }

    [Fact]
    public void ProvisionForBranch_ManyInvocations_ProduceDistinctDevicesAndKeys()
    {
        var service = CreateService();
        const int iterations = 500;

        var recordIds = new HashSet<Guid>();
        var keyIds = new HashSet<string>();
        var plaintextKeys = new HashSet<string>();

        for (var i = 0; i < iterations; i++)
        {
            var provisioning = service.ProvisionForBranch(Guid.NewGuid());

            Assert.True(recordIds.Add(provisioning.Device.DeviceRecordId), "DeviceRecordId collided.");
            Assert.True(keyIds.Add(provisioning.ActivationKey.ActivationKeyId), "keyId collided.");
            Assert.True(plaintextKeys.Add(provisioning.PlaintextActivationKey), "plaintext key collided.");
        }
    }

    [Fact]
    public void ProvisionForBranch_EmptyBranchId_Throws()
    {
        var service = CreateService();

        Assert.Throws<ArgumentException>(() => service.ProvisionForBranch(Guid.Empty));
    }

    [Fact]
    public void Constructor_NullGenerator_Throws()
    {
        Assert.Throws<ArgumentNullException>(() => new DeviceService(null!));
    }
}

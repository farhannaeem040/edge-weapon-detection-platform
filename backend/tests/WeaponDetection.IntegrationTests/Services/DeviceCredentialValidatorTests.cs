using System;
using System.Linq;
using System.Threading.Tasks;
using Microsoft.EntityFrameworkCore;
using WeaponDetection.Application.Interfaces;
using WeaponDetection.Domain;
using WeaponDetection.Infrastructure.Persistence;
using WeaponDetection.Infrastructure.Security;
using WeaponDetection.Infrastructure.Services;
using Xunit;

namespace WeaponDetection.IntegrationTests.Services;

// Verifies DeviceCredentialValidator (IP-05 T-51, FS-02 §10.5) against a real SQL Server database:
// the DeviceId lookup, the state guard, and the constant-time secret comparison are exercised over a
// real round-trip with the real DataProtection-backed secret protector (IP-01 §9). Each test gets its
// own freshly migrated, empty database, so lookups are exact.
//
// Every secret-shaped value is an obvious placeholder. No real device shared secret appears, and no
// test prints the presented or stored secret — verdicts are asserted, values are not displayed.
public class DeviceCredentialValidatorTests : IDisposable
{
    private const string KnownSecret = "device-shared-secret-placeholder-AAAAAAAA";
    private const string WrongSecret = "device-shared-secret-placeholder-BBBBBBBB";

    private readonly WeaponDetectionDbContext _dbContext;
    private readonly DataProtectionDeviceSecretProtector _protector = TestDeviceSecretProtector.Create();
    private readonly DeviceCredentialValidator _validator;

    public DeviceCredentialValidatorTests()
    {
        var connectionString =
            $"Server=localhost\\SQLEXPRESS;Database=WeaponDetectionDeviceCredentialValidatorTests_{Guid.NewGuid():N};" +
            "Trusted_Connection=True;TrustServerCertificate=True;";

        var options = new DbContextOptionsBuilder<WeaponDetectionDbContext>()
            .UseSqlServer(connectionString)
            .Options;

        _dbContext = new WeaponDetectionDbContext(options);
        _dbContext.Database.Migrate();

        _validator = new DeviceCredentialValidator(_dbContext, _protector);
    }

    public void Dispose()
    {
        _dbContext.Database.EnsureDeleted();
        _dbContext.Dispose();
    }

    // Seeds an Activated device whose stored protected secret unprotects to the given plaintext.
    private async Task<(Guid DeviceId, Guid DeviceRecordId)> SeedActivatedDeviceAsync(string plaintextSecret)
    {
        var branch = new Branch("Downtown Branch", "1 High Street", "ops@example.local");
        var device = new Device(branch.BranchId);
        _dbContext.Branches.Add(branch);
        _dbContext.Devices.Add(device);
        await _dbContext.SaveChangesAsync();

        device.Activate(_protector.Protect(plaintextSecret));
        await _dbContext.SaveChangesAsync();

        return (device.DeviceId!.Value, device.DeviceRecordId);
    }

    [Fact]
    public async Task ValidateAsync_ValidActiveCredentials_ReturnsValid()
    {
        var (deviceId, _) = await SeedActivatedDeviceAsync(KnownSecret);

        var result = await _validator.ValidateAsync(deviceId, KnownSecret);

        Assert.True(result.IsValid);
        Assert.Equal(DeviceCredentialValidationOutcome.Valid, result.Outcome);
    }

    // FS-06 §6.2 (additive, non-breaking): a valid result now also carries the authenticated
    // device's BranchId/DeviceRecordId, resolved from the same Device row the credential check
    // already loaded — never a second, independent lookup.
    [Fact]
    public async Task ValidateAsync_ValidActiveCredentials_PopulatesBranchIdAndDeviceRecordId()
    {
        var branch = new Branch("Downtown Branch", "1 High Street", "ops@example.local");
        var device = new Device(branch.BranchId);
        _dbContext.Branches.Add(branch);
        _dbContext.Devices.Add(device);
        await _dbContext.SaveChangesAsync();
        device.Activate(_protector.Protect(KnownSecret));
        await _dbContext.SaveChangesAsync();

        var result = await _validator.ValidateAsync(device.DeviceId!.Value, KnownSecret);

        Assert.True(result.IsValid);
        Assert.Equal(branch.BranchId, result.BranchId);
        Assert.Equal(device.DeviceRecordId, result.DeviceRecordId);
    }

    [Fact]
    public async Task ValidateAsync_InvalidOutcome_LeavesBranchIdAndDeviceRecordIdNull()
    {
        var (deviceId, _) = await SeedActivatedDeviceAsync(KnownSecret);

        var result = await _validator.ValidateAsync(deviceId, WrongSecret);

        Assert.False(result.IsValid);
        Assert.Null(result.BranchId);
        Assert.Null(result.DeviceRecordId);
    }

    [Fact]
    public async Task ValidateAsync_IncorrectSecret_ReturnsSecretMismatch()
    {
        var (deviceId, _) = await SeedActivatedDeviceAsync(KnownSecret);

        var result = await _validator.ValidateAsync(deviceId, WrongSecret);

        Assert.False(result.IsValid);
        Assert.Equal(DeviceCredentialValidationOutcome.SecretMismatch, result.Outcome);
    }

    [Fact]
    public async Task ValidateAsync_UnknownDevice_ReturnsUnknownDevice()
    {
        await SeedActivatedDeviceAsync(KnownSecret);

        var result = await _validator.ValidateAsync(Guid.NewGuid(), KnownSecret);

        Assert.False(result.IsValid);
        Assert.Equal(DeviceCredentialValidationOutcome.UnknownDevice, result.Outcome);
    }

    [Fact]
    public async Task ValidateAsync_ReactivationRequiredDevice_IsRejected_EvenWithThePreviouslyCorrectSecret()
    {
        // Regeneration revoked the secret and moved the device to ReactivationRequired. Presenting the
        // once-correct secret must still be rejected — the state guard runs before any comparison.
        var (deviceId, deviceRecordId) = await SeedActivatedDeviceAsync(KnownSecret);
        var device = await _dbContext.Devices.SingleAsync(d => d.DeviceRecordId == deviceRecordId);
        device.RequireReactivation();
        await _dbContext.SaveChangesAsync();

        var result = await _validator.ValidateAsync(deviceId, KnownSecret);

        Assert.False(result.IsValid);
        Assert.Equal(DeviceCredentialValidationOutcome.ReactivationRequired, result.Outcome);
    }

    [Fact]
    public async Task ValidateAsync_ReactivationRequiredDevice_WithAStrayStoredSecret_IsStillRejected()
    {
        // Inconsistent/legacy data: a ReactivationRequired device that still carries a stored secret.
        // The status guard rejects it regardless, so the stray secret can never authenticate.
        var (deviceId, deviceRecordId) = await SeedActivatedDeviceAsync(KnownSecret);
        var device = await _dbContext.Devices.SingleAsync(d => d.DeviceRecordId == deviceRecordId);
        device.RequireReactivation();
        await _dbContext.SaveChangesAsync();

        // Inject a stored secret directly (the domain would never leave one on a ReactivationRequired
        // device); the DB is what the validator reads.
        await _dbContext.Database.ExecuteSqlRawAsync(
            "UPDATE Devices SET ProtectedSharedSecret = {0} WHERE DeviceRecordId = {1}",
            _protector.Protect(KnownSecret), deviceRecordId);

        var result = await _validator.ValidateAsync(deviceId, KnownSecret);

        Assert.False(result.IsValid);
        Assert.Equal(DeviceCredentialValidationOutcome.ReactivationRequired, result.Outcome);
    }

    [Fact]
    public async Task ValidateAsync_ActivatedDeviceWithNoStoredSecret_ReturnsMissingStoredSecret()
    {
        // Inconsistent state: Activated but no stored secret. CanAuthenticate() is false, so it is
        // rejected before any comparison rather than throwing on a null secret.
        var (deviceId, deviceRecordId) = await SeedActivatedDeviceAsync(KnownSecret);
        await _dbContext.Database.ExecuteSqlRawAsync(
            "UPDATE Devices SET ProtectedSharedSecret = NULL WHERE DeviceRecordId = {0}", deviceRecordId);

        var result = await _validator.ValidateAsync(deviceId, KnownSecret);

        Assert.False(result.IsValid);
        Assert.Equal(DeviceCredentialValidationOutcome.MissingStoredSecret, result.Outcome);
    }

    [Fact]
    public async Task ValidateAsync_IsReadOnly_LeavesTheDeviceAndKeysUnchanged()
    {
        var (deviceId, deviceRecordId) = await SeedActivatedDeviceAsync(KnownSecret);

        await _validator.ValidateAsync(deviceId, KnownSecret);
        await _validator.ValidateAsync(deviceId, WrongSecret);

        // Validation neither rotates the secret nor changes status/identity, and writes no rows.
        using var verify = CreateVerificationContext();
        var device = await verify.Devices.AsNoTracking().SingleAsync(d => d.DeviceRecordId == deviceRecordId);
        Assert.Equal(DeviceActivationStatus.Activated, device.ActivationStatus);
        Assert.Equal(deviceId, device.DeviceId);
        Assert.Equal(KnownSecret, _protector.Unprotect(device.ProtectedSharedSecret!));
        Assert.Empty(verify.ActivationKeys.Where(k => k.DeviceRecordId == deviceRecordId));
    }

    private WeaponDetectionDbContext CreateVerificationContext()
    {
        var options = new DbContextOptionsBuilder<WeaponDetectionDbContext>()
            .UseSqlServer(_dbContext.Database.GetConnectionString())
            .Options;
        return new WeaponDetectionDbContext(options);
    }
}

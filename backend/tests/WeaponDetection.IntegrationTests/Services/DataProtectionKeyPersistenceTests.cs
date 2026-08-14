using System;
using System.IO;
using System.Threading.Tasks;
using Microsoft.AspNetCore.DataProtection;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using WeaponDetection.Domain;
using WeaponDetection.Infrastructure.Persistence;
using WeaponDetection.Infrastructure.Security;
using WeaponDetection.Infrastructure.Services;
using Xunit;

namespace WeaponDetection.IntegrationTests.Services;

// FS-07/IP-09 T-124: proves the actual incident (2026-07-28) is fixed and cannot recur silently.
// "Instance A protects, instance B (a brand-new IServiceProvider/IDataProtectionProvider, exactly
// what a recreated container gets) decrypts" is the closest in-process proxy for a real
// `docker compose up -d --force-recreate` available to a unit/integration test — the real
// container-recreation acceptance run is IP-09 Phase 10, against an actual Docker Compose project.
//
// No test in this file prints a shared secret, protected value, or key material.
public class DataProtectionKeyPersistenceTests : IDisposable
{
    private readonly List<string> _keyDirectories = [];
    private WeaponDetectionDbContext? _dbContext;

    public void Dispose()
    {
        _dbContext?.Database.EnsureDeleted();
        _dbContext?.Dispose();

        foreach (var directory in _keyDirectories)
        {
            if (Directory.Exists(directory))
            {
                Directory.Delete(directory, recursive: true);
            }
        }
    }

    private string NewKeyDirectory()
    {
        var path = Path.Combine(Path.GetTempPath(), "wd-dp-persist-test-" + Guid.NewGuid().ToString("N"));
        _keyDirectories.Add(path);
        return path;
    }

    // Mirrors production's exact configuration (DependencyInjection.cs): SetApplicationName +
    // PersistKeysToFileSystem — never the bare AddDataProtection() the other, non-persistence tests
    // use, since the whole point here is proving persistence across separate instances.
    private static DataProtectionDeviceSecretProtector CreateProtector(string keyDirectory)
    {
        var services = new ServiceCollection();
        services.AddDataProtection()
            .SetApplicationName("WeaponDetection")
            .PersistKeysToFileSystem(new DirectoryInfo(keyDirectory));
        var provider = services.BuildServiceProvider();

        return new DataProtectionDeviceSecretProtector(
            provider.GetRequiredService<IDataProtectionProvider>());
    }

    private WeaponDetectionDbContext CreateDbContext()
    {
        var connectionString =
            $"Server=localhost\\SQLEXPRESS;Database=WeaponDetectionDataProtectionPersistenceTests_{Guid.NewGuid():N};" +
            "Trusted_Connection=True;TrustServerCertificate=True;";
        var options = new DbContextOptionsBuilder<WeaponDetectionDbContext>()
            .UseSqlServer(connectionString)
            .Options;
        _dbContext = new WeaponDetectionDbContext(options);
        _dbContext.Database.Migrate();
        return _dbContext;
    }

    [Fact]
    public void Protect_ThenUnprotectFromANewServiceProviderSharingTheSameKeyDirectory_Succeeds()
    {
        var keyDirectory = NewKeyDirectory();
        const string secret = "device-shared-secret-placeholder-AAAAAAAA";

        var instanceA = CreateProtector(keyDirectory);
        var protectedValue = instanceA.Protect(secret);

        // A brand-new IServiceProvider/IDataProtectionProvider — no shared in-memory state with
        // instanceA whatsoever — is exactly what a recreated container's fresh process gets. Only
        // the on-disk key directory is shared, which is the fix being proven.
        var instanceB = CreateProtector(keyDirectory);
        var recovered = instanceB.Unprotect(protectedValue);

        Assert.Equal(secret, recovered);
    }

    [Fact]
    public void Protect_ThenUnprotectAcrossThreeSeparateInstances_AllSucceed()
    {
        // Proves the fix holds across more than one recreation, not just one.
        var keyDirectory = NewKeyDirectory();
        const string secret = "device-shared-secret-placeholder-CCCCCCCC";

        var protectedValue = CreateProtector(keyDirectory).Protect(secret);

        Assert.Equal(secret, CreateProtector(keyDirectory).Unprotect(protectedValue));
        Assert.Equal(secret, CreateProtector(keyDirectory).Unprotect(protectedValue));
    }

    [Fact]
    public void Unprotect_FromADifferentEmptyKeyDirectory_ThrowsCryptographicException_NotSomeOtherFailure()
    {
        // Reproduces the exact incident shape: a protected value whose key ring lives somewhere the
        // current process cannot see. This is the precondition DeviceCredentialValidator's new
        // catch block (below) turns into a controlled outcome instead of an unhandled exception.
        var originalKeyDirectory = NewKeyDirectory();
        var unrelatedKeyDirectory = NewKeyDirectory();
        const string secret = "device-shared-secret-placeholder-DDDDDDDD";

        var protectedValue = CreateProtector(originalKeyDirectory).Protect(secret);
        var protectorWithLostKeyRing = CreateProtector(unrelatedKeyDirectory);

        Assert.Throws<System.Security.Cryptography.CryptographicException>(
            () => protectorWithLostKeyRing.Unprotect(protectedValue));
    }

    [Fact]
    public async Task ValidateAsync_StoredSecretProtectedUnderAnInaccessibleKeyRing_ReturnsCredentialStorageUnavailable_NotAnException()
    {
        // The end-to-end shape of the actual incident: a real Activated Device in SQL Server whose
        // ProtectedSharedSecret was encrypted by a key ring the current validator instance cannot
        // reach. DeviceCredentialValidator must translate this into a typed outcome, not let a
        // CryptographicException propagate as an unhandled 500 (FS-07 §3.4).
        var originalKeyDirectory = NewKeyDirectory();
        var currentKeyDirectory = NewKeyDirectory();
        const string secret = "device-shared-secret-placeholder-EEEEEEEE";

        var dbContext = CreateDbContext();
        var branch = new Branch("Downtown Branch", "1 High Street", "ops@example.local");
        var device = new Device(branch.BranchId);
        dbContext.Branches.Add(branch);
        dbContext.Devices.Add(device);
        await dbContext.SaveChangesAsync();

        device.Activate(CreateProtector(originalKeyDirectory).Protect(secret));
        await dbContext.SaveChangesAsync();

        var validatorWithLostKeyRing =
            new DeviceCredentialValidator(dbContext, CreateProtector(currentKeyDirectory));

        var result = await validatorWithLostKeyRing.ValidateAsync(device.DeviceId!.Value, secret);

        Assert.False(result.IsValid);
        Assert.Equal(
            Application.Interfaces.DeviceCredentialValidationOutcome.CredentialStorageUnavailable,
            result.Outcome);
    }

    [Fact]
    public async Task ValidateAsync_StoredSecretProtectedUnderAnAccessibleKeyRing_StillAuthenticatesNormally()
    {
        // The unaffected path: once the key ring IS reachable (the normal, fixed case), validation
        // behaves exactly as it always has — this feature changes behavior in exactly one branch.
        var keyDirectory = NewKeyDirectory();
        const string secret = "device-shared-secret-placeholder-FFFFFFFF";

        var dbContext = CreateDbContext();
        var branch = new Branch("Downtown Branch", "1 High Street", "ops@example.local");
        var device = new Device(branch.BranchId);
        dbContext.Branches.Add(branch);
        dbContext.Devices.Add(device);
        await dbContext.SaveChangesAsync();

        var protector = CreateProtector(keyDirectory);
        device.Activate(protector.Protect(secret));
        await dbContext.SaveChangesAsync();

        // A second, independently constructed instance sharing the same key directory — simulating
        // the validator running after a redeploy, with the fix in place.
        var validator = new DeviceCredentialValidator(dbContext, CreateProtector(keyDirectory));

        var validResult = await validator.ValidateAsync(device.DeviceId!.Value, secret);
        var invalidResult = await validator.ValidateAsync(device.DeviceId!.Value, "wrong-secret-value");

        Assert.True(validResult.IsValid);
        Assert.False(invalidResult.IsValid);
        Assert.Equal(
            Application.Interfaces.DeviceCredentialValidationOutcome.SecretMismatch,
            invalidResult.Outcome);
    }

    // FS-07 §3.5: the purpose string is part of the cryptographic compatibility contract — changing
    // it would make every already-protected secret unreadable even with an intact key ring, the
    // same class of failure this whole feature exists to prevent. Pinned here by independently
    // constructing a raw protector with the exact expected string and proving it interoperates with
    // DataProtectionDeviceSecretProtector, rather than reading a private constant via reflection.
    [Fact]
    public void Protect_IsInteroperableWithARawProtectorUsingTheDocumentedPurposeString()
    {
        var keyDirectory = NewKeyDirectory();
        const string secret = "device-shared-secret-placeholder-GGGGGGGG";
        const string documentedPurpose = "WeaponDetection.Device.SharedSecret.v1";

        var services = new ServiceCollection();
        services.AddDataProtection()
            .SetApplicationName("WeaponDetection")
            .PersistKeysToFileSystem(new DirectoryInfo(keyDirectory));
        var provider = services.BuildServiceProvider().GetRequiredService<IDataProtectionProvider>();

        var deviceSecretProtector = new DataProtectionDeviceSecretProtector(provider);
        var protectedValue = deviceSecretProtector.Protect(secret);

        var rawProtectorWithDocumentedPurpose = provider.CreateProtector(documentedPurpose);
        var recovered = rawProtectorWithDocumentedPurpose.Unprotect(protectedValue);

        Assert.Equal(secret, recovered);
    }
}

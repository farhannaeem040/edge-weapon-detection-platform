using WeaponDetection.Infrastructure.Storage;
using Xunit;

namespace WeaponDetection.UnitTests.Storage;

// FS-08 §8, mirroring DeviceSecretDataProtectionOptionsValidatorTests: shape-only validation of
// AlertSnapshots:StoragePath — no filesystem access here (that is
// AlertSnapshotStoragePathValidator's job, tested separately). Unlike DataProtection:KeyPath,
// StoragePath is REQUIRED, so the "not configured" case fails rather than succeeding.
public class AlertSnapshotStorageOptionsValidatorTests
{
    private readonly AlertSnapshotStorageOptionsValidator _validator = new();

    [Fact]
    public void Validate_StoragePathNotConfigured_Fails()
    {
        var result = _validator.Validate(null, new AlertSnapshotStorageOptions { StoragePath = null });

        Assert.True(result.Failed);
    }

    [Fact]
    public void Validate_BlankStoragePath_Fails()
    {
        var result = _validator.Validate(null, new AlertSnapshotStorageOptions { StoragePath = "   " });

        Assert.True(result.Failed);
    }

    [Fact]
    public void Validate_RelativeStoragePath_Fails()
    {
        var result = _validator.Validate(
            null, new AlertSnapshotStorageOptions { StoragePath = "relative/path" });

        Assert.True(result.Failed);
    }

    [Fact]
    public void Validate_AbsoluteStoragePath_Succeeds()
    {
        var absolutePath = Path.Combine(Path.GetTempPath(), "weapon-detection-alert-snapshots");

        var result = _validator.Validate(
            null, new AlertSnapshotStorageOptions { StoragePath = absolutePath });

        Assert.True(result.Succeeded);
    }
}

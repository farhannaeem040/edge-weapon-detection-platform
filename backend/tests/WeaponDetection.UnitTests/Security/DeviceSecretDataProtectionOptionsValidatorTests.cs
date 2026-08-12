using WeaponDetection.Infrastructure.Security;
using Xunit;

namespace WeaponDetection.UnitTests.Security;

// FS-07 §3.3/T-116: shape-only validation of DataProtection:KeyPath — no filesystem access here
// (that is DataProtectionKeyPathValidator's job, tested separately).
public class DeviceSecretDataProtectionOptionsValidatorTests
{
    private readonly DeviceSecretDataProtectionOptionsValidator _validator = new();

    [Fact]
    public void Validate_KeyPathNotConfigured_Succeeds()
    {
        var result = _validator.Validate(null, new DeviceSecretDataProtectionOptions { KeyPath = null });

        Assert.True(result.Succeeded);
    }

    [Fact]
    public void Validate_BlankKeyPath_Fails()
    {
        var result = _validator.Validate(null, new DeviceSecretDataProtectionOptions { KeyPath = "   " });

        Assert.True(result.Failed);
    }

    [Fact]
    public void Validate_RelativeKeyPath_Fails()
    {
        var result = _validator.Validate(
            null, new DeviceSecretDataProtectionOptions { KeyPath = "relative/path" });

        Assert.True(result.Failed);
    }

    [Fact]
    public void Validate_AbsoluteKeyPath_Succeeds()
    {
        var absolutePath = Path.Combine(Path.GetTempPath(), "weapon-detection-dataprotection-keys");

        var result = _validator.Validate(
            null, new DeviceSecretDataProtectionOptions { KeyPath = absolutePath });

        Assert.True(result.Succeeded);
    }
}

using WeaponDetection.Infrastructure.Services;
using Xunit;

namespace WeaponDetection.UnitTests.Services;

// FS-09 §11: shape-only validation of AlertQuota:MaximumPerBranchPerDay — integer >= 1, bounded to a
// documented safe ceiling, no clamping. Mirrors AlertSnapshotStorageOptionsValidatorTests' style.
public class AlertQuotaOptionsValidatorTests
{
    private readonly AlertQuotaOptionsValidator _validator = new();

    [Fact]
    public void Validate_DefaultOptions_Succeeds()
    {
        var result = _validator.Validate(null, new AlertQuotaOptions());

        Assert.True(result.Succeeded);
    }

    [Theory]
    [InlineData(0)]
    [InlineData(-1)]
    public void Validate_MaximumBelowOne_Fails(int maximum)
    {
        var result = _validator.Validate(
            null, new AlertQuotaOptions { MaximumPerBranchPerDay = maximum });

        Assert.True(result.Failed);
    }

    [Fact]
    public void Validate_MaximumAboveCeiling_Fails()
    {
        var result = _validator.Validate(
            null,
            new AlertQuotaOptions
            {
                MaximumPerBranchPerDay = AlertQuotaOptionsValidator.MaximumPerBranchPerDayCeiling + 1,
            });

        Assert.True(result.Failed);
    }

    [Fact]
    public void Validate_MaximumAtCeiling_Succeeds()
    {
        var result = _validator.Validate(
            null,
            new AlertQuotaOptions
            {
                MaximumPerBranchPerDay = AlertQuotaOptionsValidator.MaximumPerBranchPerDayCeiling,
            });

        Assert.True(result.Succeeded);
    }

    [Fact]
    public void Validate_MaximumAtOne_Succeeds()
    {
        var result = _validator.Validate(null, new AlertQuotaOptions { MaximumPerBranchPerDay = 1 });

        Assert.True(result.Succeeded);
    }

    [Fact]
    public void Validate_DisabledWithInvalidMaximum_StillFails()
    {
        // Shape validation runs regardless of Enabled — an operator who disables the feature but
        // leaves a nonsensical maximum configured is still told about it at startup, not silently
        // ignored (FS-09 §11: "no silent clamping").
        var result = _validator.Validate(
            null, new AlertQuotaOptions { Enabled = false, MaximumPerBranchPerDay = 0 });

        Assert.True(result.Failed);
    }

    // --- POC configurable maximum (compose ALERT_QUOTA_MAXIMUM_PER_BRANCH_PER_DAY) ---------------

    [Fact]
    public void DefaultMaximum_IsFifteen_WhenNothingIsConfigured()
    {
        // compose.yaml falls back to "15" when the env var is absent, and this C# default backs that
        // up if the section itself is missing — the two must not drift.
        Assert.Equal(15, new AlertQuotaOptions().MaximumPerBranchPerDay);
    }

    [Theory]
    [InlineData(1000)]   // the POC value
    [InlineData(500)]
    [InlineData(9999)]
    public void Validate_ConfiguredMaximumWithinRange_Succeeds(int maximum)
    {
        var result = _validator.Validate(null, new AlertQuotaOptions { MaximumPerBranchPerDay = maximum });

        Assert.True(result.Succeeded);
    }

    [Fact]
    public void Validate_ExcessivelyLargeMaximum_IsRejectedNotClamped()
    {
        var result = _validator.Validate(
            null,
            new AlertQuotaOptions { MaximumPerBranchPerDay = 100_000 });

        Assert.False(result.Succeeded);
        // Rejected outright: a misconfigured quota must stop startup, never silently become a
        // different (smaller) effective limit.
        Assert.Contains(
            AlertQuotaOptionsValidator.MaximumPerBranchPerDayCeiling.ToString(),
            result.FailureMessage);
    }
}

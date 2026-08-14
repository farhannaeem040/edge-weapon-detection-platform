using Microsoft.Extensions.Options;

namespace WeaponDetection.Infrastructure.Services;

// Runs eagerly at application startup via ValidateOnStart() (see DependencyInjection.cs), mirroring
// AlertSnapshotStorageOptionsValidator (FS-09 §11). Shape-only: MaximumPerBranchPerDay must be an
// integer >= 1 and <= the documented safe ceiling below — invalid configuration stops Backend startup
// with a clear message; there is no clamping to the nearest valid value.
public class AlertQuotaOptionsValidator : IValidateOptions<AlertQuotaOptions>
{
    // A documented safe ceiling (task Phase 2: "maximum bounded to a documented safe value"). Well
    // above any plausible production Branch daily Alert volume, while still catching an obvious
    // misconfiguration (e.g. a value entered in the wrong units) before it reaches AlertSyncService.
    public const int MaximumPerBranchPerDayCeiling = 10_000;

    public ValidateOptionsResult Validate(string? name, AlertQuotaOptions options)
    {
        if (options.MaximumPerBranchPerDay < 1)
        {
            return ValidateOptionsResult.Fail(
                "AlertQuota:MaximumPerBranchPerDay must be at least 1.");
        }

        if (options.MaximumPerBranchPerDay > MaximumPerBranchPerDayCeiling)
        {
            return ValidateOptionsResult.Fail(
                $"AlertQuota:MaximumPerBranchPerDay must not exceed {MaximumPerBranchPerDayCeiling}.");
        }

        return ValidateOptionsResult.Success;
    }
}

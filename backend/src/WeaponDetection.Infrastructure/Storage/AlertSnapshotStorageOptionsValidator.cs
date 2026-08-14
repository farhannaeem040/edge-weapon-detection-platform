using Microsoft.Extensions.Options;

namespace WeaponDetection.Infrastructure.Storage;

// Runs eagerly at application startup via ValidateOnStart() (see DependencyInjection.cs), mirroring
// DeviceSecretDataProtectionOptionsValidator (FS-07 §3.3). Shape-only: no filesystem access here
// (the directory-exists/writable check is a separate startup step, AlertSnapshotStoragePathValidator,
// FS-08 §8).
//
// Unlike DeviceSecretDataProtectionOptionsValidator, StoragePath is REQUIRED — there is no fallback
// location for snapshot evidence the way Data Protection falls back to its own default key location.
public class AlertSnapshotStorageOptionsValidator : IValidateOptions<AlertSnapshotStorageOptions>
{
    public ValidateOptionsResult Validate(string? name, AlertSnapshotStorageOptions options)
    {
        if (string.IsNullOrWhiteSpace(options.StoragePath))
        {
            return ValidateOptionsResult.Fail("AlertSnapshots:StoragePath is required.");
        }

        if (!Path.IsPathFullyQualified(options.StoragePath))
        {
            return ValidateOptionsResult.Fail("AlertSnapshots:StoragePath must be an absolute path.");
        }

        return ValidateOptionsResult.Success;
    }
}

using Microsoft.Extensions.Options;

namespace WeaponDetection.Infrastructure.Security;

// Runs eagerly at application startup via ValidateOnStart() (see DependencyInjection.cs), mirroring
// JwtOptionsValidator. Shape-only: no filesystem access here (the directory-exists/writable check is
// a separate startup step, DataProtectionKeyPathValidator, FS-07 §3.3) — an options validator that
// touches disk would blur "is this configuration well-formed" with "is the environment currently
// healthy," and the latter needs a clearer, dedicated failure message.
//
// KeyPath itself is optional: when unset, Data Protection falls back to its own default location
// unchanged (FS-07 §3.1) — this validator only rejects a KeyPath that IS supplied but malformed.
public class DeviceSecretDataProtectionOptionsValidator : IValidateOptions<DeviceSecretDataProtectionOptions>
{
    public ValidateOptionsResult Validate(string? name, DeviceSecretDataProtectionOptions options)
    {
        if (string.IsNullOrEmpty(options.KeyPath))
        {
            return ValidateOptionsResult.Success;
        }

        if (string.IsNullOrWhiteSpace(options.KeyPath))
        {
            return ValidateOptionsResult.Fail("DataProtection:KeyPath must not be blank.");
        }

        if (!Path.IsPathFullyQualified(options.KeyPath))
        {
            return ValidateOptionsResult.Fail("DataProtection:KeyPath must be an absolute path.");
        }

        return ValidateOptionsResult.Success;
    }
}

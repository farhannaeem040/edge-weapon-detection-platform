using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using WeaponDetection.Application.Interfaces;
using WeaponDetection.Infrastructure.Security;

namespace WeaponDetection.Infrastructure.Startup;

// FS-07 §3.3: the separate, I/O-performing half of Data Protection key-path validation.
// DeviceSecretDataProtectionOptionsValidator (ValidateOnStart) only checks the configured value is
// well-formed; this step checks the *environment* is actually healthy — the directory exists or
// can be created, and the Backend process can read and write it. Run once at startup (mirroring
// AdminBootstrapper's "unhandled exception here fails startup" posture, Program.cs) so a broken
// mount is caught immediately rather than surfacing as an opaque failure on the first real request.
public class DataProtectionKeyPathValidator : IDataProtectionKeyPathValidator
{
    private readonly DeviceSecretDataProtectionOptions _options;
    private readonly ILogger<DataProtectionKeyPathValidator> _logger;

    public DataProtectionKeyPathValidator(
        IOptions<DeviceSecretDataProtectionOptions> options, ILogger<DataProtectionKeyPathValidator> logger)
    {
        _options = options.Value;
        _logger = logger;
    }

    public void Validate()
    {
        var keyPath = _options.KeyPath;
        if (string.IsNullOrEmpty(keyPath))
        {
            _logger.LogInformation(
                "Data Protection key path not configured; using the ASP.NET Core default location.");
            return;
        }

        try
        {
            var directory = new DirectoryInfo(keyPath);
            if (!directory.Exists)
            {
                directory.Create();
            }

            // A real read/write probe, not just an existence check: an existence check alone
            // would not catch a mounted-but-read-only volume, which is exactly the failure mode
            // most likely to recreate the original incident (FS-07) if the mount is misconfigured.
            var probePath = Path.Combine(keyPath, ".dataprotection-writeprobe");
            File.WriteAllText(probePath, string.Empty);
            File.Delete(probePath);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            // Names only the configuration key — never the exception's own message verbatim,
            // which on some platforms can include the full resolved path; the operator already
            // knows the path they configured.
            throw new InvalidOperationException(
                "The configured 'DataProtection:KeyPath' directory is not usable (missing, or not " +
                "readable/writable by the Backend process). Verify the mounted volume/bind path and " +
                "its permissions.", ex);
        }

        _logger.LogInformation("Data Protection key path verified and ready.");
    }
}

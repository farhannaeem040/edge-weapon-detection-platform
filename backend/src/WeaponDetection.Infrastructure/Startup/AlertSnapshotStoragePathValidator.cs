using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using WeaponDetection.Application.Interfaces;
using WeaponDetection.Infrastructure.Storage;

namespace WeaponDetection.Infrastructure.Startup;

// FS-08 §8, mirroring DataProtectionKeyPathValidator (FS-07 §3.3) exactly: the separate,
// I/O-performing half of snapshot storage path validation. AlertSnapshotStorageOptionsValidator
// (ValidateOnStart) only checks the configured value is well-formed; this step checks the
// *environment* is actually healthy — the directory exists or can be created, and the Backend
// process can read and write it. Run once at startup so a broken mount (the exact bug class FS-07
// fixed for the Data Protection volume) is caught immediately rather than surfacing as an opaque
// failure on the first upload request.
public class AlertSnapshotStoragePathValidator : IAlertSnapshotStoragePathValidator
{
    private readonly AlertSnapshotStorageOptions _options;
    private readonly ILogger<AlertSnapshotStoragePathValidator> _logger;

    public AlertSnapshotStoragePathValidator(
        IOptions<AlertSnapshotStorageOptions> options, ILogger<AlertSnapshotStoragePathValidator> logger)
    {
        _options = options.Value;
        _logger = logger;
    }

    public void Validate()
    {
        // AlertSnapshotStorageOptionsValidator's ValidateOnStart() has already rejected a
        // missing/malformed value before this ever runs, so StoragePath is guaranteed non-empty
        // here — this is a defensive re-check, not the primary enforcement point.
        var storagePath = _options.StoragePath;
        if (string.IsNullOrEmpty(storagePath))
        {
            throw new InvalidOperationException(
                "The 'AlertSnapshots:StoragePath' configuration value is required but was not set.");
        }

        try
        {
            var directory = new DirectoryInfo(storagePath);
            if (!directory.Exists)
            {
                directory.Create();
            }

            // A real read/write probe, not just an existence check: an existence check alone would
            // not catch a mounted-but-read-only volume (FS-07's exact bug class, pre-empted here by
            // construction rather than rediscovered, FS-08 §8).
            var probePath = Path.Combine(storagePath, ".alert-snapshots-writeprobe");
            File.WriteAllText(probePath, string.Empty);
            File.Delete(probePath);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            // Names only the configuration key — never the exception's own message verbatim, which
            // on some platforms can include the full resolved path; the operator already knows the
            // path they configured.
            throw new InvalidOperationException(
                "The configured 'AlertSnapshots:StoragePath' directory is not usable (missing, or " +
                "not readable/writable by the Backend process). Verify the mounted volume/bind path " +
                "and its permissions.", ex);
        }

        _logger.LogInformation("Alert snapshot storage path verified and ready.");
    }
}

namespace WeaponDetection.Infrastructure.Storage;

// Bound from configuration section "AlertSnapshots" (AlertSnapshots:StoragePath / the equivalent
// AlertSnapshots__StoragePath environment variable) — mirrors DeviceSecretDataProtectionOptions'
// shape exactly (FS-08 §8). Unlike DataProtection:KeyPath, there is no ASP.NET Core built-in
// fallback location for snapshot evidence, so StoragePath is required: FileSystemAlertSnapshotStorage
// has nowhere sensible to write without it, and AlertSnapshotStorageOptionsValidator rejects a
// missing value rather than silently degrading to some ad hoc default.
public class AlertSnapshotStorageOptions
{
    public const string SectionName = "AlertSnapshots";

    public string? StoragePath { get; set; }
}

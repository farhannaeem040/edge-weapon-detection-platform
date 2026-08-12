namespace WeaponDetection.Application.Interfaces;

// FS-08 §8, mirroring IDataProtectionKeyPathValidator (FS-07 §3.3) exactly: verifies the configured
// snapshot storage directory is actually usable before the Backend starts serving requests. This is
// a contract only — filesystem access is an Infrastructure-layer concern.
public interface IAlertSnapshotStoragePathValidator
{
    // Idempotent. Ensures the configured StoragePath directory exists and is read/write-accessible;
    // throws a redacted, actionable error otherwise. Unlike Data Protection's KeyPath, StoragePath
    // is required (there is no ASP.NET Core built-in default for snapshot evidence), so this always
    // performs the check.
    void Validate();
}

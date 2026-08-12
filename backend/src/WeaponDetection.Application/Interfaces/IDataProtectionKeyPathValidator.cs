namespace WeaponDetection.Application.Interfaces;

// FS-07 §3.3: verifies the configured Data Protection key directory is actually usable before the
// Backend starts serving requests. This is a contract only — filesystem access is an
// Infrastructure-layer concern.
public interface IDataProtectionKeyPathValidator
{
    // Idempotent: does nothing if no KeyPath is configured (the ASP.NET Core default location is
    // used, unchanged). If a KeyPath is configured, ensures the directory exists and is
    // read/write-accessible; throws a redacted, actionable error otherwise. Never touches key
    // contents.
    void Validate();
}

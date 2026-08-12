namespace WeaponDetection.Infrastructure.Security;

// Bound from configuration section "DataProtection" (DataProtection:KeyPath / the equivalent
// DataProtection__KeyPath environment variable). When unset, the ASP.NET Core default key ring
// location is used unchanged (no behavior change for a non-containerized `dotnet run`). Set to an
// absolute, durable path (a mounted Docker volume in production, FS-07 §3.2) so the key ring
// survives full container recreation, not merely a process restart within the same container.
public class DeviceSecretDataProtectionOptions
{
    public const string SectionName = "DataProtection";

    public string? KeyPath { get; set; }
}

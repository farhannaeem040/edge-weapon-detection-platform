using Microsoft.AspNetCore.DataProtection;
using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.Infrastructure.Security;

// ARCH-001 §13.3: recoverable-but-protected storage via ASP.NET Core Data Protection, not
// one-way hashing, because the Backend must present the plaintext device shared secret again
// when authenticating outbound commands to the Jetson Agent. Keys are persisted using the
// ASP.NET Core default (local file-system key ring), consistent with the prototype's
// single-host deployment (ARCH-001 §12.1, ARCH-ASM-001).
public class DataProtectionDeviceSecretProtector : IDeviceSecretProtector
{
    // A distinct purpose string scopes derived keys so this protector's ciphertext cannot be
    // unprotected by a data protector created for a different purpose.
    private const string Purpose = "WeaponDetection.Device.SharedSecret.v1";

    private readonly IDataProtector _protector;

    public DataProtectionDeviceSecretProtector(IDataProtectionProvider dataProtectionProvider)
    {
        _protector = dataProtectionProvider.CreateProtector(Purpose);
    }

    public string Protect(string plaintextSecret)
    {
        if (string.IsNullOrWhiteSpace(plaintextSecret))
        {
            throw new ArgumentException("Shared secret is required.", nameof(plaintextSecret));
        }

        return _protector.Protect(plaintextSecret);
    }

    public string Unprotect(string protectedSecret)
    {
        if (string.IsNullOrWhiteSpace(protectedSecret))
        {
            throw new ArgumentException("Protected secret is required.", nameof(protectedSecret));
        }

        return _protector.Unprotect(protectedSecret);
    }
}

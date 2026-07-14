namespace WeaponDetection.Application.Interfaces;

// Decouples the Application layer from the concrete protection mechanism (ASP.NET Core Data
// Protection in Infrastructure). Unlike IPasswordHasher, this is recoverable-but-protected
// storage, not one-way hashing: the Backend must be able to present the plaintext shared secret
// again when authenticating outbound commands to the Jetson Agent.
public interface IDeviceSecretProtector
{
    // Protects a plaintext device shared secret for storage. The returned value is safe to
    // persist; it is not the plaintext.
    string Protect(string plaintextSecret);

    // Recovers the plaintext device shared secret from a previously protected value.
    string Unprotect(string protectedSecret);
}

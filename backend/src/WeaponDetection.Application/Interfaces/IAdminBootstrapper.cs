namespace WeaponDetection.Application.Interfaces;

// FS-01 §3 precondition / IP-01 §6: provisions the single AdminUser account on application
// startup. This is a contract only — persistence, configuration reading, and password hashing
// coordination are Infrastructure-layer concerns (the concrete implementation depends on the
// DbContext and IPasswordHasher).
public interface IAdminBootstrapper
{
    // Idempotent: creates the single AdminUser only if none exists yet. Does nothing if an
    // AdminUser already exists. Throws if no AdminUser exists and the required bootstrap
    // configuration is missing or invalid.
    Task BootstrapAsync(CancellationToken cancellationToken = default);
}

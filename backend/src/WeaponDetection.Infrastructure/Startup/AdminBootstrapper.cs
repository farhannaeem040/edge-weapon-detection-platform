using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging;
using WeaponDetection.Application.Interfaces;
using WeaponDetection.Domain;
using WeaponDetection.Infrastructure.Persistence;

namespace WeaponDetection.Infrastructure.Startup;

// IP-01 §6: provisions the single AdminUser account on application startup, replacing
// migration-based seeding. Reads BootstrapAdmin:CredentialIdentifier / BootstrapAdmin:Password
// from configuration (.NET user-secrets locally, environment variables elsewhere) — never from
// appsettings.json, and never persisted or logged in plaintext.
public class AdminBootstrapper : IAdminBootstrapper
{
    private const string CredentialIdentifierKey = "BootstrapAdmin:CredentialIdentifier";
    private const string PasswordKey = "BootstrapAdmin:Password";

    private readonly WeaponDetectionDbContext _dbContext;
    private readonly IPasswordHasher _passwordHasher;
    private readonly IConfiguration _configuration;
    private readonly ILogger<AdminBootstrapper> _logger;

    public AdminBootstrapper(
        WeaponDetectionDbContext dbContext,
        IPasswordHasher passwordHasher,
        IConfiguration configuration,
        ILogger<AdminBootstrapper> logger)
    {
        _dbContext = dbContext;
        _passwordHasher = passwordHasher;
        _configuration = configuration;
        _logger = logger;
    }

    public async Task BootstrapAsync(CancellationToken cancellationToken = default)
    {
        if (await _dbContext.AdminUsers.AnyAsync(cancellationToken))
        {
            _logger.LogInformation("Admin bootstrap skipped: an Admin account already exists.");
            return;
        }

        var credentialIdentifier = _configuration[CredentialIdentifierKey];
        if (string.IsNullOrWhiteSpace(credentialIdentifier))
        {
            throw new InvalidOperationException(
                $"Admin bootstrap is required (no Admin account exists) but configuration key " +
                $"'{CredentialIdentifierKey}' is missing or blank. Set it via .NET user-secrets " +
                "(local development, key 'BootstrapAdmin:CredentialIdentifier') or the " +
                "BootstrapAdmin__CredentialIdentifier environment variable.");
        }

        var password = _configuration[PasswordKey];
        if (string.IsNullOrWhiteSpace(password))
        {
            throw new InvalidOperationException(
                $"Admin bootstrap is required (no Admin account exists) but configuration key " +
                $"'{PasswordKey}' is missing or blank. Set it via .NET user-secrets (local " +
                "development, key 'BootstrapAdmin:Password') or the BootstrapAdmin__Password " +
                "environment variable.");
        }

        // Hashed before an AdminUser instance is ever constructed — the plaintext password
        // never reaches a log statement or exception message from this point on.
        var passwordHash = _passwordHasher.Hash(password);

        AdminUser adminUser;
        try
        {
            adminUser = new AdminUser(credentialIdentifier, passwordHash);
        }
        catch (ArgumentException ex)
        {
            throw new InvalidOperationException(
                "Admin bootstrap configuration is invalid: " + ex.Message, ex);
        }

        await using var transaction = await _dbContext.Database.BeginTransactionAsync(cancellationToken);
        try
        {
            _dbContext.AdminUsers.Add(adminUser);
            await _dbContext.SaveChangesAsync(cancellationToken);
            await transaction.CommitAsync(cancellationToken);
        }
        catch (DbUpdateException ex)
        {
            await transaction.RollbackAsync(cancellationToken);

            // The unique index on CredentialIdentifier is the final safeguard against a
            // concurrent bootstrap race (two instances starting simultaneously, both passing
            // the AnyAsync check above before either commits). If another process won that
            // race, an Admin now exists and bootstrap has nothing left to do.
            if (await _dbContext.AdminUsers.AsNoTracking().AnyAsync(cancellationToken))
            {
                _logger.LogInformation(
                    "Admin bootstrap skipped: an Admin account was created concurrently by another process.");
                return;
            }

            throw new InvalidOperationException(
                "Admin bootstrap failed while persisting the initial Admin account. Verify the " +
                "configured BootstrapAdmin values satisfy the Admin account's constraints.", ex);
        }

        _logger.LogInformation("Initial Admin account created successfully.");
    }
}

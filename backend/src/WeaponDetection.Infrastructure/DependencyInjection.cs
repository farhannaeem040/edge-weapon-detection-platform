using Microsoft.AspNetCore.DataProtection;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Options;
using WeaponDetection.Application.Interfaces;
using WeaponDetection.Infrastructure.Persistence;
using WeaponDetection.Infrastructure.Security;
using WeaponDetection.Infrastructure.Services;
using WeaponDetection.Infrastructure.Startup;
using WeaponDetection.Infrastructure.Storage;

namespace WeaponDetection.Infrastructure;

public static class DependencyInjection
{
    public static IServiceCollection AddInfrastructure(
        this IServiceCollection services,
        string connectionString,
        IConfiguration configuration)
    {
        services.AddDbContext<WeaponDetectionDbContext>(options =>
            options.UseSqlServer(connectionString));

        // Stateless (constants only) — safe and efficient as a singleton.
        services.AddSingleton<IPasswordHasher, Pbkdf2PasswordHasher>();

        // Stateless apart from the (singleton) IPasswordHasher it delegates secret hashing to —
        // safe and efficient as a singleton.
        services.AddSingleton<IActivationKeyGenerator, ActivationKeyGenerator>();

        // FS-07: DataProtection:KeyPath is validated for shape (ValidateOnStart) and, separately,
        // for actual filesystem usability (IDataProtectionKeyPathValidator, run explicitly from
        // Program.cs before the host starts serving requests — mirrors JwtOptions/AdminBootstrapper).
        services.AddSingleton<IValidateOptions<DeviceSecretDataProtectionOptions>, DeviceSecretDataProtectionOptionsValidator>();
        services.AddOptions<DeviceSecretDataProtectionOptions>()
            .Bind(configuration.GetSection(DeviceSecretDataProtectionOptions.SectionName))
            .ValidateOnStart();
        services.AddSingleton<IDataProtectionKeyPathValidator, DataProtectionKeyPathValidator>();

        // ASP.NET Core Data Protection's IDataProtector is thread-safe and designed for
        // long-lived reuse — safe and efficient as a singleton. When DataProtection:KeyPath is
        // configured, keys persist to that directory (a mounted Docker volume in production, FS-07
        // §3.2) so the key ring survives full container recreation, not merely a process restart
        // within the same container — the gap that broke the production Device secret on 2026-07-28.
        // The application name is fixed and must never change between deployments (FS-07 §3.1): it
        // is itself an input to key derivation, so changing it would make every already-protected
        // secret unreadable even with an intact key ring.
        var dataProtectionBuilder = services.AddDataProtection().SetApplicationName("WeaponDetection");
        var dataProtectionKeyPath = configuration[$"{DeviceSecretDataProtectionOptions.SectionName}:KeyPath"];
        if (!string.IsNullOrEmpty(dataProtectionKeyPath))
        {
            dataProtectionBuilder.PersistKeysToFileSystem(new DirectoryInfo(dataProtectionKeyPath));
        }
        services.AddSingleton<IDeviceSecretProtector, DataProtectionDeviceSecretProtector>();

        // Depends on the (scoped) DbContext, so it must be scoped itself.
        services.AddScoped<IAdminBootstrapper, AdminBootstrapper>();

        // TimeProvider.System is registered only if the host hasn't already supplied one.
        services.TryAddSingleton(TimeProvider.System);

        // ValidateOnStart() runs JwtOptionsValidator during application startup (via the
        // generic host's startup-validation hosted service), so an invalid Jwt configuration
        // fails startup immediately rather than remaining dormant until the first token
        // issuance (IP-01 §7).
        services.AddSingleton<IValidateOptions<JwtOptions>, JwtOptionsValidator>();
        services.AddOptions<JwtOptions>()
            .Bind(configuration.GetSection(JwtOptions.SectionName))
            .ValidateOnStart();
        services.AddSingleton<IJwtIssuer, JwtIssuer>();

        // Depend on the (scoped) DbContext, so they must be scoped themselves.
        services.AddScoped<IAuthService, AuthService>();
        services.AddScoped<IAdminSessionValidator, AdminSessionValidator>();

        // Both depend on the (scoped) DbContext, so they are scoped. BranchService uses it for the
        // branch-creation transaction and the list/detail reads (T-15/T-16); DeviceService uses it
        // for the device read path (T-16), key regeneration (T-17), and activation consumption
        // (T-19) — its provisioning step alone stays pure and does not touch the context.
        // DeviceService also depends on the (singleton) IActivationKeyGenerator and
        // IDeviceSecretProtector registered above, resolved automatically by the container.
        services.AddScoped<IDeviceService, DeviceService>();
        services.AddScoped<IBranchService, BranchService>();

        // The device-credential validation service (IP-05 T-51/T-52). Depends on the (scoped)
        // DbContext for its read-only lookup and the (singleton) IDeviceSecretProtector to recover the
        // stored secret for a constant-time comparison, so it is scoped.
        services.AddScoped<IDeviceCredentialValidator, DeviceCredentialValidator>();

        // FS-11 §3, IP-13 T-226: read-only Camera-configuration lookup for the authenticated Device.
        services.AddScoped<IDeviceConfigurationService, DeviceConfigurationService>();

        // FS-09 §11, IP-11 T-168: AlertQuota:MaximumPerBranchPerDay is validated for shape
        // (ValidateOnStart) — a documented integer floor/ceiling, no filesystem/network dependency, so
        // (unlike AlertSnapshots/DataProtection) there is no separate startup-time usability check.
        services.AddSingleton<IValidateOptions<AlertQuotaOptions>, AlertQuotaOptionsValidator>();
        services.AddOptions<AlertQuotaOptions>()
            .Bind(configuration.GetSection(AlertQuotaOptions.SectionName))
            .ValidateOnStart();

        // Read-only Alert list/detail projections for the Admin Dashboard (FS-10 §6, IP-12 T-197).
        // Depends on the (scoped) DbContext for its AsNoTracking reads, so it is scoped.
        services.AddScoped<IAlertQueryService, AlertQueryService>();

        // The bounded Admin Dashboard summary (FS-10 §6, IP-12 T-197). Depends on the (scoped)
        // DbContext, the (already-registered) TimeProvider, and the (already-registered)
        // AlertQuotaOptions, so it is scoped.
        services.AddScoped<IDashboardSummaryService, DashboardSummaryService>();

        // The Alert idempotent-insert engine for POST /api/v1/sync/events (FS-06 §5, IP-08 T-98/T-100),
        // extended by FS-09/IP-11 with the branch daily Alert quota (§7). Depends on the (scoped)
        // DbContext for its batch transaction, the (already-registered) TimeProvider for ReceivedAtUtc,
        // and the (already-registered) AlertQuotaOptions, so it is scoped.
        services.AddScoped<IAlertSyncService, AlertSyncService>();

        // FS-08 §8, IP-10 T-151: AlertSnapshots:StoragePath is validated for shape (ValidateOnStart)
        // and, separately, for actual filesystem usability (IAlertSnapshotStoragePathValidator, run
        // explicitly from Program.cs before the host starts serving requests) — mirrors
        // DeviceSecretDataProtectionOptions/DataProtectionKeyPathValidator's exact pattern above.
        services.AddSingleton<IValidateOptions<AlertSnapshotStorageOptions>, AlertSnapshotStorageOptionsValidator>();
        services.AddOptions<AlertSnapshotStorageOptions>()
            .Bind(configuration.GetSection(AlertSnapshotStorageOptions.SectionName))
            .ValidateOnStart();
        services.AddSingleton<IAlertSnapshotStoragePathValidator, AlertSnapshotStoragePathValidator>();

        // Stateless apart from the (singleton) IOptions it reads StoragePath from — safe and
        // efficient as a singleton, mirroring IDeviceSecretProtector's registration.
        services.AddSingleton<IAlertSnapshotStorage, FileSystemAlertSnapshotStorage>();

        // The snapshot upload validation/storage/attach pipeline for POST
        // /api/v1/alerts/{alertId}/snapshot (FS-08 §9, IP-10 T-153). Depends on the (scoped)
        // DbContext and the (already-registered, singleton) IAlertSnapshotStorage/TimeProvider, so it
        // is scoped.
        services.AddScoped<IAlertSnapshotUploadService, AlertSnapshotUploadService>();

        // The read counterpart for GET /api/v1/alerts/{alertId}/snapshot (FS-08 §12, IP-10 T-160).
        // Same scoping rationale as IAlertSnapshotUploadService above (scoped DbContext + singleton
        // IAlertSnapshotStorage).
        services.AddScoped<IAlertSnapshotRetrievalService, AlertSnapshotRetrievalService>();

        return services;
    }
}

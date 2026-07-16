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

        // ASP.NET Core Data Protection's IDataProtector is thread-safe and designed for
        // long-lived reuse — safe and efficient as a singleton.
        services.AddDataProtection();
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
        // for the device read path (GetDeviceByDeviceIdAsync, T-16) — its provisioning step remains
        // pure and simply does not touch the context. Later tasks (T-17/T-19) add more
        // database-backed operations on the same lifetime.
        services.AddScoped<IDeviceService, DeviceService>();
        services.AddScoped<IBranchService, BranchService>();

        return services;
    }
}

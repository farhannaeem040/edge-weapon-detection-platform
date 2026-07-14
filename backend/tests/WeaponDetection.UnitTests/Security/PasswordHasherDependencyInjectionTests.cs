using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using WeaponDetection.Application.Interfaces;
using WeaponDetection.Infrastructure;
using WeaponDetection.Infrastructure.Security;
using WeaponDetection.Infrastructure.Services;
using WeaponDetection.Infrastructure.Startup;
using Xunit;

namespace WeaponDetection.UnitTests.Security;

public class PasswordHasherDependencyInjectionTests
{
    private const string TestConnectionString =
        "Server=localhost;Database=WeaponDetectionUnitTests;Trusted_Connection=True;TrustServerCertificate=True;";

    private static IConfiguration EmptyConfiguration() => new ConfigurationBuilder().Build();

    [Fact]
    public void AddInfrastructure_ResolvesIPasswordHasher_ToPbkdf2Implementation()
    {
        var services = new ServiceCollection();

        services.AddInfrastructure(TestConnectionString, EmptyConfiguration());

        using var provider = services.BuildServiceProvider();
        var hasher = provider.GetService<IPasswordHasher>();

        Assert.NotNull(hasher);
        Assert.IsType<Pbkdf2PasswordHasher>(hasher);
    }

    [Fact]
    public void AddInfrastructure_ResolvesIDeviceSecretProtector_ToDataProtectionImplementation()
    {
        var services = new ServiceCollection();

        services.AddInfrastructure(TestConnectionString, EmptyConfiguration());

        using var provider = services.BuildServiceProvider();
        var protector = provider.GetService<IDeviceSecretProtector>();

        Assert.NotNull(protector);
        Assert.IsType<DataProtectionDeviceSecretProtector>(protector);
    }

    [Fact]
    public void AddInfrastructure_ResolvesIAdminBootstrapper_ToAdminBootstrapperImplementation()
    {
        var services = new ServiceCollection();
        services.AddSingleton<IConfiguration>(EmptyConfiguration());
        services.AddLogging();

        services.AddInfrastructure(TestConnectionString, EmptyConfiguration());

        using var provider = services.BuildServiceProvider();
        using var scope = provider.CreateScope();
        var bootstrapper = scope.ServiceProvider.GetService<IAdminBootstrapper>();

        Assert.NotNull(bootstrapper);
        Assert.IsType<AdminBootstrapper>(bootstrapper);
    }

    [Fact]
    public void AddInfrastructure_ResolvesIJwtIssuer_ToJwtIssuerImplementation()
    {
        var services = new ServiceCollection();
        var configuration = new ConfigurationBuilder()
            .AddInMemoryCollection(new Dictionary<string, string?>
            {
                ["Jwt:Issuer"] = "test-issuer",
                ["Jwt:Audience"] = "test-audience",
                ["Jwt:SigningKey"] = new string('k', 32),
                ["Jwt:AccessTokenLifetimeMinutes"] = "60",
            })
            .Build();

        services.AddInfrastructure(TestConnectionString, configuration);

        using var provider = services.BuildServiceProvider();
        var issuer = provider.GetService<IJwtIssuer>();

        Assert.NotNull(issuer);
        Assert.IsType<JwtIssuer>(issuer);
    }

    [Fact]
    public void AddInfrastructure_ResolvesIAuthService_ToAuthServiceImplementation()
    {
        var services = new ServiceCollection();
        var configuration = new ConfigurationBuilder()
            .AddInMemoryCollection(new Dictionary<string, string?>
            {
                ["Jwt:Issuer"] = "test-issuer",
                ["Jwt:Audience"] = "test-audience",
                ["Jwt:SigningKey"] = new string('k', 32),
                ["Jwt:AccessTokenLifetimeMinutes"] = "60",
            })
            .Build();

        services.AddInfrastructure(TestConnectionString, configuration);

        using var provider = services.BuildServiceProvider();
        using var scope = provider.CreateScope();
        var authService = scope.ServiceProvider.GetService<IAuthService>();

        Assert.NotNull(authService);
        Assert.IsType<AuthService>(authService);
    }
}

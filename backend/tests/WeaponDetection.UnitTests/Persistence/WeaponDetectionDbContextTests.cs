using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using WeaponDetection.Infrastructure;
using WeaponDetection.Infrastructure.Persistence;
using Xunit;

namespace WeaponDetection.UnitTests.Persistence;

public class WeaponDetectionDbContextTests
{
    private const string TestConnectionString =
        "Server=localhost;Database=WeaponDetectionUnitTests;Trusted_Connection=True;TrustServerCertificate=True;";

    private static IConfiguration EmptyConfiguration() => new ConfigurationBuilder().Build();

    [Fact]
    public void AddInfrastructure_ResolvesDbContext_ThroughDependencyInjection()
    {
        var services = new ServiceCollection();

        services.AddInfrastructure(TestConnectionString, EmptyConfiguration());

        using var provider = services.BuildServiceProvider();
        var context = provider.GetService<WeaponDetectionDbContext>();

        Assert.NotNull(context);
    }

    [Fact]
    public void AddInfrastructure_ConfiguresSqlServerProvider()
    {
        var services = new ServiceCollection();

        services.AddInfrastructure(TestConnectionString, EmptyConfiguration());

        using var provider = services.BuildServiceProvider();
        var context = provider.GetRequiredService<WeaponDetectionDbContext>();

        Assert.Equal("Microsoft.EntityFrameworkCore.SqlServer", context.Database.ProviderName);
    }
}

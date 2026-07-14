using System;
using System.Collections.Generic;
using System.Threading.Tasks;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Options;
using WeaponDetection.Infrastructure;
using Xunit;

namespace WeaponDetection.IntegrationTests.Startup;

// Verifies that AddInfrastructure's ValidateOnStart() wiring (IP-01 §7) actually enforces JWT
// configuration validity during real host startup — not merely when JwtOptionsValidator is
// invoked directly in isolation. No real SQL Server connection is required: AddDbContext only
// registers a factory, it does not open a connection during Build()/StartAsync().
public class JwtOptionsStartupValidationTests
{
    private const string TestConnectionString =
        "Server=localhost;Database=WeaponDetectionUnitTests;Trusted_Connection=True;TrustServerCertificate=True;";

    private static IHost BuildHost(IDictionary<string, string?> jwtConfig)
    {
        var builder = Host.CreateApplicationBuilder();
        var configuration = new ConfigurationBuilder().AddInMemoryCollection(jwtConfig).Build();
        builder.Services.AddInfrastructure(TestConnectionString, configuration);

        return builder.Build();
    }

    [Fact]
    public async Task StartAsync_ValidJwtConfiguration_StartsSuccessfully()
    {
        using var host = BuildHost(new Dictionary<string, string?>
        {
            ["Jwt:Issuer"] = "test-issuer",
            ["Jwt:Audience"] = "test-audience",
            ["Jwt:SigningKey"] = new string('k', 32),
            ["Jwt:AccessTokenLifetimeMinutes"] = "60",
        });

        await host.StartAsync();
        await host.StopAsync();
    }

    [Fact]
    public async Task StartAsync_MissingSigningKey_FailsStartupBeforeServingRequests()
    {
        using var host = BuildHost(new Dictionary<string, string?>
        {
            ["Jwt:Issuer"] = "test-issuer",
            ["Jwt:Audience"] = "test-audience",
            ["Jwt:AccessTokenLifetimeMinutes"] = "60",
        });

        await Assert.ThrowsAsync<OptionsValidationException>(() => host.StartAsync());
    }

    [Fact]
    public async Task StartAsync_MissingIssuer_FailsStartup()
    {
        using var host = BuildHost(new Dictionary<string, string?>
        {
            ["Jwt:Audience"] = "test-audience",
            ["Jwt:SigningKey"] = new string('k', 32),
            ["Jwt:AccessTokenLifetimeMinutes"] = "60",
        });

        await Assert.ThrowsAsync<OptionsValidationException>(() => host.StartAsync());
    }

    [Fact]
    public async Task StartAsync_InvalidLifetime_FailsStartup()
    {
        using var host = BuildHost(new Dictionary<string, string?>
        {
            ["Jwt:Issuer"] = "test-issuer",
            ["Jwt:Audience"] = "test-audience",
            ["Jwt:SigningKey"] = new string('k', 32),
            ["Jwt:AccessTokenLifetimeMinutes"] = "0",
        });

        await Assert.ThrowsAsync<OptionsValidationException>(() => host.StartAsync());
    }
}

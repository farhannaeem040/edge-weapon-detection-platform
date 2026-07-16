using Microsoft.AspNetCore.DataProtection;
using Microsoft.Extensions.DependencyInjection;
using WeaponDetection.Infrastructure.Security;

namespace WeaponDetection.IntegrationTests.Services;

// Builds a real DataProtectionDeviceSecretProtector for the service-level integration tests, the
// same way production wires it (AddDataProtection() in DependencyInjection.cs) rather than
// instantiating a data-protection provider by an unsupported path. Shared by the service tests that
// construct a DeviceService directly so each does not repeat the setup.
internal static class TestDeviceSecretProtector
{
    public static DataProtectionDeviceSecretProtector Create()
    {
        var services = new ServiceCollection();
        services.AddDataProtection();
        var provider = services.BuildServiceProvider();

        return new DataProtectionDeviceSecretProtector(
            provider.GetRequiredService<IDataProtectionProvider>());
    }
}

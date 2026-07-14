using Microsoft.AspNetCore.DataProtection;
using Microsoft.Extensions.DependencyInjection;
using WeaponDetection.Infrastructure.Security;
using Xunit;

namespace WeaponDetection.UnitTests.Security;

// No test in this file prints a shared secret or protected value to console/log output —
// assertions compare values in-memory only.
public class DataProtectionDeviceSecretProtectorTests
{
    private const string PlaintextSecret = "device-shared-secret-value-12345";

    // Resolved the same way as production (AddDataProtection() in DependencyInjection.cs),
    // rather than instantiating a data protection provider by an unsupported path.
    private static DataProtectionDeviceSecretProtector CreateProtector()
    {
        var services = new ServiceCollection();
        services.AddDataProtection();
        var provider = services.BuildServiceProvider();

        return new DataProtectionDeviceSecretProtector(provider.GetRequiredService<IDataProtectionProvider>());
    }

    [Fact]
    public void Protect_ThenUnprotect_RoundTripsToOriginalPlaintext()
    {
        var protector = CreateProtector();

        var protectedValue = protector.Protect(PlaintextSecret);
        var recovered = protector.Unprotect(protectedValue);

        Assert.Equal(PlaintextSecret, recovered);
    }

    [Fact]
    public void Protect_ValidSecret_ProtectedValueIsNotThePlaintext()
    {
        var protector = CreateProtector();

        var protectedValue = protector.Protect(PlaintextSecret);

        Assert.NotEqual(PlaintextSecret, protectedValue);
    }

    [Fact]
    public void Protect_SameSecretTwice_ProducesDifferentProtectedValues()
    {
        var protector = CreateProtector();

        var first = protector.Protect(PlaintextSecret);
        var second = protector.Protect(PlaintextSecret);

        Assert.NotEqual(first, second);
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    public void Protect_EmptyOrWhitespaceSecret_Throws(string secret)
    {
        var protector = CreateProtector();

        Assert.Throws<ArgumentException>(() => protector.Protect(secret));
    }

    [Fact]
    public void Protect_NullSecret_Throws()
    {
        var protector = CreateProtector();

        Assert.Throws<ArgumentException>(() => protector.Protect(null!));
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    public void Unprotect_EmptyOrWhitespaceValue_Throws(string protectedValue)
    {
        var protector = CreateProtector();

        Assert.Throws<ArgumentException>(() => protector.Unprotect(protectedValue));
    }

    [Fact]
    public void Unprotect_NullValue_Throws()
    {
        var protector = CreateProtector();

        Assert.Throws<ArgumentException>(() => protector.Unprotect(null!));
    }

    [Fact]
    public void Unprotect_MalformedProtectedValue_ThrowsCryptographicException()
    {
        var protector = CreateProtector();

        Assert.ThrowsAny<Exception>(() => protector.Unprotect("not-a-real-protected-value"));
    }
}

using System;
using System.Threading.Tasks;
using Microsoft.EntityFrameworkCore;
using WeaponDetection.Application.Interfaces;
using WeaponDetection.Infrastructure.Persistence;
using WeaponDetection.Infrastructure.Services;
using Xunit;

namespace WeaponDetection.UnitTests.Services;

// The input-validation short-circuits of DeviceCredentialValidator (IP-05 T-51): a missing DeviceId
// or a missing/empty secret is rejected before any database lookup. These are pure unit tests — the
// DbContext is configured with a placeholder connection string that is never opened, because the
// service returns before it queries, and the secret protector is a stub that fails if it is ever
// reached (proving these paths never touch the credential-comparison code).
//
// The database-backed outcomes (valid credentials, unknown device, ReactivationRequired, secret
// mismatch, inconsistent state) are verified against real SQL Server in the IntegrationTests project,
// following this repository's convention that any query/constraint behaviour is proven on real SQL
// Server rather than an in-memory provider (IP-01 §9).
public class DeviceCredentialValidatorInputTests
{
    private static WeaponDetectionDbContext CreateNeverOpenedContext()
    {
        var options = new DbContextOptionsBuilder<WeaponDetectionDbContext>()
            .UseSqlServer(
                "Server=localhost;Database=WeaponDetectionCredValidatorUnitTests;" +
                "Trusted_Connection=True;TrustServerCertificate=True;")
            .Options;
        return new WeaponDetectionDbContext(options);
    }

    private static DeviceCredentialValidator CreateValidator(WeaponDetectionDbContext context) =>
        new(context, new UnreachableDeviceSecretProtector());

    [Fact]
    public async Task ValidateAsync_EmptyDeviceId_ReturnsMissingDeviceId_WithoutQueryingOrComparing()
    {
        using var context = CreateNeverOpenedContext();
        var validator = CreateValidator(context);

        var result = await validator.ValidateAsync(Guid.Empty, "any-secret-placeholder");

        Assert.False(result.IsValid);
        Assert.Equal(DeviceCredentialValidationOutcome.MissingDeviceId, result.Outcome);
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    public async Task ValidateAsync_MissingSecret_ReturnsMissingSecret_WithoutQueryingOrComparing(
        string? presentedSecret)
    {
        using var context = CreateNeverOpenedContext();
        var validator = CreateValidator(context);

        var result = await validator.ValidateAsync(Guid.NewGuid(), presentedSecret);

        Assert.False(result.IsValid);
        Assert.Equal(DeviceCredentialValidationOutcome.MissingSecret, result.Outcome);
    }

    [Fact]
    public void Result_Invalid_RejectsTheValidOutcome()
    {
        // A failure result can never claim the Valid outcome.
        Assert.Throws<ArgumentException>(
            () => DeviceCredentialValidationResult.Invalid(DeviceCredentialValidationOutcome.Valid));
    }

    [Fact]
    public void Result_ToString_CarriesOnlyTheOutcome_NoSecretMaterial()
    {
        var text = DeviceCredentialValidationResult
            .Invalid(DeviceCredentialValidationOutcome.SecretMismatch).ToString();

        Assert.Contains("SecretMismatch", text);
        Assert.DoesNotContain("secret-", text, StringComparison.OrdinalIgnoreCase);
    }

    // A protector that must never be called on the short-circuit paths — reaching it is a bug, because
    // input validation happens before the stored secret is ever recovered or compared.
    private sealed class UnreachableDeviceSecretProtector : IDeviceSecretProtector
    {
        public string Protect(string plaintextSecret) =>
            throw new InvalidOperationException("Protect must not be reached on an input short-circuit.");

        public string Unprotect(string protectedSecret) =>
            throw new InvalidOperationException("Unprotect must not be reached on an input short-circuit.");
    }
}

using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using WeaponDetection.Api.Contracts;
using WeaponDetection.Api.Controllers;
using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.UnitTests.Controllers;

// Controller-level unit tests for POST /api/v1/device/credentials/validate (FS-02 §10.5, IP-05
// T-52), added alongside FS-07's new CredentialStorageUnavailable branch (T-121) — the real
// database-backed validator behavior is covered by DeviceCredentialValidatorTests/
// DeviceCredentialValidationApiTests; this isolates only the controller's own status-code mapping.
public class DeviceCredentialValidationControllerTests
{
    private static readonly Guid ValidDeviceId = Guid.NewGuid();
    private const string ValidSecret = "device-shared-secret-placeholder-AAAAAAAA";

    private sealed class StubDeviceCredentialValidator : IDeviceCredentialValidator
    {
        public DeviceCredentialValidationOutcome? ForcedOutcome { get; init; }

        public Task<DeviceCredentialValidationResult> ValidateAsync(
            Guid deviceId, string? presentedSecret, CancellationToken cancellationToken = default)
        {
            if (ForcedOutcome is { } forcedOutcome)
            {
                return Task.FromResult(DeviceCredentialValidationResult.Invalid(forcedOutcome));
            }

            var isValid = deviceId == ValidDeviceId && presentedSecret == ValidSecret;
            return Task.FromResult(isValid
                ? DeviceCredentialValidationResult.Valid(Guid.NewGuid(), Guid.NewGuid())
                : DeviceCredentialValidationResult.Invalid(
                    DeviceCredentialValidationOutcome.SecretMismatch));
        }
    }

    private static DeviceCredentialValidationController CreateController(
        StubDeviceCredentialValidator validator, Guid? deviceId, string? secret)
    {
        var controller = new DeviceCredentialValidationController(validator)
        {
            ControllerContext = new ControllerContext { HttpContext = new DefaultHttpContext() },
        };

        if (deviceId is not null)
        {
            controller.Request.Headers["X-Device-Id"] = deviceId.Value.ToString();
        }

        if (secret is not null)
        {
            controller.Request.Headers["X-Device-Secret"] = secret;
        }

        return controller;
    }

    [Fact]
    public async Task Validate_ValidCredentials_ReturnsOk()
    {
        var controller = CreateController(new StubDeviceCredentialValidator(), ValidDeviceId, ValidSecret);

        var result = await controller.Validate(CancellationToken.None);

        Assert.IsType<OkObjectResult>(result);
    }

    [Fact]
    public async Task Validate_WrongSecret_Returns401UniformFailure()
    {
        var controller = CreateController(
            new StubDeviceCredentialValidator(), ValidDeviceId, "wrong-secret-value");

        var result = await controller.Validate(CancellationToken.None);

        var unauthorized = Assert.IsType<UnauthorizedObjectResult>(result);
        var envelope = Assert.IsType<ApiResponse>(unauthorized.Value);
        Assert.Equal("INVALID_DEVICE_CREDENTIALS", envelope.ErrorCode);
    }

    // FS-07 §3.4: the exact branch this remediation adds — must never be collapsed into the
    // confirmed-revocation 401.
    [Fact]
    public async Task Validate_CredentialStorageUnavailable_Returns503_NotTheUniform401()
    {
        var validator = new StubDeviceCredentialValidator
        {
            ForcedOutcome = DeviceCredentialValidationOutcome.CredentialStorageUnavailable,
        };
        var controller = CreateController(validator, ValidDeviceId, ValidSecret);

        var result = await controller.Validate(CancellationToken.None);

        var objectResult = Assert.IsType<ObjectResult>(result);
        Assert.Equal(StatusCodes.Status503ServiceUnavailable, objectResult.StatusCode);
        var envelope = Assert.IsType<ApiResponse>(objectResult.Value);
        Assert.False(envelope.Success);
        Assert.Equal("DEVICE_AUTHENTICATION_UNAVAILABLE", envelope.ErrorCode);
    }
}

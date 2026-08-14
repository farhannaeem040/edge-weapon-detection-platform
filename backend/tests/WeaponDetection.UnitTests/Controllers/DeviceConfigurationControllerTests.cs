using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using WeaponDetection.Api.Contracts;
using WeaponDetection.Api.Controllers;
using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.UnitTests.Controllers;

// Controller-level unit tests for GET /api/v1/device/configuration (FS-11 §3, IP-13 T-224) — isolates
// only the controller's status-code mapping and BranchId sourcing; the real database-backed
// resolution/version-hash behavior is covered by DeviceConfigurationApiTests.
public class DeviceConfigurationControllerTests
{
    private static readonly Guid ValidDeviceId = Guid.NewGuid();
    private static readonly Guid ValidBranchId = Guid.NewGuid();
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
                ? DeviceCredentialValidationResult.Valid(ValidBranchId, Guid.NewGuid())
                : DeviceCredentialValidationResult.Invalid(
                    DeviceCredentialValidationOutcome.SecretMismatch));
        }
    }

    private sealed class StubDeviceConfigurationService : IDeviceConfigurationService
    {
        public Guid? ReceivedDeviceId { get; private set; }
        public Guid? ReceivedBranchId { get; private set; }

        public Task<DeviceConfigurationView> GetConfigurationAsync(
            Guid deviceId, Guid branchId, CancellationToken cancellationToken = default)
        {
            ReceivedDeviceId = deviceId;
            ReceivedBranchId = branchId;
            return Task.FromResult(new DeviceConfigurationView(
                1, "version-hash", deviceId, branchId, DateTime.UtcNow, []));
        }
    }

    private static (DeviceConfigurationController Controller, StubDeviceConfigurationService Service) CreateController(
        StubDeviceCredentialValidator validator, Guid? deviceId, string? secret)
    {
        var service = new StubDeviceConfigurationService();
        var controller = new DeviceConfigurationController(validator, service)
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

        return (controller, service);
    }

    [Fact]
    public async Task Get_ValidCredentials_ReturnsOk()
    {
        var (controller, _) = CreateController(new StubDeviceCredentialValidator(), ValidDeviceId, ValidSecret);

        var result = await controller.Get(CancellationToken.None);

        Assert.IsType<OkObjectResult>(result);
    }

    [Fact]
    public async Task Get_ValidCredentials_PassesTheValidatorsBranchId_NeverAClientSuppliedOne()
    {
        // FS-11 §3: a Device can never fetch another Branch's configuration — the only BranchId the
        // service ever sees is the one the validator itself resolved from the credential.
        var (controller, service) = CreateController(new StubDeviceCredentialValidator(), ValidDeviceId, ValidSecret);

        await controller.Get(CancellationToken.None);

        Assert.Equal(ValidBranchId, service.ReceivedBranchId);
        Assert.Equal(ValidDeviceId, service.ReceivedDeviceId);
    }

    [Fact]
    public async Task Get_WrongSecret_Returns401UniformFailure()
    {
        var (controller, _) = CreateController(
            new StubDeviceCredentialValidator(), ValidDeviceId, "wrong-secret-value");

        var result = await controller.Get(CancellationToken.None);

        var unauthorized = Assert.IsType<UnauthorizedObjectResult>(result);
        var envelope = Assert.IsType<ApiResponse>(unauthorized.Value);
        Assert.Equal("INVALID_DEVICE_CREDENTIALS", envelope.ErrorCode);
    }

    [Fact]
    public async Task Get_CredentialStorageUnavailable_Returns503_NotTheUniform401()
    {
        var validator = new StubDeviceCredentialValidator
        {
            ForcedOutcome = DeviceCredentialValidationOutcome.CredentialStorageUnavailable,
        };
        var (controller, _) = CreateController(validator, ValidDeviceId, ValidSecret);

        var result = await controller.Get(CancellationToken.None);

        var objectResult = Assert.IsType<ObjectResult>(result);
        Assert.Equal(StatusCodes.Status503ServiceUnavailable, objectResult.StatusCode);
        var envelope = Assert.IsType<ApiResponse>(objectResult.Value);
        Assert.False(envelope.Success);
        Assert.Equal("DEVICE_AUTHENTICATION_UNAVAILABLE", envelope.ErrorCode);
    }
}

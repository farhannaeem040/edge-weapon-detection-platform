using Microsoft.AspNetCore.Mvc;
using WeaponDetection.Api.Contracts;
using WeaponDetection.Api.Controllers;
using WeaponDetection.Application.Exceptions;
using WeaponDetection.Application.Interfaces;
using WeaponDetection.Domain;

namespace WeaponDetection.UnitTests.Controllers;

public class DeviceControllerTests
{
    private sealed class StubDeviceService : IDeviceService
    {
        public Func<Guid, CancellationToken, Task<ActivationKeyRegenerationResult?>>? RegenerateHandler { get; init; }

        public DeviceProvisioning ProvisionForBranch(Guid branchId) =>
            throw new NotSupportedException();

        public Task<ActivationKeyRegenerationResult?> RegenerateActivationKeyAsync(
            Guid branchId,
            CancellationToken cancellationToken = default)
        {
            if (RegenerateHandler is null)
            {
                throw new NotSupportedException();
            }

            return RegenerateHandler(branchId, cancellationToken);
        }

        public Task<DeviceActivationResult> ActivateAsync(
            string activationKey,
            CancellationToken cancellationToken = default) =>
            throw new NotSupportedException();

        public Task<DeviceDetailView?> GetDeviceByDeviceIdAsync(
            Guid deviceId,
            CancellationToken cancellationToken = default) =>
            throw new NotSupportedException();

        public Task<DeviceNetworkUpdate?> SetNetworkConfigurationAsync(
            Guid branchId,
            string? jetsonHost,
            int? rtspOutputPort,
            CancellationToken cancellationToken = default) =>
            throw new NotSupportedException();
    }

    [Fact]
    public async Task RegenerateActivationKey_WhenTypedConflictOccurs_Returns409Contract()
    {
        var service = new StubDeviceService
        {
            RegenerateHandler = (_, _) => throw new ActivationKeyRegenerationConflictException(),
        };
        var controller = new DeviceController(service);

        var result = await controller.RegenerateActivationKey(Guid.NewGuid(), CancellationToken.None);

        var conflict = Assert.IsType<ConflictObjectResult>(result);
        Assert.Equal(409, conflict.StatusCode);

        var envelope = Assert.IsType<ApiResponse>(conflict.Value);
        Assert.False(envelope.Success);
        Assert.Equal(
            "Another activation key regeneration request completed concurrently. Refresh the device and try again.",
            envelope.Message);
        Assert.Equal("ACTIVATION_KEY_REGENERATION_CONFLICT", envelope.ErrorCode);
        Assert.Null(envelope.Data);
    }

    [Fact]
    public async Task RegenerateActivationKey_WhenTypedConflictOccurs_ReturnsNoCredentialOrSecretData()
    {
        var service = new StubDeviceService
        {
            RegenerateHandler = (_, _) => throw new ActivationKeyRegenerationConflictException(),
        };
        var controller = new DeviceController(service);

        var result = await controller.RegenerateActivationKey(Guid.NewGuid(), CancellationToken.None);

        var conflict = Assert.IsType<ConflictObjectResult>(result);
        var envelope = Assert.IsType<ApiResponse>(conflict.Value);
        Assert.Null(envelope.Data);
        Assert.DoesNotContain("activationKey", envelope.Message, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("secret", envelope.Message, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("database", envelope.Message, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("index", envelope.Message, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("exception", envelope.Message, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public async Task RegenerateActivationKey_WhenUnrelatedExceptionOccurs_DoesNotMapTo409()
    {
        var service = new StubDeviceService
        {
            RegenerateHandler = (_, _) => throw new InvalidOperationException("ordinary failure"),
        };
        var controller = new DeviceController(service);

        await Assert.ThrowsAsync<InvalidOperationException>(
            () => controller.RegenerateActivationKey(Guid.NewGuid(), CancellationToken.None));
    }

    [Fact]
    public async Task RegenerateActivationKey_WhenBranchIsUnknown_Returns404Unchanged()
    {
        var service = new StubDeviceService
        {
            RegenerateHandler = (_, _) => Task.FromResult<ActivationKeyRegenerationResult?>(null),
        };
        var controller = new DeviceController(service);

        var result = await controller.RegenerateActivationKey(Guid.NewGuid(), CancellationToken.None);

        var notFound = Assert.IsType<NotFoundObjectResult>(result);
        var envelope = Assert.IsType<ApiResponse>(notFound.Value);
        Assert.False(envelope.Success);
        Assert.Equal("NOT_FOUND", envelope.ErrorCode);
    }

    [Fact]
    public async Task RegenerateActivationKey_WhenSuccessful_ReturnsOkWithPlaintextKeyUnchanged()
    {
        const string plaintextKey = "public-key-id.private-key-secret";
        var service = new StubDeviceService
        {
            RegenerateHandler = (_, _) =>
                Task.FromResult<ActivationKeyRegenerationResult?>(new ActivationKeyRegenerationResult(plaintextKey)),
        };
        var controller = new DeviceController(service);

        var result = await controller.RegenerateActivationKey(Guid.NewGuid(), CancellationToken.None);

        var ok = Assert.IsType<OkObjectResult>(result);
        var dto = Assert.IsType<RegenerateActivationKeyResponseDto>(ok.Value);
        Assert.Equal(plaintextKey, dto.ActivationKey);
    }
}

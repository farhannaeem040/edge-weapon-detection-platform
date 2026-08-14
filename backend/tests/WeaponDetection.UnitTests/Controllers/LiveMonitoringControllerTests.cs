using Microsoft.AspNetCore.Mvc;
using WeaponDetection.Api.Contracts;
using WeaponDetection.Api.Controllers;
using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.UnitTests.Controllers;

// FS-14 §5, IP-16 T-9. Controller-layer tests against a stub ILiveStreamService — no HTTP pipeline,
// no database, no gateway. Mirrors AlertControllerTests/AlertSnapshotUploadControllerTests' style.
public class LiveMonitoringControllerTests
{
    private sealed class StubLiveStreamService : ILiveStreamService
    {
        public Func<Guid, IReadOnlyList<LiveMonitoringCameraView>>? ListHandler { get; init; }
        public Func<LiveStreamRequest, LiveStreamOutcome>? CreateHandler { get; init; }
        public LiveStreamRequest? ReceivedRequest { get; private set; }

        public Task<IReadOnlyList<LiveMonitoringCameraView>> ListCamerasAsync(
            Guid branchId, CancellationToken cancellationToken = default) =>
            Task.FromResult(ListHandler?.Invoke(branchId) ?? []);

        public Task<LiveStreamOutcome> CreateStreamAsync(
            LiveStreamRequest request, CancellationToken cancellationToken = default)
        {
            ReceivedRequest = request;
            return Task.FromResult(
                CreateHandler?.Invoke(request)
                    ?? LiveStreamOutcome.Created(Guid.NewGuid(), "/media/x/whep", DateTime.UtcNow.AddMinutes(1)));
        }
    }

    [Fact]
    public async Task ListCameras_ReturnsProjectedDtos()
    {
        var cameraId = Guid.NewGuid();
        var service = new StubLiveStreamService
        {
            ListHandler = _ => [new LiveMonitoringCameraView(cameraId, "Front Camera", "front-camera", true, true)],
        };
        var controller = new LiveMonitoringController(service);

        var result = await controller.ListCameras(Guid.NewGuid(), CancellationToken.None);

        var ok = Assert.IsType<OkObjectResult>(result);
        var dtos = Assert.IsType<List<LiveMonitoringCameraDto>>(ok.Value);
        Assert.Single(dtos);
        Assert.Equal(cameraId, dtos[0].CameraId);
        Assert.True(dtos[0].MonitoringAvailable);
    }

    [Fact]
    public async Task CreateStream_InvalidMode_Returns400WithoutCallingService()
    {
        var called = false;
        var service = new StubLiveStreamService { CreateHandler = _ => { called = true; return LiveStreamOutcome.Created(Guid.NewGuid(), "/media/x/whep", DateTime.UtcNow); } };
        var controller = new LiveMonitoringController(service);
        var request = new CreateLiveStreamRequestDto(Guid.NewGuid(), Guid.NewGuid(), "recording");

        var result = await controller.CreateStream(request, CancellationToken.None);

        var badRequest = Assert.IsType<BadRequestObjectResult>(result);
        var envelope = Assert.IsType<ApiResponse>(badRequest.Value);
        Assert.Equal("VALIDATION_ERROR", envelope.ErrorCode);
        Assert.False(called);
    }

    [Fact]
    public async Task CreateStream_PassesParsedModeToService()
    {
        var service = new StubLiveStreamService();
        var controller = new LiveMonitoringController(service);
        var cameraId = Guid.NewGuid();
        var request = new CreateLiveStreamRequestDto(Guid.NewGuid(), cameraId, "Inference");

        await controller.CreateStream(request, CancellationToken.None);

        Assert.Equal(LiveStreamMode.Inference, service.ReceivedRequest!.Mode);
        Assert.Equal(cameraId, service.ReceivedRequest.CameraId);
    }

    [Fact]
    public async Task CreateStream_Created_ReturnsOkWithPlaybackUrl()
    {
        var service = new StubLiveStreamService
        {
            CreateHandler = _ => LiveStreamOutcome.Created(Guid.NewGuid(), "/media/live-x-monitoring/whep", DateTime.UtcNow.AddMinutes(1)),
        };
        var controller = new LiveMonitoringController(service);
        var request = new CreateLiveStreamRequestDto(Guid.NewGuid(), Guid.NewGuid(), "monitoring");

        var result = await controller.CreateStream(request, CancellationToken.None);

        var ok = Assert.IsType<OkObjectResult>(result);
        var dto = Assert.IsType<LiveStreamResponseDto>(ok.Value);
        Assert.Equal("/media/live-x-monitoring/whep", dto.PlaybackUrl);
    }

    [Fact]
    public async Task CreateStream_NotFound_Returns404()
    {
        var service = new StubLiveStreamService
        {
            CreateHandler = _ => LiveStreamOutcome.Failed(LiveStreamOutcomeKind.BranchOrCameraNotFound),
        };
        var controller = new LiveMonitoringController(service);
        var request = new CreateLiveStreamRequestDto(Guid.NewGuid(), Guid.NewGuid(), "monitoring");

        var result = await controller.CreateStream(request, CancellationToken.None);

        var notFound = Assert.IsType<NotFoundObjectResult>(result);
        Assert.Equal("NOT_FOUND", Assert.IsType<ApiResponse>(notFound.Value).ErrorCode);
    }

    [Fact]
    public async Task CreateStream_DeviceUnavailable_Returns422()
    {
        var service = new StubLiveStreamService
        {
            CreateHandler = _ => LiveStreamOutcome.Failed(LiveStreamOutcomeKind.DeviceUnavailable),
        };
        var controller = new LiveMonitoringController(service);
        var request = new CreateLiveStreamRequestDto(Guid.NewGuid(), Guid.NewGuid(), "inference");

        var result = await controller.CreateStream(request, CancellationToken.None);

        var unprocessable = Assert.IsType<UnprocessableEntityObjectResult>(result);
        Assert.Equal("DEVICE_UNAVAILABLE", Assert.IsType<ApiResponse>(unprocessable.Value).ErrorCode);
    }

    [Fact]
    public async Task CreateStream_GatewayUnavailable_Returns502()
    {
        var service = new StubLiveStreamService
        {
            CreateHandler = _ => LiveStreamOutcome.Failed(LiveStreamOutcomeKind.GatewayUnavailable),
        };
        var controller = new LiveMonitoringController(service);
        var request = new CreateLiveStreamRequestDto(Guid.NewGuid(), Guid.NewGuid(), "monitoring");

        var result = await controller.CreateStream(request, CancellationToken.None);

        var objectResult = Assert.IsType<ObjectResult>(result);
        Assert.Equal(502, objectResult.StatusCode);
        Assert.Equal("GATEWAY_UNAVAILABLE", Assert.IsType<ApiResponse>(objectResult.Value).ErrorCode);
    }
}

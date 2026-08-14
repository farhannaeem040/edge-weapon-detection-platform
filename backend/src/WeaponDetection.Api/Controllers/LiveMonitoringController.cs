using Microsoft.AspNetCore.Mvc;
using WeaponDetection.Api.Contracts;
using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.Api.Controllers;

// FS-14 §5, IP-16 T-7. Branch Live Monitoring / Live Inference Viewer. All source resolution and
// media-gateway orchestration lives in ILiveStreamService; this controller only translates HTTP.
//
// No [AllowAnonymous] on either action, so both inherit the application's default/fallback
// ActiveAdminSessionRequirement policy exactly like AlertController/BranchController — a
// device-credentialed or unauthenticated request is rejected before either action body runs.
[ApiController]
public class LiveMonitoringController : ControllerBase
{
    private readonly ILiveStreamService _liveStreamService;

    public LiveMonitoringController(ILiveStreamService liveStreamService)
    {
        _liveStreamService = liveStreamService;
    }

    [HttpGet("api/v1/branches/{branchId:guid}/live-monitoring/cameras")]
    public async Task<IActionResult> ListCameras(Guid branchId, CancellationToken cancellationToken)
    {
        var cameras = await _liveStreamService.ListCamerasAsync(branchId, cancellationToken);
        return Ok(cameras.Select(LiveMonitoringCameraDto.From).ToList());
    }

    [HttpPost("api/v1/live-streams")]
    public async Task<IActionResult> CreateStream(
        [FromBody] CreateLiveStreamRequestDto request, CancellationToken cancellationToken)
    {
        if (!Enum.TryParse<LiveStreamMode>(request.Mode, ignoreCase: true, out var mode)
            || !Enum.IsDefined(mode))
        {
            return BadRequest(ApiResponse.Fail(
                "VALIDATION_ERROR", "mode must be one of: monitoring, inference."));
        }

        var outcome = await _liveStreamService.CreateStreamAsync(
            new LiveStreamRequest(request.BranchId, request.CameraId, mode), cancellationToken);

        return outcome.Kind switch
        {
            LiveStreamOutcomeKind.Created => Ok(new LiveStreamResponseDto(
                outcome.SessionId!.Value, request.CameraId, request.Mode.ToLowerInvariant(),
                outcome.PlaybackUrl!, outcome.ExpiresAtUtc!.Value)),
            LiveStreamOutcomeKind.BranchOrCameraNotFound =>
                NotFound(ApiResponse.Fail("NOT_FOUND", "Camera not found.")),
            LiveStreamOutcomeKind.DeviceUnavailable => UnprocessableEntity(ApiResponse.Fail(
                "DEVICE_UNAVAILABLE",
                "This Branch's Device has not been configured with a Jetson host/port yet.")),
            _ => StatusCode(
                StatusCodes.Status502BadGateway,
                ApiResponse.Fail("GATEWAY_UNAVAILABLE", "The media gateway is unavailable.")),
        };
    }
}

using Microsoft.AspNetCore.Mvc;
using WeaponDetection.Api.Contracts;
using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.Api.Controllers;

// FS-02 §10.3, IP-01 §10/T-16. The device read endpoint. Like BranchController it carries no
// [AllowAnonymous], so the fallback policy (T-10) enforces Admin JWT + active session before any
// action runs.
//
// A device is addressable here only by its external DeviceId — the internal DeviceRecordId is never
// exposed by any API (FS-02 §1.3), and DeviceId exists only once a device has activated. An
// unactivated device therefore has no address on this route and is viewed through its branch
// instead; an unknown or unassigned id yields 404.
[ApiController]
[Route("api/v1/devices")]
public class DeviceController : ControllerBase
{
    private readonly IDeviceService _deviceService;

    public DeviceController(IDeviceService deviceService)
    {
        _deviceService = deviceService;
    }

    [HttpGet("{id:guid}")]
    public async Task<IActionResult> GetById(Guid id, CancellationToken cancellationToken)
    {
        var device = await _deviceService.GetDeviceByDeviceIdAsync(id, cancellationToken);
        if (device is null)
        {
            return NotFound(ApiResponse.Fail("NOT_FOUND", "Device not found."));
        }

        return Ok(DeviceDetailResponseDto.From(device));
    }
}

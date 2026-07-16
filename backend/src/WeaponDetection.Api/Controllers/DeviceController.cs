using Microsoft.AspNetCore.Mvc;
using WeaponDetection.Api.Contracts;
using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.Api.Controllers;

// FS-02 §10.3/§10.2, IP-01 §10/T-16/T-18. The device read endpoint and the Activation Key
// regeneration endpoint. Like BranchController it carries no [AllowAnonymous], so the fallback
// policy (T-10) enforces Admin JWT + active session before any action runs — the "Valid Admin JWT +
// active session" requirement of both §10.3 and §10.2.
//
// Two actions on this controller address a device by *different* identifiers, and the difference is
// forced by the identity model (FS-02 §1.3), not an oversight:
//
//  - GET {id} reads an activated device by its external, persistent DeviceId. That id exists only
//    once a device has activated, so an unactivated device has no address here and is viewed through
//    its branch instead.
//  - POST {id}/activation-key/regenerate addresses the device by its BRANCH id. Regeneration must
//    work before a device has ever activated (FS-02 §15 T-03, the never-consumed case), when its
//    DeviceId is still NULL and cannot address it; the internal DeviceRecordId is never exposed to
//    any client (FS-02 §1.3). The branch id is the only always-present, client-visible handle to the
//    branch's single device (BR-002/CON-007). The route path is the one IP-01 §10 fixes
//    (/api/v1/devices/{id}/activation-key/regenerate); the template variable is named branchId here
//    only to make the identifier's meaning explicit at the call site.
//
// The internal DeviceRecordId, stored secret hashes, and the protected shared secret are never
// serialized by either action — the response DTOs have no member to carry them (FS-02 §1.3, §11).
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

    // FS-02 §10.2: invalidates the branch's current Activation Key and issues a new one in a single
    // transaction (delegated to the T-17 service), returning 200 with the new complete plaintext key
    // — the only response that ever carries it. A branch/device that does not exist yields 404
    // (FS-02 §13). There is no request body: the only input is the branch identifier in the route,
    // so there is no §10.2 400/conflict case for this endpoint to produce beyond the route's own
    // {branchId:guid} constraint. The old key is never returned — regeneration has already
    // invalidated it, and this response exposes only the replacement (FS-02 §5.3).
    [HttpPost("{branchId:guid}/activation-key/regenerate")]
    public async Task<IActionResult> RegenerateActivationKey(
        Guid branchId, CancellationToken cancellationToken)
    {
        var result = await _deviceService.RegenerateActivationKeyAsync(branchId, cancellationToken);
        if (result is null)
        {
            return NotFound(ApiResponse.Fail("NOT_FOUND", "Device not found."));
        }

        return Ok(RegenerateActivationKeyResponseDto.From(result));
    }
}

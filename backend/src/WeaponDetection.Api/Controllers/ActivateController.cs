using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using WeaponDetection.Api.Contracts;
using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.Api.Controllers;

// FS-02 §10.4, IP-01 §10/T-20. The device activation endpoint — the one an Agent (real or simulated,
// FS-02 §1.2) calls to exchange an Activation Key for its persistent DeviceId, shared secret, and
// branch metadata. All activation logic (parse, indexed lookup, secret verification, atomic
// consumption, DeviceId assignment/retention, shared-secret issuance) lives in
// IDeviceService.ActivateAsync (T-19); this controller only translates between HTTP and that result.
// It performs no parsing, hashing, verification, consumption, generation, or persistence itself.
//
// This is the second and last endpoint permanently exempt from Admin JWT authentication (FS-02
// §10.4, §11): the Activation Key is itself the credential, so the endpoint cannot require an Admin
// session. The exemption is explicit via [AllowAnonymous] — exactly the opt-out the application's
// fallback authorization policy (T-10) anticipates — and applies ONLY to this action; every other
// endpoint remains protected by that default-deny policy. The endpoint still passes through the
// standard request-validation and response-envelope pipeline like any other (FS-02 §10.4).
[ApiController]
[Route("api/v1/activate")]
public class ActivateController : ControllerBase
{
    private readonly IDeviceService _deviceService;

    public ActivateController(IDeviceService deviceService)
    {
        _deviceService = deviceService;
    }

    [AllowAnonymous]
    [HttpPost]
    public async Task<IActionResult> Activate(
        [FromBody] ActivateRequestDto request, CancellationToken cancellationToken)
    {
        // A null/blank key is passed straight through: the service treats it as a malformed key and
        // rejects it identically to every other bad key (FS-02 §5.6, AC-15), so an empty value is not
        // special-cased into a different (e.g. 400) outcome here.
        var result = await _deviceService.ActivateAsync(
            request.ActivationKey ?? string.Empty, cancellationToken);

        if (!result.Succeeded)
        {
            // Every rejection reason — malformed, unknown keyId, incorrect secret, consumed,
            // invalidated — collapses to one identical 401 body (FS-02 §5.6, §10.4, §13, AC-15). The
            // typed reason (result.FailureReason) is never placed on the wire.
            return Unauthorized(ActivationFailure.Response());
        }

        // The only response that ever carries the plaintext shared secret, disclosed once (FS-02
        // §5.5 step 8). The DTO exposes only DeviceId, the secret, and BranchId — never the internal
        // DeviceRecordId or the stored ProtectedSharedSecret.
        return Ok(ActivateResponseDto.From(result.Success!));
    }
}

using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using WeaponDetection.Api.Contracts;
using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.Api.Controllers;

// FS-02 §10.5, IP-05 T-52/ADR-017. The device-authenticated credential-validation endpoint a running
// Agent calls to confirm its stored credentials are still valid, so it can lock itself when they have
// been revoked (a key regeneration). It is detect-only: it returns a verdict and nothing else — never
// a key, a secret, device status, configuration, health, or Online/Offline state — and writes nothing.
//
// Like ActivateController this is exempt from Admin JWT authentication (the device credentials in the
// headers are the credential, not an Admin session), so the fallback default-deny policy is opted out
// of explicitly with [AllowAnonymous] on the action only; every other endpoint stays protected.
//
// Credentials travel in the established device-auth headers (ARCH-001 §14.1) — X-Device-Id and
// X-Device-Secret — never a body, query string, or new header. All validation logic lives in
// IDeviceCredentialValidator (T-51); this controller only reads the two headers and maps the verdict
// to HTTP. It logs nothing: the internal outcome, the DeviceId, and the secret never reach a log.
[ApiController]
[Route("api/v1/device/credentials")]
public class DeviceCredentialValidationController : ControllerBase
{
    // The established device-authentication headers (ARCH-001 §14.1). Not new fields.
    private const string DeviceIdHeader = "X-Device-Id";
    private const string DeviceSecretHeader = "X-Device-Secret";

    private readonly IDeviceCredentialValidator _deviceCredentialValidator;

    public DeviceCredentialValidationController(IDeviceCredentialValidator deviceCredentialValidator)
    {
        _deviceCredentialValidator = deviceCredentialValidator;
    }

    [AllowAnonymous]
    [HttpPost("validate")]
    public async Task<IActionResult> Validate(CancellationToken cancellationToken)
    {
        // A missing or malformed DeviceId header parses to Guid.Empty, which the validator treats as a
        // missing DeviceId — so bad input yields the same uniform rejection as any other failure, never
        // an unhandled exception (FS-02 §12/§13). An absent secret header reads as an empty string,
        // which the validator treats as a missing secret.
        var deviceId = Guid.TryParse(Request.Headers[DeviceIdHeader].ToString(), out var parsed)
            ? parsed
            : Guid.Empty;
        var presentedSecret = Request.Headers[DeviceSecretHeader].ToString();

        var result = await _deviceCredentialValidator.ValidateAsync(
            deviceId, presentedSecret, cancellationToken);

        // Only a valid, active credential succeeds. Every other outcome — missing input, unknown
        // device, revoked/ReactivationRequired, missing stored secret, wrong secret — collapses to one
        // uniform 401/INVALID_DEVICE_CREDENTIALS. The typed outcome is never placed on the wire, in a
        // header, or in a log (FS-02 §10.5).
        return result.IsValid
            ? Ok(ApiResponse.Ok(null))
            : Unauthorized(DeviceCredentialFailure.Response());
    }
}

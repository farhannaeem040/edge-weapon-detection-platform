using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using WeaponDetection.Api.Contracts;
using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.Api.Controllers;

// FS-11 §3, IP-13 T-224. The Agent's authoritative source of DeepStream pipeline input — the Camera
// configuration assigned to the authenticated Device's Branch. Device-authenticated exactly like
// DeviceCredentialValidationController (same X-Device-Id/X-Device-Secret headers, same
// IDeviceCredentialValidator, same [AllowAnonymous] opt-out of the default Admin-JWT policy) — this
// is a deliberate reuse of the established convention (ARCH-001 §14.1), not a new auth scheme.
[ApiController]
[Route("api/v1/device/configuration")]
public class DeviceConfigurationController : ControllerBase
{
    private const string DeviceIdHeader = "X-Device-Id";
    private const string DeviceSecretHeader = "X-Device-Secret";

    private readonly IDeviceCredentialValidator _deviceCredentialValidator;
    private readonly IDeviceConfigurationService _deviceConfigurationService;

    public DeviceConfigurationController(
        IDeviceCredentialValidator deviceCredentialValidator,
        IDeviceConfigurationService deviceConfigurationService)
    {
        _deviceCredentialValidator = deviceCredentialValidator;
        _deviceConfigurationService = deviceConfigurationService;
    }

    [AllowAnonymous]
    [HttpGet]
    public async Task<IActionResult> Get(CancellationToken cancellationToken)
    {
        var deviceId = Guid.TryParse(Request.Headers[DeviceIdHeader].ToString(), out var parsed)
            ? parsed
            : Guid.Empty;
        var presentedSecret = Request.Headers[DeviceSecretHeader].ToString();

        var validation = await _deviceCredentialValidator.ValidateAsync(
            deviceId, presentedSecret, cancellationToken);

        if (!validation.IsValid)
        {
            // FS-07: a storage-unavailable outcome is a server-side failure, never collapsed into
            // the same 401 a confirmed credential revocation produces — identical rule to
            // DeviceCredentialValidationController.
            if (validation.Outcome == DeviceCredentialValidationOutcome.CredentialStorageUnavailable)
            {
                return StatusCode(
                    StatusCodes.Status503ServiceUnavailable, DeviceAuthenticationUnavailable.Response());
            }

            return Unauthorized(DeviceCredentialFailure.Response());
        }

        // BranchId comes only from the validator's own result (the same Device row it already
        // loaded for the credential check) — never a second, independent lookup, and never a
        // client-supplied value, so a Device can never fetch another Branch's Cameras (FS-11 §3).
        var configuration = await _deviceConfigurationService.GetConfigurationAsync(
            deviceId, validation.BranchId!.Value, cancellationToken);

        return Ok(ApiResponse.Ok(DeviceConfigurationResponseDto.From(configuration)));
    }
}

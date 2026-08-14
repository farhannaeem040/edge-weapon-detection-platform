using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using WeaponDetection.Api.Contracts;
using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.Api.Controllers;

// FS-08 §9, IP-10 T-154. The device-authenticated endpoint the Jetson Agent's SnapshotUploadWorker
// calls to durably hand off one captured JPEG per accepted DetectionEvent. Mirrors
// SyncEventsController's exact pattern (FS-06 §4, IP-08 T-99): device credentials via
// IDeviceCredentialValidator (including the FS-07 CredentialStorageUnavailable -> 503 branch and the
// uniform 401 for an invalid credential, byte-identical to every other device-authenticated
// endpoint), [AllowAnonymous] applied to the action only, and all real validation/storage/attach
// logic delegated to IAlertSnapshotUploadService — no EF, hashing, or filesystem logic lives here.
//
// The route (`api/v1/alerts/{alertId}/snapshot`) is architecturally frozen (ARCH-001 §14.1) and must
// not be changed.
[ApiController]
[Route("api/v1/alerts")]
public class AlertSnapshotUploadController : ControllerBase
{
    // The established device-authentication headers (ARCH-001 §14.1) — not new fields, read exactly
    // as SyncEventsController/DeviceCredentialValidationController read them.
    private const string DeviceIdHeader = "X-Device-Id";
    private const string DeviceSecretHeader = "X-Device-Secret";

    private readonly IDeviceCredentialValidator _deviceCredentialValidator;
    private readonly IAlertSnapshotUploadService _alertSnapshotUploadService;

    public AlertSnapshotUploadController(
        IDeviceCredentialValidator deviceCredentialValidator,
        IAlertSnapshotUploadService alertSnapshotUploadService)
    {
        _deviceCredentialValidator = deviceCredentialValidator;
        _alertSnapshotUploadService = alertSnapshotUploadService;
    }

    [AllowAnonymous]
    [HttpPost("{alertId:guid}/snapshot")]
    public async Task<IActionResult> UploadSnapshot(
        Guid alertId, [FromForm] AlertSnapshotUploadRequestDto request, CancellationToken cancellationToken)
    {
        // Mirrors SyncEventsController exactly: a missing/malformed DeviceId header parses to
        // Guid.Empty, and an absent secret header reads as an empty string — both handled uniformly
        // by the validator, never as an unhandled exception.
        var deviceId = Guid.TryParse(Request.Headers[DeviceIdHeader].ToString(), out var parsedDeviceId)
            ? parsedDeviceId
            : Guid.Empty;
        var presentedSecret = Request.Headers[DeviceSecretHeader].ToString();

        var validation = await _deviceCredentialValidator.ValidateAsync(
            deviceId, presentedSecret, cancellationToken);

        if (!validation.IsValid)
        {
            // FS-07: a storage-unavailable outcome is a server-side failure, not a confirmed-invalid
            // credential — never collapsed into the same 401 a real revocation produces (identical
            // rationale/branch to SyncEventsController).
            if (validation.Outcome == DeviceCredentialValidationOutcome.CredentialStorageUnavailable)
            {
                return StatusCode(
                    StatusCodes.Status503ServiceUnavailable, DeviceAuthenticationUnavailable.Response());
            }

            return Unauthorized(DeviceCredentialFailure.Response());
        }

        if (!Guid.TryParse(request.EventId, out var eventId))
        {
            return BadRequest(ApiResponse.Fail(
                "VALIDATION_ERROR", "A valid eventId is required."));
        }

        var file = request.File;

        // validation.BranchId is non-null here: populated only on a Valid result (FS-06 §6.2), and
        // IsValid was just confirmed true above.
        var outcome = await _alertSnapshotUploadService.UploadAsync(
            new SnapshotUploadRequest(
                alertId,
                validation.BranchId!.Value,
                eventId,
                file?.ContentType,
                request.Sha256,
                file?.Length ?? 0,
                file?.OpenReadStream()),
            cancellationToken);

        return outcome.Kind switch
        {
            SnapshotUploadOutcomeKind.Accepted => StatusCode(
                StatusCodes.Status201Created,
                ApiResponse.Ok(new AlertSnapshotUploadResponseDto(
                    alertId, SnapshotUploadOutcomeNames.Accepted, outcome.SnapshotReference!))),

            SnapshotUploadOutcomeKind.Duplicate => Ok(
                ApiResponse.Ok(new AlertSnapshotUploadResponseDto(
                    alertId, SnapshotUploadOutcomeNames.Duplicate, outcome.SnapshotReference!))),

            SnapshotUploadOutcomeKind.AlertNotOwnedOrFound => NotFound(
                ApiResponse.Fail(outcome.ErrorCode!, outcome.ErrorMessage!)),

            SnapshotUploadOutcomeKind.EventIdMismatch => BadRequest(
                ApiResponse.Fail(outcome.ErrorCode!, outcome.ErrorMessage!)),

            SnapshotUploadOutcomeKind.MissingFile => BadRequest(
                ApiResponse.Fail(outcome.ErrorCode!, outcome.ErrorMessage!)),

            SnapshotUploadOutcomeKind.MalformedImage => BadRequest(
                ApiResponse.Fail(outcome.ErrorCode!, outcome.ErrorMessage!)),

            SnapshotUploadOutcomeKind.Oversized => StatusCode(
                StatusCodes.Status413PayloadTooLarge, ApiResponse.Fail(outcome.ErrorCode!, outcome.ErrorMessage!)),

            SnapshotUploadOutcomeKind.UnsupportedMediaType => StatusCode(
                StatusCodes.Status415UnsupportedMediaType,
                ApiResponse.Fail(outcome.ErrorCode!, outcome.ErrorMessage!)),

            SnapshotUploadOutcomeKind.Conflict => Conflict(
                ApiResponse.Fail(outcome.ErrorCode!, outcome.ErrorMessage!)),

            _ => StatusCode(
                StatusCodes.Status500InternalServerError,
                ApiResponse.Fail("UNEXPECTED_ERROR", "An unexpected error occurred.")),
        };
    }
}

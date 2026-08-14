using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using WeaponDetection.Api.Contracts;
using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.Api.Controllers;

// FS-06 §4, IP-08 T-99. The device-authenticated endpoint the Agent's DetectionEventSyncWorker calls
// to durably hand off its SQLite outbox to the Backend. Like DeviceCredentialValidationController it
// is exempt from Admin JWT authentication — the device credentials in the headers are the credential
// — so [AllowAnonymous] is applied to the action only; every other endpoint stays protected.
//
// Credentials travel in the established device-auth headers (ARCH-001 §14.1) — X-Device-Id and
// X-Device-Secret — read exactly as DeviceCredentialValidationController reads them (a missing or
// malformed DeviceId parses to Guid.Empty, which the validator treats uniformly as missing). A 401
// here is byte-identical to the credential-validation endpoint's own 401: this endpoint introduces no
// new error code, and per FS-06 §6.1 a 401 from this endpoint never locks or reactivates the Agent —
// only the dedicated validate endpoint is authoritative for confirmed revocation.
//
// The controller is deliberately thin (FS-06 §2): it authenticates, binds/bounds the batch, delegates
// to IAlertSyncService, and maps typed outcomes back onto the wire. No EF or transaction logic lives
// here.
[ApiController]
[Route("api/v1/sync/events")]
public class SyncEventsController : ControllerBase
{
    // The established device-authentication headers (ARCH-001 §14.1). Not new fields.
    private const string DeviceIdHeader = "X-Device-Id";
    private const string DeviceSecretHeader = "X-Device-Secret";

    // Matches the Agent's own WDA_DETECTION_SYNC_BATCH_SIZE upper bound (FS-06 §10:
    // Field(default=25, ge=1, le=100)) — a batch larger than the Agent could ever legitimately send
    // is rejected outright rather than silently accepted.
    public const int MaxBatchSize = 100;

    private readonly IDeviceCredentialValidator _deviceCredentialValidator;
    private readonly IAlertSyncService _alertSyncService;

    public SyncEventsController(
        IDeviceCredentialValidator deviceCredentialValidator, IAlertSyncService alertSyncService)
    {
        _deviceCredentialValidator = deviceCredentialValidator;
        _alertSyncService = alertSyncService;
    }

    [AllowAnonymous]
    [HttpPost]
    public async Task<IActionResult> SyncEvents(
        [FromBody] SyncEventsRequest request, CancellationToken cancellationToken)
    {
        // Mirrors DeviceCredentialValidationController exactly: a missing/malformed DeviceId header
        // parses to Guid.Empty, and an absent secret header reads as an empty string — both handled
        // uniformly by the validator, never as an unhandled exception.
        var deviceId = Guid.TryParse(Request.Headers[DeviceIdHeader].ToString(), out var parsed)
            ? parsed
            : Guid.Empty;
        var presentedSecret = Request.Headers[DeviceSecretHeader].ToString();

        var validation = await _deviceCredentialValidator.ValidateAsync(
            deviceId, presentedSecret, cancellationToken);

        if (!validation.IsValid)
        {
            // FS-07: a storage-unavailable outcome is a server-side failure, not a confirmed-invalid
            // credential — it must never be collapsed into the same 401 a real revocation produces
            // (the sync worker's retry/backoff treats a 401 and a 5xx/503 identically either way,
            // FS-06 §6.1, but the distinct status keeps this endpoint's own behavior consistent with
            // DeviceCredentialValidationController's).
            if (validation.Outcome == DeviceCredentialValidationOutcome.CredentialStorageUnavailable)
            {
                return StatusCode(
                    StatusCodes.Status503ServiceUnavailable, DeviceAuthenticationUnavailable.Response());
            }

            return Unauthorized(DeviceCredentialFailure.Response());
        }

        var events = request.Events;
        if (events is null || events.Count == 0)
        {
            return BadRequest(ApiResponse.Fail(
                "VALIDATION_ERROR", "At least one event is required."));
        }

        if (events.Count > MaxBatchSize)
        {
            return StatusCode(
                StatusCodes.Status413PayloadTooLarge,
                ApiResponse.Fail(
                    "BATCH_TOO_LARGE",
                    $"A sync request must not carry more than {MaxBatchSize} events."));
        }

        var items = events.Select(ToApplicationItem).ToList();

        // validation.BranchId/DeviceRecordId are non-null here: they are populated only on a Valid
        // result (FS-06 §6.2), and IsValid was just confirmed true above.
        var outcomes = await _alertSyncService.SyncEventsAsync(
            deviceId, validation.BranchId!.Value, items, cancellationToken);

        var results = outcomes.Select(ToResultDto).ToList();

        return Ok(new SyncEventsResponse(results));
    }

    private static DetectionEventSyncItem ToApplicationItem(DetectionEventDto dto) =>
        new(
            dto.EventId,
            dto.CameraId,
            dto.DetectedAtUtc,
            dto.CreatedAtUtc,
            dto.ClassId,
            dto.ClassName,
            dto.Confidence,
            dto.SourceId,
            dto.FrameNumber,
            dto.FrameWidth,
            dto.FrameHeight,
            new BoundingBoxValue(
                dto.BoundingBox.Left, dto.BoundingBox.Top, dto.BoundingBox.Width, dto.BoundingBox.Height));

    private static SyncEventResultDto ToResultDto(SyncEventOutcome outcome) =>
        new(
            outcome.EventId,
            outcome.Kind switch
            {
                SyncEventOutcomeKind.Accepted => SyncEventOutcomeNames.Accepted,
                SyncEventOutcomeKind.Duplicate => SyncEventOutcomeNames.Duplicate,
                SyncEventOutcomeKind.QuotaExceeded => SyncEventOutcomeNames.QuotaExceeded,
                _ => SyncEventOutcomeNames.Rejected,
            },
            outcome.AlertId,
            outcome.ErrorCode,
            outcome.Quota is null ? null : new SyncEventQuotaDto(outcome.Quota.Maximum, outcome.Quota.LocalDate));
}

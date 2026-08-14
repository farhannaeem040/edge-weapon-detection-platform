using Microsoft.AspNetCore.Mvc;
using WeaponDetection.Api.Contracts;
using WeaponDetection.Application.Interfaces;
using WeaponDetection.Domain;

namespace WeaponDetection.Api.Controllers;

// FS-10 §9.2/§9.3, IP-12 T-195. Read-only Alert list/detail endpoints for the Admin Dashboard. Every
// orchestration (pagination, filtering, sorting, the Alert→Camera→Branch projection) stays in
// IAlertQueryService; this controller only translates HTTP query parameters and results.
//
// The controller carries no [AllowAnonymous], so the application's default/fallback policy subjects
// every action here to Admin JWT validation and the active-session check before the action body runs
// — the same convention BranchController documents. Device credentials (X-Device-Id/X-Device-Secret)
// never satisfy this policy; a device-credentialed request is rejected 401 before reaching this
// controller. This coexists with AlertSnapshotUploadController's POST on the same "api/v1/alerts"
// route prefix without collision — different templates/verbs, a different controller class.
[ApiController]
[Route("api/v1/alerts")]
public class AlertController : ControllerBase
{
    // The two whitelisted sortBy values (FS-10 §9.2/§11) — never a raw column name, never
    // interpolated into SQL.
    private const string SortByDetectedAtUtc = "detectedAtUtc";
    private const string SortByReceivedAtUtc = "receivedAtUtc";

    // The two weapon classes this platform's model currently detects (mirrors AlertSyncService's own
    // gun/knife suppression-counter convention, FS-09 §7) — the only className filter values accepted.
    private static readonly HashSet<string> KnownClassNames =
        new(StringComparer.OrdinalIgnoreCase) { "gun", "knife" };

    private readonly IAlertQueryService _alertQueryService;
    private readonly IAlertSnapshotRetrievalService _snapshotRetrievalService;

    public AlertController(
        IAlertQueryService alertQueryService, IAlertSnapshotRetrievalService snapshotRetrievalService)
    {
        _alertQueryService = alertQueryService;
        _snapshotRetrievalService = snapshotRetrievalService;
    }

    [HttpGet]
    public async Task<IActionResult> List(
        [FromQuery] AlertListRequestDto request, CancellationToken cancellationToken)
    {
        var page = request.Page ?? 1;
        if (page < 1)
        {
            return BadRequest(ApiResponse.Fail("VALIDATION_ERROR", "page must be a positive integer."));
        }

        var pageSize = request.PageSize ?? AlertListQuery.DefaultPageSize;
        if (pageSize < 1 || pageSize > AlertListQuery.MaxPageSize)
        {
            return BadRequest(ApiResponse.Fail(
                "VALIDATION_ERROR",
                $"pageSize must be between 1 and {AlertListQuery.MaxPageSize}."));
        }

        AlertSortField sortField;
        if (string.IsNullOrEmpty(request.SortBy)
            || string.Equals(request.SortBy, SortByDetectedAtUtc, StringComparison.OrdinalIgnoreCase))
        {
            sortField = AlertSortField.DetectedAtUtc;
        }
        else if (string.Equals(request.SortBy, SortByReceivedAtUtc, StringComparison.OrdinalIgnoreCase))
        {
            sortField = AlertSortField.ReceivedAtUtc;
        }
        else
        {
            return BadRequest(ApiResponse.Fail(
                "VALIDATION_ERROR",
                $"sortBy must be one of: {SortByDetectedAtUtc}, {SortByReceivedAtUtc}."));
        }

        if (request.ClassName is not null && !KnownClassNames.Contains(request.ClassName))
        {
            return BadRequest(ApiResponse.Fail("VALIDATION_ERROR", "className is not recognized."));
        }

        if (request.Status is not null
            && !Enum.TryParse<AlertStatus>(request.Status, ignoreCase: true, out _))
        {
            return BadRequest(ApiResponse.Fail("VALIDATION_ERROR", "status is not recognized."));
        }

        var query = new AlertListQuery(
            page,
            pageSize,
            request.FromUtc,
            request.ToUtc,
            request.ClassName,
            request.BranchId,
            request.CameraId,
            request.Status,
            request.SnapshotAvailable,
            sortField,
            request.SortDescending ?? true);

        var result = await _alertQueryService.ListAlertsAsync(query, cancellationToken);

        return Ok(AlertListResponseDto.From(result));
    }

    [HttpGet("{id:guid}")]
    public async Task<IActionResult> GetById(Guid id, CancellationToken cancellationToken)
    {
        var alert = await _alertQueryService.GetAlertAsync(id, cancellationToken);
        if (alert is null)
        {
            return NotFound(ApiResponse.Fail("NOT_FOUND", "Alert not found."));
        }

        return Ok(AlertDetailDto.From(alert));
    }

    // FS-08 §12, IP-10 T-160: no [AllowAnonymous] here either, so this inherits the exact same
    // default/fallback ActiveAdminSessionRequirement policy as GetById/List above — an
    // unauthenticated or device-credentialed request is rejected before this action body runs.
    // Never returns SnapshotReference or any filesystem path — only the JPEG bytes and their
    // content type.
    [HttpGet("{id:guid}/snapshot")]
    public async Task<IActionResult> GetSnapshot(Guid id, CancellationToken cancellationToken)
    {
        var outcome = await _snapshotRetrievalService.GetSnapshotAsync(id, cancellationToken);
        if (outcome.Kind == SnapshotRetrievalOutcomeKind.NotFound)
        {
            return NotFound(ApiResponse.Fail("NOT_FOUND", "No snapshot is available for this Alert."));
        }

        Response.Headers.CacheControl = "no-store, private";
        return File(outcome.Content!, outcome.ContentType!);
    }
}

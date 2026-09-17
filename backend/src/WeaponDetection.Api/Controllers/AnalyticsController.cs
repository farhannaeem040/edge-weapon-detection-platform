using System.Text;
using Microsoft.AspNetCore.Mvc;
using WeaponDetection.Api.Contracts;
using WeaponDetection.Application.Analytics;
using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.Api.Controllers;

// FS-15 §6, IP-17 T-5. The Admin-only Operational Analytics endpoints. Every aggregation stays in
// IOperationalAnalyticsService; this controller only validates query parameters, resolves the range
// preset into an absolute UTC window, and translates outcomes onto HTTP.
//
// The controller carries no [AllowAnonymous], so the application's default/fallback policy subjects
// both actions to Admin JWT validation and the active-session check before the action body runs — the
// same convention AlertController, DashboardController and BranchController document. Device
// credentials (X-Device-Id/X-Device-Secret) never satisfy this policy; a device-credentialed request
// is rejected 401 before reaching this controller. Analytics is never a public endpoint.
[ApiController]
[Route("api/v1/analytics")]
public class AnalyticsController : ControllerBase
{
    // The platform's own detection taxonomy (FS-15 §3.5) — the same whitelist AlertController applies,
    // mirroring AlertSyncService's gun/knife suppression counters (FS-09 §7). No class outside it is
    // offered or accepted; unrelated classes from the UI mockup are deliberately absent.
    private static readonly HashSet<string> KnownDetectionTypes =
        new(StringComparer.OrdinalIgnoreCase) { "gun", "knife" };

    private readonly IOperationalAnalyticsService _analyticsService;
    private readonly TimeProvider _timeProvider;

    public AnalyticsController(IOperationalAnalyticsService analyticsService, TimeProvider timeProvider)
    {
        _analyticsService = analyticsService;
        _timeProvider = timeProvider;
    }

    [HttpGet("operational")]
    public async Task<IActionResult> Operational(
        [FromQuery] string? range,
        [FromQuery] DateTime? fromUtc,
        [FromQuery] DateTime? toUtc,
        [FromQuery] Guid? branchId,
        [FromQuery] string? detectionType,
        CancellationToken cancellationToken)
    {
        if (!TryBuildQuery(range, fromUtc, toUtc, branchId, detectionType, out var query, out var resolvedRange, out var failure))
        {
            return BadRequest(failure);
        }

        var view = await _analyticsService.GetOperationalAsync(query, cancellationToken);
        if (view is null)
        {
            return NotFound(ApiResponse.Fail("NOT_FOUND", "Branch not found."));
        }

        return Ok(OperationalAnalyticsDto.From(view, resolvedRange));
    }

    // FS-15 §6.2. Returns raw CSV bytes rather than an envelope: FileContentResult is not an
    // ObjectResult, so ApiEnvelopeResultFilter leaves it untouched — the same mechanism that already
    // lets AlertController return snapshot JPEG bytes. Failures on this route still answer with the
    // normal envelope, so an error is never mistaken for a one-line CSV file.
    [HttpGet("operational/export")]
    public async Task<IActionResult> ExportOperational(
        [FromQuery] string? range,
        [FromQuery] DateTime? fromUtc,
        [FromQuery] DateTime? toUtc,
        [FromQuery] Guid? branchId,
        [FromQuery] string? detectionType,
        CancellationToken cancellationToken)
    {
        if (!TryBuildQuery(range, fromUtc, toUtc, branchId, detectionType, out var query, out _, out var failure))
        {
            return BadRequest(failure);
        }

        var rows = await _analyticsService.GetExportRowsAsync(query, cancellationToken);
        if (rows is null)
        {
            return NotFound(ApiResponse.Fail("NOT_FOUND", "Branch not found."));
        }

        // Over the bound rather than at it (the service deliberately fetches MaxExportRows + 1), so a
        // too-large export is refused with an actionable message instead of quietly handing back a
        // file that is missing rows (FS-15 §6.2).
        if (rows.Count > AnalyticsQuery.MaxExportRows)
        {
            return BadRequest(ApiResponse.Fail(
                "VALIDATION_ERROR",
                $"The selected filters match more than {AnalyticsQuery.MaxExportRows} Alerts. " +
                "Narrow the date range, Branch, or detection type and try again."));
        }

        var generatedAtUtc = _timeProvider.GetUtcNow().UtcDateTime;
        var csv = AnalyticsCsvWriter.Write(rows);

        // A UTF-8 BOM so Excel opens non-ASCII Branch/Camera names correctly rather than as mojibake;
        // every RFC 4180 reader tolerates it.
        var bytes = Encoding.UTF8.GetPreamble().Concat(Encoding.UTF8.GetBytes(csv)).ToArray();

        return File(bytes, "text/csv; charset=utf-8", AnalyticsCsvWriter.FileName(generatedAtUtc));
    }

    // The single validation/resolution path both actions share, so the export can never accept a
    // filter the analytics view would have rejected (or vice versa) — which would let an Admin export
    // a different data set than the one on screen.
    private bool TryBuildQuery(
        string? range,
        DateTime? fromUtc,
        DateTime? toUtc,
        Guid? branchId,
        string? detectionType,
        out AnalyticsQuery query,
        out AnalyticsRange? resolvedRange,
        out ApiResponse failure)
    {
        query = null!;
        resolvedRange = null;
        failure = null!;

        string? normalizedDetectionType = null;
        if (!string.IsNullOrWhiteSpace(detectionType))
        {
            if (!KnownDetectionTypes.Contains(detectionType))
            {
                failure = ApiResponse.Fail("VALIDATION_ERROR", "detectionType is not recognized.");
                return false;
            }

            normalizedDetectionType = detectionType.Trim().ToLowerInvariant();
        }

        if (branchId == Guid.Empty)
        {
            failure = ApiResponse.Fail("VALIDATION_ERROR", "branchId must be a non-empty GUID.");
            return false;
        }

        DateTime windowFrom;
        DateTime windowTo;
        AnalyticsBucket bucket;

        // An absolute window and a preset are mutually exclusive: honouring both would leave the
        // response's echoed filters ambiguous about which one actually applied.
        if (fromUtc.HasValue || toUtc.HasValue)
        {
            if (range is not null)
            {
                failure = ApiResponse.Fail(
                    "VALIDATION_ERROR", "Supply either range or fromUtc/toUtc, not both.");
                return false;
            }

            if (!fromUtc.HasValue || !toUtc.HasValue)
            {
                failure = ApiResponse.Fail(
                    "VALIDATION_ERROR", "fromUtc and toUtc must be supplied together.");
                return false;
            }

            windowFrom = AsUtc(fromUtc.Value);
            windowTo = AsUtc(toUtc.Value);

            if (windowTo <= windowFrom)
            {
                failure = ApiResponse.Fail("VALIDATION_ERROR", "toUtc must be later than fromUtc.");
                return false;
            }

            // A hard ceiling, so a single analytics call can never be turned into an unbounded scan of
            // years of Alerts (FS-15 §4.1).
            if (windowTo - windowFrom > TimeSpan.FromDays(AnalyticsQuery.MaxRangeDays))
            {
                failure = ApiResponse.Fail(
                    "VALIDATION_ERROR",
                    $"The requested range must not exceed {AnalyticsQuery.MaxRangeDays} days.");
                return false;
            }

            bucket = AnalyticsWindow.BucketFor(windowFrom, windowTo);
        }
        else
        {
            // An absent range is the documented default; an *unrecognized* one is a caller error, not
            // a reason to quietly show a different period than the URL names (FS-15 §4.1).
            if (range is not null && !AnalyticsWindow.TryParseRange(range, out _))
            {
                failure = ApiResponse.Fail(
                    "VALIDATION_ERROR",
                    $"range must be one of: {string.Join(", ", AnalyticsWindow.RangeWireValues)}.");
                return false;
            }

            var preset = AnalyticsWindow.DefaultRange;
            if (range is not null)
            {
                AnalyticsWindow.TryParseRange(range, out preset);
            }

            resolvedRange = preset;
            (windowFrom, windowTo, bucket) =
                AnalyticsWindow.Resolve(preset, _timeProvider.GetUtcNow().UtcDateTime);
        }

        query = new AnalyticsQuery(windowFrom, windowTo, bucket, branchId, normalizedDetectionType);
        return true;
    }

    // The parameters are named fromUtc/toUtc, so an offset-less value is taken at its word as UTC
    // rather than being reinterpreted through the server's local timezone — which would silently shift
    // the window by the container's offset and make the same URL mean different periods on different
    // hosts. An explicitly offset value is converted normally.
    private static DateTime AsUtc(DateTime value) => value.Kind switch
    {
        DateTimeKind.Utc => value,
        DateTimeKind.Local => value.ToUniversalTime(),
        _ => DateTime.SpecifyKind(value, DateTimeKind.Utc),
    };
}

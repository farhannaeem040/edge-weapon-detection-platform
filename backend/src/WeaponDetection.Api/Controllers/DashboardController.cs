using Microsoft.AspNetCore.Mvc;
using WeaponDetection.Api.Contracts;
using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.Api.Controllers;

// FS-10 §9.1, IP-12 T-196. The bounded Admin Dashboard summary endpoint. No [AllowAnonymous] — the
// default/fallback Admin JWT + active-session policy applies, exactly as every other protected
// controller (BranchController, AlertController).
[ApiController]
[Route("api/v1/dashboard")]
public class DashboardController : ControllerBase
{
    private readonly IDashboardSummaryService _dashboardSummaryService;

    public DashboardController(IDashboardSummaryService dashboardSummaryService)
    {
        _dashboardSummaryService = dashboardSummaryService;
    }

    // Manual-review Correction 3: branchId is a required query parameter — the summary is always for
    // one explicitly identified Branch, never an implicit "first Branch" pick. A request that omits
    // it is a 400 (a caller error, not "no data yet"); a branchId that does not resolve to any Branch
    // (never existed, or deleted since) is a 404, the same explicit "unavailable" outcome the Angular
    // dashboard already renders distinctly from a confirmed zero-state (FS-10 Phase 12) — never a
    // fabricated summary, and never another Branch's data.
    [HttpGet("summary")]
    public async Task<IActionResult> Summary(
        [FromQuery] Guid? branchId, CancellationToken cancellationToken)
    {
        if (branchId is null || branchId == Guid.Empty)
        {
            return BadRequest(ApiResponse.Fail("VALIDATION_ERROR", "branchId is required."));
        }

        var summary = await _dashboardSummaryService.GetSummaryAsync(branchId.Value, cancellationToken);
        if (summary is null)
        {
            return NotFound(ApiResponse.Fail("NOT_FOUND", "Branch not found."));
        }

        return Ok(DashboardSummaryDto.From(summary));
    }
}

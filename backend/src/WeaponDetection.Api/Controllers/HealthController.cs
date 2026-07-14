using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace WeaponDetection.Api.Controllers;

// Temporary scaffolding-verification endpoint for IP-01 T-01.
// Superseded by real health/status reporting in a later feature specification.
//
// [AllowAnonymous] keeps this liveness probe reachable without a session now that T-10's
// fallback policy protects every endpoint by default. It is not a Dashboard-initiated data
// operation in the sense of FS-01 §6 — it exposes no branch, device, alert, or account data,
// only a fixed "Healthy" literal — so exempting it weakens no FS-01 requirement. The feature
// that replaces it will decide its own authentication posture.
[AllowAnonymous]
[ApiController]
[Route("api/v1/[controller]")]
public class HealthController : ControllerBase
{
    [HttpGet]
    public IActionResult Get() => Ok(new { status = "Healthy" });
}

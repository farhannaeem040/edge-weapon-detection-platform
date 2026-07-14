using Microsoft.AspNetCore.Mvc;

namespace WeaponDetection.Api.Controllers;

// Temporary scaffolding-verification endpoint for IP-01 T-01.
// Superseded by real health/status reporting in a later feature specification.
[ApiController]
[Route("api/v1/[controller]")]
public class HealthController : ControllerBase
{
    [HttpGet]
    public IActionResult Get() => Ok(new { status = "Healthy" });
}

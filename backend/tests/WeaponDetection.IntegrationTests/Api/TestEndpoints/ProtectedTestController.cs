using System.Threading;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace WeaponDetection.IntegrationTests.Api.TestEndpoints;

// The placeholder protected endpoints required as T-10's completion evidence. They live in the
// test assembly, not in WeaponDetection.Api, so verifying the authentication middleware does not
// ship a scaffolding route in the real API surface; ProtectedEndpointApiFactory registers this
// assembly as an MVC application part on the in-process host.
//
// FS-01 §5.3 step 5 requires rejection *before* the request reaches business logic. Invocations
// counts the number of times an action body actually executed, so a test can assert that a
// rejected request never got here.
[ApiController]
[Route("api/v1/test")]
public class ProtectedTestController : ControllerBase
{
    private static int _invocations;

    public static int Invocations => Volatile.Read(ref _invocations);

    // Protected by an explicit attribute — the form T-10's plan names ("any endpoint marked
    // [Authorize]").
    [Authorize]
    [HttpGet("protected")]
    public IActionResult Protected()
    {
        Interlocked.Increment(ref _invocations);
        return Ok(new { reached = true });
    }

    // Deliberately carries no authorization attribute at all: it exists to prove the application's
    // fallback policy protects an endpoint whose author forgot to mark it (FS-01 §6 — uniform
    // application, explicit exceptions only).
    [HttpGet("unannotated")]
    public IActionResult Unannotated()
    {
        Interlocked.Increment(ref _invocations);
        return Ok(new { reached = true });
    }
}

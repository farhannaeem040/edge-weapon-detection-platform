using Microsoft.AspNetCore.Mvc;
using WeaponDetection.Api.Contracts;
using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.Api.Controllers;

// FS-01 §9.1, IP-01 §10/T-09. This route is exempt from JWT authentication (no middleware
// exists yet, so every route is currently anonymously accessible — bearer enforcement is a
// later task). All orchestration (credential verification, token issuance, session creation)
// stays in IAuthService; this controller only translates its result to HTTP.
[ApiController]
[Route("api/v1/auth")]
public class AuthController : ControllerBase
{
    private readonly IAuthService _authService;

    public AuthController(IAuthService authService)
    {
        _authService = authService;
    }

    [HttpPost("login")]
    public async Task<IActionResult> Login([FromBody] LoginRequestDto request, CancellationToken cancellationToken)
    {
        var result = await _authService.LoginAsync(request.CredentialIdentifier, request.Password, cancellationToken);

        if (!result.Succeeded)
        {
            // Deliberately identical for an unknown credential identifier and an incorrect
            // password (FS-01 §5.2/§11) — the caller cannot distinguish the two cases.
            return Unauthorized(ApiResponse.Fail(
                "INVALID_CREDENTIALS",
                "The credential identifier or password is invalid."));
        }

        return Ok(new LoginResponseDto(result.AccessToken!));
    }
}

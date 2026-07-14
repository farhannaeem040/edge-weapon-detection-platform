using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using WeaponDetection.Api.Contracts;
using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.Api.Controllers;

// FS-01 §9.1, IP-01 §10/T-09. All orchestration (credential verification, token issuance,
// session creation) stays in IAuthService; this controller only translates its result to HTTP.
[ApiController]
[Route("api/v1/auth")]
public class AuthController : ControllerBase
{
    private readonly IAuthService _authService;

    public AuthController(IAuthService authService)
    {
        _authService = authService;
    }

    // The one endpoint permanently exempt from Admin JWT authentication (FS-01 §9.1, AC-4): it
    // is what issues a session, so it cannot require one. The exemption is explicit because the
    // application's fallback policy (T-10) otherwise protects every endpoint by default. It still
    // passes through the standard validation and response-envelope pipeline.
    [AllowAnonymous]
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

using System.IdentityModel.Tokens.Jwt;
using System.Security.Claims;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using WeaponDetection.Api.Contracts;
using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.Api.Controllers;

// FS-01 §9.1/§9.2, IP-01 §10/T-09/T-11. All orchestration (credential verification, token
// issuance, session creation, session revocation) stays in IAuthService; this controller only
// translates its result to HTTP.
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

    // FS-01 §9.2: a protected endpoint like any other — it carries no [AllowAnonymous], so the
    // application's fallback policy (T-10) subjects it to both checks before this action body runs.
    // That is what makes the second-logout case work without any special handling here: a token
    // whose session is already revoked never reaches this method, and is rejected with the same
    // 401 as any other invalid token (FS-01 §5.4 step 6, §12, §14 T-12).
    //
    // The endpoint takes no request body — the session to revoke is named by the presented token's
    // own `jti`, never by client-supplied input. A client therefore cannot ask the Backend to
    // revoke a session other than the one it is authenticated as.
    [HttpPost("logout")]
    public async Task<IActionResult> Logout(CancellationToken cancellationToken)
    {
        // The authentication middleware has already validated the token and resolved these two
        // claims to an active session; parsing them again cannot fail in practice. The guard is
        // here so that a future change to the pipeline can never turn a missing claim into an
        // unhandled exception (500) or, worse, a silently successful logout of nothing.
        if (!Guid.TryParse(User.FindFirstValue(JwtRegisteredClaimNames.Jti), out var sessionId) ||
            !Guid.TryParse(User.FindFirstValue(JwtRegisteredClaimNames.Sub), out var userId))
        {
            return Unauthorized(AuthenticationFailure.Response());
        }

        var result = await _authService.LogoutAsync(sessionId, userId, cancellationToken);

        if (result != LogoutResult.Revoked)
        {
            // Reachable only if the session was revoked between authorization and this call. The
            // shared envelope keeps it byte-identical to the middleware's own 401, so this
            // narrow race is indistinguishable from every other rejection (FS-01 §11).
            return Unauthorized(AuthenticationFailure.Response());
        }

        // Success carries no data (FS-01 §9.2) — the envelope's `data` is null and is omitted from
        // the response by the host's JSON options, leaving {success, message}.
        return Ok(ApiResponse.Ok(data: null, message: "Logged out."));
    }
}

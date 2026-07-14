using System.IdentityModel.Tokens.Jwt;
using System.Security.Claims;
using Microsoft.AspNetCore.Authorization;
using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.Api.Security;

// IP-01 §12 step 4 / T-10: the session half of the two-check authentication rule. JWT Bearer
// middleware has already validated the token's signature, issuer, audience, and expiry by the
// time this runs; this handler additionally resolves the token's `jti` against AdminSession and
// refuses the request unless a matching, non-revoked, unexpired session exists for the very user
// the token was issued to (FS-01 §5.3, §11; ADR-013).
//
// Every failure path here simply returns without calling Succeed(), so the requirement goes
// unmet and the request is rejected uniformly — the handler never records *which* check failed,
// and nothing it does can leak that distinction to the caller (FS-01 §11). It also never logs
// the token, the `jti`, or the session id (FS-01 §10).
public sealed class SessionAuthorizationHandler : AuthorizationHandler<ActiveAdminSessionRequirement>
{
    private readonly IAdminSessionValidator _sessionValidator;
    private readonly IHttpContextAccessor _httpContextAccessor;

    public SessionAuthorizationHandler(
        IAdminSessionValidator sessionValidator,
        IHttpContextAccessor httpContextAccessor)
    {
        _sessionValidator = sessionValidator;
        _httpContextAccessor = httpContextAccessor;
    }

    protected override async Task HandleRequirementAsync(
        AuthorizationHandlerContext context,
        ActiveAdminSessionRequirement requirement)
    {
        if (context.User.Identity?.IsAuthenticated != true)
        {
            return;
        }

        // JwtBearerOptions.MapInboundClaims is disabled (AdminAuthenticationExtensions), so the
        // claims arrive under their raw JWT names — exactly as JwtIssuer emitted them — rather
        // than being rewritten to the legacy ClaimTypes.* URIs.
        var sessionIdClaim = context.User.FindFirstValue(JwtRegisteredClaimNames.Jti);
        var userIdClaim = context.User.FindFirstValue(JwtRegisteredClaimNames.Sub);

        // A token with no `jti` is rejected here, before any session lookup is attempted
        // (FS-01 §11, §12, test scenario T-14) — an absent or unparseable claim never reaches
        // the database.
        if (!Guid.TryParse(sessionIdClaim, out var sessionId) || !Guid.TryParse(userIdClaim, out var userId))
        {
            return;
        }

        var cancellationToken = _httpContextAccessor.HttpContext?.RequestAborted ?? CancellationToken.None;

        if (await _sessionValidator.IsSessionActiveAsync(sessionId, userId, cancellationToken))
        {
            context.Succeed(requirement);
        }
    }
}

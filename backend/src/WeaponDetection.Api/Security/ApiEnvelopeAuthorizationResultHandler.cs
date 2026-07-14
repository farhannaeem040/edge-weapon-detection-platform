using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Authorization.Policy;
using WeaponDetection.Api.Contracts;

namespace WeaponDetection.Api.Security;

// FS-01 §9.3 / §11: every authentication failure on a protected endpoint — no Authorization
// header, a malformed or wrongly-signed token, an expired token, a token with no `jti`, a `jti`
// with no session, a session belonging to a different user, or a revoked session — must produce
// 401 with the same standard error envelope, revealing nothing about which check failed.
//
// Two ASP.NET Core defaults are corrected here, in one place, rather than per endpoint:
//
//  1. The default 401 challenge writes an empty body; ADR-009 requires the uniform envelope on
//     every /api/v1 response, with no exception for authentication endpoints (FS-01 §9.3).
//  2. A *failed authorization requirement* on an authenticated principal defaults to 403
//     Forbidden. For this prototype that can only ever mean "the AdminSession check failed",
//     which FS-01 §12 explicitly requires to be a 401 — there are no roles or permission levels
//     that a 403 could legitimately represent (BR-001).
public sealed class ApiEnvelopeAuthorizationResultHandler : IAuthorizationMiddlewareResultHandler
{
    private readonly AuthorizationMiddlewareResultHandler _defaultHandler = new();

    public async Task HandleAsync(
        RequestDelegate next,
        HttpContext context,
        AuthorizationPolicy policy,
        PolicyAuthorizationResult authorizeResult)
    {
        if (authorizeResult.Challenged || authorizeResult.Forbidden)
        {
            context.Response.StatusCode = StatusCodes.Status401Unauthorized;
            await context.Response.WriteAsJsonAsync(AuthenticationFailure.Response());

            return;
        }

        await _defaultHandler.HandleAsync(next, context, policy, authorizeResult);
    }
}

using Microsoft.AspNetCore.Authorization;

namespace WeaponDetection.Api.Security;

// The authorization requirement that carries FS-01's second check (an active, non-revoked
// AdminSession) into the ASP.NET Core authorization pipeline. Satisfied by
// SessionAuthorizationHandler.
public sealed class ActiveAdminSessionRequirement : IAuthorizationRequirement
{
    // The single Admin policy for this prototype (BR-001 — one account, no roles). Also
    // installed as the default and fallback policy in AdminAuthenticationExtensions, so a new
    // endpoint is protected unless it explicitly opts out with [AllowAnonymous].
    public const string PolicyName = "ActiveAdminSession";
}

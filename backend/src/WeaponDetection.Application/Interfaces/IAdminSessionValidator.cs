namespace WeaponDetection.Application.Interfaces;

// FS-01 §5.3 step 3 / §11, IP-01 §12 step 4: the server-side half of the two-check rule. A valid
// JWT signature and expiry are never sufficient on their own — the session the token names must
// also exist, belong to the user the token was issued for, be unexpired, and not have been
// revoked (ADR-013). Kept separate from IAuthService (which issues and revokes sessions) so the
// API layer's authorization handler depends only on the narrow contract it actually needs.
//
// The result is deliberately a single boolean rather than a failure-reason enum: FS-01 §11
// requires an absent, mismatched, expired, and revoked session to be indistinguishable to the
// caller, so there is no reason code here that a future caller could accidentally surface.
public interface IAdminSessionValidator
{
    Task<bool> IsSessionActiveAsync(
        Guid sessionId,
        Guid userId,
        CancellationToken cancellationToken = default);
}

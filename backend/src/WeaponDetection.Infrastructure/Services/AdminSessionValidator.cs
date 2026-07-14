using Microsoft.EntityFrameworkCore;
using WeaponDetection.Application.Interfaces;
using WeaponDetection.Infrastructure.Persistence;

namespace WeaponDetection.Infrastructure.Services;

// Lives in Infrastructure (not Application) for the same reason as AuthService and
// AdminBootstrapper: the implementation depends on WeaponDetectionDbContext, and Application
// must not reference Infrastructure. IAdminSessionValidator (Application) is the only type the
// API layer sees.
public class AdminSessionValidator : IAdminSessionValidator
{
    private readonly WeaponDetectionDbContext _dbContext;
    private readonly TimeProvider _timeProvider;

    public AdminSessionValidator(WeaponDetectionDbContext dbContext, TimeProvider timeProvider)
    {
        _dbContext = dbContext;
        _timeProvider = timeProvider;
    }

    public async Task<bool> IsSessionActiveAsync(
        Guid sessionId,
        Guid userId,
        CancellationToken cancellationToken = default)
    {
        if (sessionId == Guid.Empty || userId == Guid.Empty)
        {
            return false;
        }

        var now = _timeProvider.GetUtcNow();

        // The expiry predicate duplicates a check the JWT lifetime validation already performs.
        // That is intentional defence in depth, required by FS-01 §11 ("no matching, non-revoked,
        // non-expired record"): the session record — not only the bearer token — must be within
        // its own validity window for the request to proceed.
        //
        // AsNoTracking because this is a read-only authorization check running on the request's
        // scoped DbContext: it must not attach an AdminSession that the same request's later
        // business logic (e.g. logout in T-11) would then re-query and re-track.
        return await _dbContext.AdminSessions
            .AsNoTracking()
            .AnyAsync(
                session => session.SessionId == sessionId
                    && session.UserId == userId
                    && !session.Revoked
                    && session.ExpiresAt > now,
                cancellationToken);
    }
}

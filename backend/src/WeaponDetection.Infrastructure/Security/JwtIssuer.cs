using System.IdentityModel.Tokens.Jwt;
using System.Security.Claims;
using System.Text;
using Microsoft.Extensions.Options;
using Microsoft.IdentityModel.Tokens;
using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.Infrastructure.Security;

// IP-01 §7: creates a signed Admin access token with a fresh jti. HMAC-SHA256 (symmetric) is
// used consistently here and by future token-validation middleware (ARCH-001 §15); the signing
// key is sourced only from validated configuration (JwtOptions/JwtOptionsValidator), never
// hardcoded or logged.
public class JwtIssuer : IJwtIssuer
{
    private readonly JwtOptions _options;
    private readonly TimeProvider _timeProvider;

    public JwtIssuer(IOptions<JwtOptions> options, TimeProvider timeProvider)
    {
        _options = options.Value;
        _timeProvider = timeProvider;
    }

    public JwtIssuanceResult Issue(Guid userId)
    {
        if (userId == Guid.Empty)
        {
            throw new ArgumentException("User id is required.", nameof(userId));
        }

        var sessionId = Guid.NewGuid();
        var issuedAt = _timeProvider.GetUtcNow();
        var expiresAt = issuedAt.AddMinutes(_options.AccessTokenLifetimeMinutes);

        var claims = new[]
        {
            new Claim(JwtRegisteredClaimNames.Sub, userId.ToString()),
            new Claim(JwtRegisteredClaimNames.Jti, sessionId.ToString()),
        };

        var signingKey = new SymmetricSecurityKey(Encoding.UTF8.GetBytes(_options.SigningKey));
        var credentials = new SigningCredentials(signingKey, SecurityAlgorithms.HmacSha256);

        // The JwtPayload overload accepting an explicit issuedAt is used deliberately — the
        // parameterless-issuedAt JwtSecurityToken constructor stamps "iat" from the real system
        // clock, which would make the emitted claim diverge from the injected TimeProvider and
        // break deterministic testing.
        var header = new JwtHeader(credentials);
        var payload = new JwtPayload(
            issuer: _options.Issuer,
            audience: _options.Audience,
            claims: claims,
            notBefore: issuedAt.UtcDateTime,
            expires: expiresAt.UtcDateTime,
            issuedAt: issuedAt.UtcDateTime);

        var token = new JwtSecurityToken(header, payload);
        var accessToken = new JwtSecurityTokenHandler().WriteToken(token);

        return new JwtIssuanceResult(accessToken, sessionId, issuedAt, expiresAt);
    }
}

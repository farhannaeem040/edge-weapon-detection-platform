namespace WeaponDetection.Infrastructure.Security;

// Bound from configuration section "Jwt" (Jwt:Issuer, Jwt:Audience, Jwt:SigningKey,
// Jwt:AccessTokenLifetimeMinutes / the equivalent Jwt__* environment variables). SigningKey
// must come from user-secrets (local development) or an environment variable — never from a
// committed appsettings file. See JwtOptionsValidator for the enforced constraints.
public class JwtOptions
{
    public const string SectionName = "Jwt";

    public string Issuer { get; set; } = string.Empty;
    public string Audience { get; set; } = string.Empty;
    public string SigningKey { get; set; } = string.Empty;
    public int AccessTokenLifetimeMinutes { get; set; }
}

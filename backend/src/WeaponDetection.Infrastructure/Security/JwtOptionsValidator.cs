using System.Text;
using Microsoft.Extensions.Options;

namespace WeaponDetection.Infrastructure.Security;

// Runs eagerly at application startup via ValidateOnStart() (see DependencyInjection.cs) —
// an invalid JWT configuration fails startup immediately rather than remaining dormant until
// the first token issuance.
public class JwtOptionsValidator : IValidateOptions<JwtOptions>
{
    // HMAC-SHA256 requires a key of at least 256 bits (32 bytes) to be cryptographically
    // meaningful (RFC 7518 §3.2); UTF-8 byte length of the configured string is used as a
    // practical minimum-strength check.
    private const int MinimumSigningKeyBytes = 32;

    // A generous upper bound for a prototype access token lifetime — large enough to never
    // reject a reasonable configuration, small enough to catch obvious misconfiguration
    // (e.g. a value accidentally expressed in seconds or days instead of minutes).
    private const int MaximumLifetimeMinutes = 24 * 60;

    public ValidateOptionsResult Validate(string? name, JwtOptions options)
    {
        var failures = new List<string>();

        if (string.IsNullOrWhiteSpace(options.Issuer))
        {
            failures.Add("Jwt:Issuer is required.");
        }

        if (string.IsNullOrWhiteSpace(options.Audience))
        {
            failures.Add("Jwt:Audience is required.");
        }

        if (string.IsNullOrWhiteSpace(options.SigningKey))
        {
            failures.Add(
                "Jwt:SigningKey is required. Set it via .NET user-secrets (local development) or " +
                "the Jwt__SigningKey environment variable — never commit it.");
        }
        else if (Encoding.UTF8.GetByteCount(options.SigningKey) < MinimumSigningKeyBytes)
        {
            failures.Add(
                $"Jwt:SigningKey must be at least {MinimumSigningKeyBytes} bytes (UTF-8 encoded) " +
                "to provide sufficient HMAC-SHA256 key strength.");
        }

        if (options.AccessTokenLifetimeMinutes <= 0)
        {
            failures.Add("Jwt:AccessTokenLifetimeMinutes must be greater than zero.");
        }
        else if (options.AccessTokenLifetimeMinutes > MaximumLifetimeMinutes)
        {
            failures.Add($"Jwt:AccessTokenLifetimeMinutes must not exceed {MaximumLifetimeMinutes} minutes.");
        }

        return failures.Count == 0
            ? ValidateOptionsResult.Success
            : ValidateOptionsResult.Fail(failures);
    }
}

using System.Text;
using Microsoft.AspNetCore.Authentication.JwtBearer;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Authorization.Policy;
using Microsoft.Extensions.Options;
using Microsoft.IdentityModel.Tokens;
using WeaponDetection.Infrastructure.Security;

namespace WeaponDetection.Api.Security;

// IP-01 §12 / T-10: composes the Admin authentication pipeline — ASP.NET Core's JWT Bearer
// middleware (signature/issuer/audience/expiry) plus the AdminSession requirement
// (existence/ownership/revocation). Both checks must pass; neither alone is sufficient
// (FS-01 §10). Called from Program.cs, which remains the composition root.
public static class AdminAuthenticationExtensions
{
    public static IServiceCollection AddAdminAuthentication(this IServiceCollection services)
    {
        services.AddAuthentication(JwtBearerDefaults.AuthenticationScheme)
            .AddJwtBearer();

        // JwtOptions is bound and validated once, by AddInfrastructure (ValidateOnStart), and is
        // resolved lazily here so the JWT Bearer middleware verifies tokens with exactly the same
        // issuer, audience, and signing key that JwtIssuer signs them with — the values cannot
        // drift apart into a second, separately-bound copy.
        services.AddOptions<JwtBearerOptions>(JwtBearerDefaults.AuthenticationScheme)
            .Configure<IOptions<JwtOptions>>((bearerOptions, jwtOptions) =>
            {
                var jwt = jwtOptions.Value;

                // Keep `sub` and `jti` under their raw JWT names instead of ASP.NET Core's
                // legacy ClaimTypes.* remapping, so SessionAuthorizationHandler reads back
                // precisely the claims JwtIssuer wrote.
                bearerOptions.MapInboundClaims = false;

                bearerOptions.TokenValidationParameters = new TokenValidationParameters
                {
                    ValidateIssuer = true,
                    ValidIssuer = jwt.Issuer,
                    ValidateAudience = true,
                    ValidAudience = jwt.Audience,
                    ValidateIssuerSigningKey = true,
                    IssuerSigningKey = new SymmetricSecurityKey(Encoding.UTF8.GetBytes(jwt.SigningKey)),
                    ValidateLifetime = true,

                    // Pins verification to the one algorithm JwtIssuer signs with, so a token
                    // presenting a different `alg` header is rejected outright.
                    ValidAlgorithms = [SecurityAlgorithms.HmacSha256],

                    // The framework default allows five minutes of clock skew, which would keep
                    // a just-expired token usable well past its stated expiry. FS-01 §4/§12
                    // requires an expired JWT to be rejected, and the Dashboard and Backend share
                    // one host on the same LAN (ARCH-001 §12.1), so no skew allowance is warranted.
                    ClockSkew = TimeSpan.Zero,
                };
            });

        // Scoped: the handler depends on IAdminSessionValidator, which depends on the scoped
        // DbContext.
        services.AddScoped<IAuthorizationHandler, SessionAuthorizationHandler>();
        services.AddHttpContextAccessor();

        var adminPolicy = new AuthorizationPolicyBuilder(JwtBearerDefaults.AuthenticationScheme)
            .RequireAuthenticatedUser()
            .AddRequirements(new ActiveAdminSessionRequirement())
            .Build();

        services.AddAuthorizationBuilder()
            .AddPolicy(ActiveAdminSessionRequirement.PolicyName, adminPolicy)
            .SetDefaultPolicy(adminPolicy)
            // Deny by default (FS-01 §6: authentication applied *uniformly* to every
            // Dashboard-initiated operation, with the exceptions stated explicitly). An endpoint
            // that carries no authorization attribute at all is protected by this fallback
            // policy, so forgetting [Authorize] on a future controller cannot silently expose it.
            // The two documented exemptions — POST /api/v1/auth/login (FS-01 §9.1) and, once
            // T-20 adds it, POST /api/v1/activate (FS-02 §10.4) — opt out explicitly with
            // [AllowAnonymous].
            .SetFallbackPolicy(adminPolicy);

        // Turns the pipeline's challenge/forbid outcomes into the uniform 401 + error envelope.
        services.AddSingleton<IAuthorizationMiddlewareResultHandler, ApiEnvelopeAuthorizationResultHandler>();

        return services;
    }
}

using System;
using System.Collections.Generic;
using System.IdentityModel.Tokens.Jwt;
using System.Security.Claims;
using System.Text;
using Microsoft.IdentityModel.Tokens;

namespace WeaponDetection.IntegrationTests.Api;

// Mints JWTs the running host would otherwise never issue — a token with no `jti`, an expired
// token, a token signed with the wrong key, a token naming a session that does not exist — so the
// middleware's rejection paths (FS-01 §11, §14 T-04/T-05/T-14/T-15/T-16) can be exercised over
// real HTTP. Valid tokens still come from the real login endpoint, never from here.
//
// Deliberately constructs tokens with the JWT library directly rather than reusing JwtIssuer:
// JwtIssuer cannot produce these malformed shapes, and a test that could only ever assert what
// the production issuer emits would not test the verifier at all.
internal static class TestTokenBuilder
{
    public static string Create(
        Guid? userId,
        Guid? sessionId,
        DateTimeOffset issuedAt,
        DateTimeOffset expiresAt,
        string issuer = SqlServerApiHostFactory.JwtIssuer,
        string audience = SqlServerApiHostFactory.JwtAudience,
        string? signingKey = null)
    {
        var claims = new List<Claim>();

        if (userId is not null)
        {
            claims.Add(new Claim(JwtRegisteredClaimNames.Sub, userId.Value.ToString()));
        }

        if (sessionId is not null)
        {
            claims.Add(new Claim(JwtRegisteredClaimNames.Jti, sessionId.Value.ToString()));
        }

        var key = new SymmetricSecurityKey(
            Encoding.UTF8.GetBytes(signingKey ?? SqlServerApiHostFactory.JwtSigningKey));
        var credentials = new SigningCredentials(key, SecurityAlgorithms.HmacSha256);

        var payload = new JwtPayload(
            issuer: issuer,
            audience: audience,
            claims: claims,
            notBefore: issuedAt.UtcDateTime,
            expires: expiresAt.UtcDateTime,
            issuedAt: issuedAt.UtcDateTime);

        return new JwtSecurityTokenHandler().WriteToken(
            new JwtSecurityToken(new JwtHeader(credentials), payload));
    }
}

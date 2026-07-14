namespace WeaponDetection.Api.Contracts;

// The single authentication-failure envelope for the whole API (FS-01 §9.3, §11): every rejection
// — no Authorization header, a malformed or wrongly-signed token, an expired token, a token with
// no `jti`, an unknown or mismatched session, or a revoked session — must be indistinguishable to
// the caller.
//
// It lives here, rather than as literals at each rejection site, because two of those sites now
// exist: ApiEnvelopeAuthorizationResultHandler (the authorization middleware, T-10) and
// AuthController.Logout's defensive rejection (T-11). Sharing one definition is what guarantees
// the two produce a byte-identical body — a divergence in wording alone would tell an attacker
// which of the two rejected the request.
public static class AuthenticationFailure
{
    public const string ErrorCode = "UNAUTHORIZED";
    public const string Message = "Authentication is required.";

    public static ApiResponse Response() => ApiResponse.Fail(ErrorCode, Message);
}

namespace WeaponDetection.Api.Contracts;

// The single activation-failure envelope for POST /api/v1/activate (FS-02 §5.6, §10.4, §13, AC-15):
// a malformed key, an unknown keyId, an incorrect secret, an already-consumed key, and a key
// invalidated by regeneration must all be indistinguishable to the caller. The Backend distinguishes
// them internally (DeviceActivationFailureReason) only for its own control flow and tests; the wire
// response must never reveal which check failed, or an attacker could probe which keyIds exist or
// which secrets are close.
//
// It lives here, as one definition, rather than as literals at the controller's rejection site, so
// every rejected activation returns a byte-identical body — the same reason AuthenticationFailure is
// centralized. The status is 401 (FS-02 §10.4/§13): the Activation Key is the credential, so a bad
// key is an authentication failure, not a validation error.
public static class ActivationFailure
{
    public const string ErrorCode = "INVALID_ACTIVATION_KEY";
    public const string Message = "The activation key is invalid.";

    public static ApiResponse Response() => ApiResponse.Fail(ErrorCode, Message);
}

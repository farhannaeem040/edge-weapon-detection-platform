namespace WeaponDetection.Api.Contracts;

// The single credential-validation failure envelope for POST /api/v1/device/credentials/validate
// (FS-02 §10.5, IP-05 T-52/ADR-017): a missing/malformed DeviceId, an unknown device, an incorrect
// secret, a revoked/ReactivationRequired device, and an inconsistent missing-stored-secret device
// must all be indistinguishable to the caller. The Backend distinguishes them internally
// (DeviceCredentialValidationOutcome) only for its own tests; the wire response must never reveal
// which check failed, or an attacker could probe which DeviceIds exist or which secrets are close.
//
// It lives here, as one definition — mirroring ActivationFailure — so every rejected validation
// returns a byte-identical body. The status is 401: the device shared secret is the credential, so a
// bad credential is an authentication failure, not a validation error. Only a 401 carrying this exact
// code is the confirmed-revocation signal the Agent may lock on (ADR-017); every other outcome the
// Agent treats as ambiguous.
public static class DeviceCredentialFailure
{
    public const string ErrorCode = "INVALID_DEVICE_CREDENTIALS";
    public const string Message = "The device credentials are invalid.";

    public static ApiResponse Response() => ApiResponse.Fail(ErrorCode, Message);
}

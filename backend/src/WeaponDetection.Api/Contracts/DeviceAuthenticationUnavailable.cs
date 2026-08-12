namespace WeaponDetection.Api.Contracts;

// FS-07 §3.4: the response for DeviceCredentialValidationOutcome.CredentialStorageUnavailable —
// the server cannot currently decrypt the stored Device secret (e.g. a lost Data Protection key
// ring), which is a server-side failure, not a wrong credential. Deliberately a distinct envelope
// from DeviceCredentialFailure's 401: conflating the two would let a transient server-side outage
// be mistaken for (or mask) a confirmed credential revocation, which only a 401 carrying
// DeviceCredentialFailure.ErrorCode may signal (ADR-017). Status is 503: the request itself may be
// entirely valid, but the server cannot currently answer it.
public static class DeviceAuthenticationUnavailable
{
    public const string ErrorCode = "DEVICE_AUTHENTICATION_UNAVAILABLE";
    public const string Message = "Device authentication is temporarily unavailable.";

    public static ApiResponse Response() => ApiResponse.Fail(ErrorCode, Message);
}

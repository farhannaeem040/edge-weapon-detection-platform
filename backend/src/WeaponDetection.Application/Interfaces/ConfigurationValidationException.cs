namespace WeaponDetection.Application.Interfaces;

// FS-12 §3.1 — an Application-layer validation failure that carries its own named error code.
//
// The existing branch-creation path maps every ArgumentException to a single `VALIDATION_ERROR`,
// which is fine when the only question is "was the request well-formed". CameraKey failures are
// different: an administrator needs to know whether the key was malformed, reserved, or already
// taken, because each one implies a different fix. Rather than parse exception messages at the
// controller — which would couple the API surface to wording — the code travels with the exception.
//
// Deliberately derives from ArgumentException so every existing `catch (ArgumentException)` in the
// controllers keeps working unchanged; a controller that wants the specific code catches this type
// first, and one that does not still degrades to the generic 400 it produced before.
//
// The offending value is never interpolated into the message. A CameraKey is not a secret, but the
// same constructor is available to future callers whose values might be, and an exception is a log
// entry waiting to happen (FS-02 §11).
public class ConfigurationValidationException : ArgumentException
{
    public ConfigurationValidationException(string errorCode, string message)
        : base(message)
    {
        ErrorCode = errorCode;
    }

    public string ErrorCode { get; }
}

// The FS-12 §3.1 / §4 error codes, named once so the service that throws them and the tests that
// assert them cannot drift apart.
public static class ConfigurationErrorCodes
{
    public const string CameraKeyRequired = "CAMERA_KEY_REQUIRED";
    public const string CameraKeyInvalid = "CAMERA_KEY_INVALID";
    public const string CameraKeyReserved = "CAMERA_KEY_RESERVED";
    public const string CameraKeyAlreadyExists = "CAMERA_KEY_ALREADY_EXISTS";
    public const string CameraKeyImmutable = "CAMERA_KEY_IMMUTABLE";

    public const string JetsonHostRequired = "JETSON_HOST_REQUIRED";
    public const string JetsonHostInvalid = "JETSON_HOST_INVALID";
    public const string RtspOutputPortInvalid = "RTSP_OUTPUT_PORT_INVALID";
}

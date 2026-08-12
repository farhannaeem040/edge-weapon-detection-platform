using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.Api.Contracts;

// FS-11 §3 — the wire shape GET /api/v1/device/configuration returns. Carries no Device secret, no
// Activation Key, and no Data Protection detail: neither this DTO nor DeviceCameraConfigDto has a
// member to serialize them (same "no member to carry it" pattern as BranchResponseDto).
public sealed record DeviceConfigurationResponseDto(
    int SchemaVersion,
    string ConfigurationVersion,
    Guid DeviceId,
    Guid BranchId,
    DateTime GeneratedAtUtc,
    IReadOnlyList<DeviceCameraConfigDto> Cameras)
{
    public static DeviceConfigurationResponseDto From(DeviceConfigurationView view) =>
        new(
            view.SchemaVersion,
            view.ConfigurationVersion,
            view.DeviceId,
            view.BranchId,
            view.GeneratedAtUtc,
            view.Cameras.Select(DeviceCameraConfigDto.From).ToList());
}

// FS-11 §2: `Name` is display metadata only — the Agent must never use it as pipeline identity.
// `StreamUrl` is the authoritative source URI; it is intentionally NOT redacted here (unlike
// CameraResponseDto's admin-facing read, RtspUrlSanitizer.Redact) because the Agent is the one
// consumer that must actually connect to it — this response is never rendered in a browser and is
// never logged by the Backend.
// `OutputPath` (FS-11 §11, FS-12 §2.1) is the relative RTSP mount the Device publishes this Camera's
// annotated stream on — now "cameras/front-entrance" rather than "cameras/2613b331-...". It is derived
// from the administrator-defined CameraKey, so it carries no secret, never embeds the input
// StreamUrl's credentials, and is stable across a Camera rename and a StreamUrl change alike.
//
// `CameraKey` is returned alongside it for diagnostics and contract fidelity. `CameraId` remains the
// identity the Agent maps `source_id` to and the only value that reaches a DetectionEvent — FS-12
// changed what the stream is *called*, never what the Camera *is*.
public sealed record DeviceCameraConfigDto(
    Guid CameraId,
    string CameraKey,
    string Name,
    string StreamUrl,
    bool Enabled,
    int SourceOrder,
    string OutputPath)
{
    public static DeviceCameraConfigDto From(DeviceCameraConfigurationView view) =>
        new(
            view.CameraId,
            view.CameraKey,
            view.Name,
            view.StreamUrl,
            view.Enabled,
            view.SourceOrder,
            view.OutputPath);
}

namespace WeaponDetection.Application.Interfaces;

// FS-11 §3/§5, IP-13 T-222. Resolves the Camera configuration assigned to an already-authenticated
// Device — the Agent's authoritative runtime input for building its DeepStream pipeline. This
// service performs no credential check of its own; the caller (DeviceConfigurationController)
// authenticates via the existing IDeviceCredentialValidator first, exactly like every other
// device-authenticated endpoint, and passes this service only the resulting BranchId.
public interface IDeviceConfigurationService
{
    // Returns the authenticated Device's Branch's enabled Cameras, ordered by SourceOrder, plus a
    // deterministic configurationVersion hash over the pipeline-relevant fields (FS-11 §4). Never
    // returns a disabled Camera, another Branch's Camera, or any secret/credential material.
    Task<DeviceConfigurationView> GetConfigurationAsync(
        Guid deviceId, Guid branchId, CancellationToken cancellationToken = default);
}

// One Camera's pipeline-relevant configuration, as read for the Agent (FS-11 §3). `Name` is carried
// only as display metadata — no pipeline-identity code path may read it.
//
// `OutputPath` (FS-11 §11) is the relative RTSP mount this Camera's annotated stream is published on
// by the Device. It is derived from the immutable CameraId (Camera.DeriveOutputPath), so it is
// unique per Camera and unaffected by a rename or a StreamUrl change.
public sealed record DeviceCameraConfigurationView(
    Guid CameraId,
    // FS-12 §3 — carried for diagnostics and contract fidelity. `OutputPath` remains the authoritative
    // mount the Agent and Bridge act on; neither derives behaviour from the key itself.
    string CameraKey,
    string Name,
    string StreamUrl,
    bool Enabled,
    int SourceOrder,
    string OutputPath);

// The full response the Agent applies (FS-11 §3/§6). `ConfigurationVersion` changes if and only if
// the enabled-Camera set's CameraId/StreamUrl/SourceOrder tuple changes — never on a Name-only edit.
public sealed record DeviceConfigurationView(
    int SchemaVersion,
    string ConfigurationVersion,
    Guid DeviceId,
    Guid BranchId,
    DateTime GeneratedAtUtc,
    IReadOnlyList<DeviceCameraConfigurationView> Cameras);

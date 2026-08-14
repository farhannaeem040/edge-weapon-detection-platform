using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.Api.Contracts;

// FS-14 §5. Never carries CameraKey as a selection identity (CameraId is), and never carries
// RtspUrl/JetsonHost/RtspOutputPort — this is a menu of what can be watched, not a menu of sources.
public sealed record LiveMonitoringCameraDto(
    Guid CameraId, string Name, string CameraKey, bool MonitoringAvailable, bool InferenceAvailable)
{
    public static LiveMonitoringCameraDto From(LiveMonitoringCameraView view) =>
        new(view.CameraId, view.Name, view.CameraKey, view.MonitoringAvailable, view.InferenceAvailable);
}

// FS-14 §5. Deliberately just { branchId, cameraId, mode } — no url field exists on this contract,
// so a client cannot supply an arbitrary RTSP source even by accident.
public sealed record CreateLiveStreamRequestDto(Guid BranchId, Guid CameraId, string Mode);

// PlaybackUrl is always a relative, single-origin path (e.g. "/media/live-.../whep") — never a raw
// RTSP URL, never embeds a credential.
public sealed record LiveStreamResponseDto(
    Guid SessionId, Guid CameraId, string Mode, string PlaybackUrl, DateTime ExpiresAtUtc);

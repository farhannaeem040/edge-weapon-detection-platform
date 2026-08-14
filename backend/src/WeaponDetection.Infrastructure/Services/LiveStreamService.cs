using Microsoft.EntityFrameworkCore;
using WeaponDetection.Application.Interfaces;
using WeaponDetection.Domain;
using WeaponDetection.Infrastructure.Persistence;

namespace WeaponDetection.Infrastructure.Services;

// FS-14 §5, IP-16 T-6. Resolves both RTSP sources server-side and asks the media gateway to make one
// of them playable — the client never supplies a URL (LiveStreamRequest carries only branchId/
// cameraId/mode), so this is the SSRF/open-proxy boundary the feature must not weaken. Depends on
// the (scoped) DbContext and (singleton) IMediaGatewayClient, so it is scoped, mirroring
// AlertSnapshotRetrievalService's shape.
public class LiveStreamService : ILiveStreamService
{
    // Deterministic per (Camera, mode) — not per session — so every viewer of the same Camera+mode
    // shares one upstream RTSP pull through the gateway (FS-14 §5's "no N permanent connections").
    // "N" GUID format (no dashes) keeps the path name a single unbroken path segment.
    private static string BuildPathName(Guid cameraId, LiveStreamMode mode) =>
        $"live-{cameraId:N}-{(mode == LiveStreamMode.Monitoring ? "monitoring" : "inference")}";

    private readonly WeaponDetectionDbContext _dbContext;
    private readonly IMediaGatewayClient _gatewayClient;
    private readonly TimeProvider _timeProvider;

    public LiveStreamService(
        WeaponDetectionDbContext dbContext, IMediaGatewayClient gatewayClient, TimeProvider timeProvider)
    {
        _dbContext = dbContext ?? throw new ArgumentNullException(nameof(dbContext));
        _gatewayClient = gatewayClient ?? throw new ArgumentNullException(nameof(gatewayClient));
        _timeProvider = timeProvider ?? throw new ArgumentNullException(nameof(timeProvider));
    }

    public async Task<IReadOnlyList<LiveMonitoringCameraView>> ListCamerasAsync(
        Guid branchId, CancellationToken cancellationToken = default)
    {
        var device = await _dbContext.Devices
            .AsNoTracking()
            .SingleOrDefaultAsync(d => d.BranchId == branchId, cancellationToken);
        var inferenceAvailable = device is { JetsonHost: not null, RtspOutputPort: not null };

        var cameras = await _dbContext.Cameras
            .AsNoTracking()
            .Where(c => c.BranchId == branchId)
            .OrderBy(c => c.SourceOrder)
            .ToListAsync(cancellationToken);

        return cameras
            .Select(c => new LiveMonitoringCameraView(
                c.CameraId, c.Name, c.CameraKey, MonitoringAvailable: true, inferenceAvailable))
            .ToList();
    }

    public async Task<LiveStreamOutcome> CreateStreamAsync(
        LiveStreamRequest request, CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(request);

        // Both "unknown Camera" and "Camera belongs to a different Branch" collapse into the same
        // outcome — the established non-disclosure convention (AlertSnapshotUploadService).
        var camera = await _dbContext.Cameras
            .AsNoTracking()
            .SingleOrDefaultAsync(c => c.CameraId == request.CameraId, cancellationToken);
        if (camera is null || camera.BranchId != request.BranchId)
        {
            return LiveStreamOutcome.Failed(LiveStreamOutcomeKind.BranchOrCameraNotFound);
        }

        string sourceUrl;
        if (request.Mode == LiveStreamMode.Monitoring)
        {
            sourceUrl = camera.RtspUrl;
        }
        else
        {
            var device = await _dbContext.Devices
                .AsNoTracking()
                .SingleOrDefaultAsync(d => d.BranchId == request.BranchId, cancellationToken);
            if (device is not { JetsonHost: not null, RtspOutputPort: not null })
            {
                return LiveStreamOutcome.Failed(LiveStreamOutcomeKind.DeviceUnavailable);
            }

            sourceUrl = $"rtsp://{device.JetsonHost}:{device.RtspOutputPort}/{Camera.DeriveOutputPath(camera.CameraKey)}";
        }

        var pathName = BuildPathName(camera.CameraId, request.Mode);

        try
        {
            await _gatewayClient.EnsurePathAsync(pathName, sourceUrl, cancellationToken);
        }
        catch (MediaGatewayException)
        {
            return LiveStreamOutcome.Failed(LiveStreamOutcomeKind.GatewayUnavailable);
        }

        var now = _timeProvider.GetUtcNow().UtcDateTime;
        return LiveStreamOutcome.Created(
            sessionId: Guid.NewGuid(),
            playbackUrl: $"/media/{pathName}/whep",
            expiresAtUtc: now.AddMinutes(1));
    }
}

using System.Security.Cryptography;
using System.Text;
using Microsoft.EntityFrameworkCore;
using WeaponDetection.Application.Interfaces;
using WeaponDetection.Domain;
using WeaponDetection.Infrastructure.Persistence;

namespace WeaponDetection.Infrastructure.Services;

// FS-11 §3/§4/§5, IP-13 T-222. Reads (never writes) the Cameras of an already-authenticated Device's
// Branch and shapes them into the Agent's configuration contract. The caller is responsible for
// authenticating the Device first (DeviceConfigurationController, via the existing
// IDeviceCredentialValidator) — this service trusts the branchId it is given exactly as
// AlertSyncService trusts the branchId resolved by that same validator (FS-06 §6.2).
public class DeviceConfigurationService : IDeviceConfigurationService
{
    private const int SchemaVersion = 1;

    private readonly WeaponDetectionDbContext _dbContext;
    private readonly TimeProvider _timeProvider;

    public DeviceConfigurationService(WeaponDetectionDbContext dbContext, TimeProvider timeProvider)
    {
        _dbContext = dbContext;
        _timeProvider = timeProvider;
    }

    public async Task<DeviceConfigurationView> GetConfigurationAsync(
        Guid deviceId, Guid branchId, CancellationToken cancellationToken = default)
    {
        // Only this Branch's enabled Cameras — never another Branch's, never a disabled one (FS-11
        // §3: a disabled Camera is omitted entirely, not returned with enabled=false). Ordered by
        // SourceOrder, never by insertion/PK order (FS-11 §2).
        // Projected to the stored columns first, then shaped in memory: OutputPath is *derived*
        // (Camera.DeriveOutputPath), not a column, so it cannot take part in a server-side
        // projection. The ordering and filtering still happen in SQL.
        var stored = await _dbContext.Cameras
            .AsNoTracking()
            .Where(c => c.BranchId == branchId && c.Enabled)
            .OrderBy(c => c.SourceOrder)
            .Select(c => new { c.CameraId, c.CameraKey, c.Name, c.RtspUrl, c.Enabled, c.SourceOrder })
            .ToListAsync(cancellationToken);

        var cameras = stored
            .Select(c => new DeviceCameraConfigurationView(
                c.CameraId,
                c.CameraKey,
                c.Name,
                c.RtspUrl,
                c.Enabled,
                c.SourceOrder,
                // FS-12 §2.1: the mount is now derived from the administrator-defined CameraKey. The
                // Agent still maps source_id to c.CameraId — the key changed the stream's public
                // name, never the detection identity.
                Camera.DeriveOutputPath(c.CameraKey)))
            .ToList();

        return new DeviceConfigurationView(
            SchemaVersion,
            ComputeConfigurationVersion(cameras),
            deviceId,
            branchId,
            _timeProvider.GetUtcNow().UtcDateTime,
            cameras);
    }

    // FS-11 §4 / FS-12 §6: a deterministic hash over exactly CameraId|StreamUrl|SourceOrder|OutputPath
    // for each enabled Camera, in SourceOrder order. Name is deliberately excluded — renaming a Camera
    // must never change this value, so an Agent comparing it must never restart its Bridge for a
    // rename alone. JetsonHost and RtspOutputPort are excluded for the same reason and by
    // construction: they are not inputs here at all, so changing the advertised address cannot
    // restart the pipeline (FS-12 §9 item 10).
    //
    // OutputPath *is* included because it is pipeline-relevant — it determines the Bridge's output
    // mounts. Under FS-12 it now embeds the CameraKey, which is exactly why hashing it matters: the
    // one-time GUID→CameraKey migration changes this value once, producing precisely one controlled
    // Bridge restart. Because the key is immutable after creation, no ordinary edit can change it
    // again.
    private static string ComputeConfigurationVersion(IReadOnlyList<DeviceCameraConfigurationView> cameras)
    {
        var builder = new StringBuilder();
        foreach (var camera in cameras)
        {
            builder.Append(camera.CameraId).Append('|')
                .Append(camera.StreamUrl).Append('|')
                .Append(camera.SourceOrder).Append('|')
                .Append(camera.OutputPath).Append(';');
        }

        var hash = SHA256.HashData(Encoding.UTF8.GetBytes(builder.ToString()));
        return Convert.ToHexStringLower(hash);
    }
}

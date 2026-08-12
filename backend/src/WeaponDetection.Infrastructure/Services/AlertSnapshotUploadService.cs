using System.Security.Cryptography;
using Microsoft.EntityFrameworkCore;
using WeaponDetection.Application.Interfaces;
using WeaponDetection.Domain;
using WeaponDetection.Infrastructure.Persistence;
using WeaponDetection.Infrastructure.Storage;

namespace WeaponDetection.Infrastructure.Services;

// The validation/storage/attach pipeline for POST /api/v1/alerts/{alertId}/snapshot (FS-08 §9).
// Like AlertSyncService it depends on WeaponDetectionDbContext directly and lives in
// Infrastructure/Services behind an Application interface — the controller never sees EF entities.
//
// Validation order (FS-08 §9, binding): auth (delegated to the controller) -> Alert exists and
// belongs to the authenticated Device's Branch -> eventId matches Alert.EventId -> size/content-type
// bounds -> decode as a real JPEG -> recompute SHA-256 server-side -> store -> Alert.AttachSnapshot.
//
// The attach/conflict decision (Alert.AttachSnapshot) runs BEFORE the storage write, using a
// deterministic, content-independent reference (IAlertSnapshotStorage.BuildReference) — so a
// Conflict is detected, and returned, without ever writing bytes for the rejected upload (FS-08 §9:
// "never silently overwrite"). Only an Attached outcome reaches the actual filesystem write; only
// after that write succeeds is the mutated Alert persisted via SaveChangesAsync — if the write
// throws, SaveChangesAsync is never reached, so SnapshotReference stays NULL in the database (no
// partial state).
public class AlertSnapshotUploadService : IAlertSnapshotUploadService
{
    // FS-08 §9 leaves the exact numeric bounds to the implementer; chosen generously for a
    // single post-OSD frame at native camera resolution (§5) while still bounding memory use per
    // request. Public so tests and the controller (for a fast pre-flight Content-Length check, if
    // ever added) share one source of truth.
    public const long MaxSnapshotSizeBytes = 15 * 1024 * 1024; // 15 MiB
    public const string RequiredContentType = "image/jpeg";

    private readonly WeaponDetectionDbContext _dbContext;
    private readonly IAlertSnapshotStorage _storage;
    private readonly TimeProvider _timeProvider;

    public AlertSnapshotUploadService(
        WeaponDetectionDbContext dbContext, IAlertSnapshotStorage storage, TimeProvider timeProvider)
    {
        _dbContext = dbContext ?? throw new ArgumentNullException(nameof(dbContext));
        _storage = storage ?? throw new ArgumentNullException(nameof(storage));
        _timeProvider = timeProvider ?? throw new ArgumentNullException(nameof(timeProvider));
    }

    public async Task<SnapshotUploadOutcome> UploadAsync(
        SnapshotUploadRequest request, CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(request);

        // Alert exists and belongs to the authenticated Device's Branch (FS-08 §9), joined via
        // Camera.BranchId exactly the way AlertSyncService resolves Camera ownership (FS-06 §6.3) —
        // both "does not exist" and "belongs to a different Branch" collapse into the same 404
        // NOT_FOUND, mirroring this codebase's existing non-disclosure convention
        // (DeviceController/BranchController).
        var alert = await _dbContext.Alerts
            .SingleOrDefaultAsync(a => a.AlertId == request.AlertId, cancellationToken);

        if (alert is null)
        {
            return NotFound();
        }

        var camera = await _dbContext.Cameras
            .AsNoTracking()
            .SingleOrDefaultAsync(c => c.CameraId == alert.CameraId, cancellationToken);

        if (camera is null || camera.BranchId != request.BranchId)
        {
            return NotFound();
        }

        if (alert.EventId != request.EventId)
        {
            return SnapshotUploadOutcome.Rejected(
                SnapshotUploadOutcomeKind.EventIdMismatch,
                SnapshotUploadErrorCodes.EventIdMismatch,
                "The submitted eventId does not match the target alert.");
        }

        var content = request.Content;
        if (content is null || request.ContentLength <= 0)
        {
            return SnapshotUploadOutcome.Rejected(
                SnapshotUploadOutcomeKind.MissingFile,
                SnapshotUploadErrorCodes.MissingFile,
                "A non-empty snapshot file is required.");
        }

        if (request.ContentLength > MaxSnapshotSizeBytes)
        {
            return SnapshotUploadOutcome.Rejected(
                SnapshotUploadOutcomeKind.Oversized,
                SnapshotUploadErrorCodes.FileTooLarge,
                $"The snapshot must not exceed {MaxSnapshotSizeBytes} bytes.");
        }

        if (!string.Equals(request.ClaimedContentType, RequiredContentType, StringComparison.OrdinalIgnoreCase))
        {
            return SnapshotUploadOutcome.Rejected(
                SnapshotUploadOutcomeKind.UnsupportedMediaType,
                SnapshotUploadErrorCodes.UnsupportedMediaType,
                $"The snapshot content type must be '{RequiredContentType}'.");
        }

        // Bounded read: request.ContentLength has already been checked <= MaxSnapshotSizeBytes above,
        // and the +1 catches a caller whose declared length understates the actual stream (defense in
        // depth — never trust a caller-declared length alone).
        var buffer = new MemoryStream(capacity: (int)Math.Min(request.ContentLength, MaxSnapshotSizeBytes));
        var bytesCopied = await CopyBoundedAsync(
            content, buffer, MaxSnapshotSizeBytes + 1, cancellationToken);
        if (bytesCopied > MaxSnapshotSizeBytes)
        {
            return SnapshotUploadOutcome.Rejected(
                SnapshotUploadOutcomeKind.Oversized,
                SnapshotUploadErrorCodes.FileTooLarge,
                $"The snapshot must not exceed {MaxSnapshotSizeBytes} bytes.");
        }

        var bytes = buffer.ToArray();

        if (!JpegInspector.TryGetDimensions(bytes, out _))
        {
            return SnapshotUploadOutcome.Rejected(
                SnapshotUploadOutcomeKind.MalformedImage,
                SnapshotUploadErrorCodes.InvalidImage,
                "The uploaded file is not a valid JPEG image.");
        }

        // NEVER trusted for the actual duplicate/conflict decision (FS-08 §9) — the server-recomputed
        // digest below is the sole authority. A mismatch here only means the bytes that arrived are
        // not the bytes the client thinks it sent (transit corruption or a stale client), so it is
        // rejected the same way a structurally invalid image is.
        var computedSha256 = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
        if (!string.IsNullOrEmpty(request.ClaimedSha256)
            && !string.Equals(request.ClaimedSha256, computedSha256, StringComparison.OrdinalIgnoreCase))
        {
            return SnapshotUploadOutcome.Rejected(
                SnapshotUploadOutcomeKind.MalformedImage,
                SnapshotUploadErrorCodes.InvalidImage,
                "The uploaded file's contents do not match the declared checksum.");
        }

        var reference = _storage.BuildReference(alert.AlertId);
        var receivedAtUtc = _timeProvider.GetUtcNow().UtcDateTime;

        var attachOutcome = alert.AttachSnapshot(reference, computedSha256, RequiredContentType, bytes.LongLength, receivedAtUtc);

        switch (attachOutcome)
        {
            case AttachSnapshotOutcome.Conflict:
                // Never touches storage or the database — the existing snapshot is preserved
                // untouched (FS-08 §9).
                return SnapshotUploadOutcome.Rejected(
                    SnapshotUploadOutcomeKind.Conflict,
                    SnapshotUploadErrorCodes.SnapshotConflict,
                    "This alert already has a different snapshot on file.");

            case AttachSnapshotOutcome.Duplicate:
                // Identical bytes already stored under the same deterministic reference — no second
                // file, no database write needed (Alert.AttachSnapshot made no field changes).
                return SnapshotUploadOutcome.Duplicate(reference);

            case AttachSnapshotOutcome.Attached:
            default:
                // Only reached for a genuinely new snapshot. If the storage write throws, this
                // propagates to the controller as an unexpected failure (FS-08 §9's "500 unexpected
                // only") and SaveChangesAsync below is never reached — the Alert's in-memory mutation
                // is discarded with the request-scoped DbContext, so SnapshotReference stays NULL in
                // the database (no partial state).
                buffer.Position = 0;
                await _storage.SaveAsync(alert.AlertId, buffer, cancellationToken);
                await _dbContext.SaveChangesAsync(cancellationToken);
                return SnapshotUploadOutcome.Accepted(reference);
        }
    }

    private static SnapshotUploadOutcome NotFound() =>
        SnapshotUploadOutcome.Rejected(
            SnapshotUploadOutcomeKind.AlertNotOwnedOrFound,
            SnapshotUploadErrorCodes.NotFound,
            "Alert not found.");

    // Copies at most maxBytes+1 bytes from source into destination, returning the number of bytes
    // actually copied (which will exceed maxBytes only when the source stream is itself oversized —
    // the caller treats that as the Oversized outcome). Never buffers more than maxBytes+1 in memory.
    private static async Task<long> CopyBoundedAsync(
        Stream source, Stream destination, long maxBytes, CancellationToken cancellationToken)
    {
        var readBuffer = new byte[81920];
        long total = 0;

        int read;
        while ((read = await source.ReadAsync(readBuffer, cancellationToken)) > 0)
        {
            total += read;
            if (total > maxBytes)
            {
                return total;
            }

            await destination.WriteAsync(readBuffer.AsMemory(0, read), cancellationToken);
        }

        return total;
    }
}

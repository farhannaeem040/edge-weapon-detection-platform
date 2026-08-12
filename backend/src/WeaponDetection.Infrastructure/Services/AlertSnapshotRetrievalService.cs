using Microsoft.EntityFrameworkCore;
using WeaponDetection.Application.Interfaces;
using WeaponDetection.Infrastructure.Persistence;

namespace WeaponDetection.Infrastructure.Services;

// The lookup for GET /api/v1/alerts/{alertId}/snapshot (FS-08 §12). Reads through the same
// IAlertSnapshotStorage the POST pipeline (AlertSnapshotUploadService) writes through — there is
// only ever one snapshot storage location, never a second read-only copy.
//
// Alert.SnapshotReference itself is never trusted as a filesystem path (it never has been one —
// IAlertSnapshotStorage.ReadAsync takes the AlertId and derives the path internally); this service
// only uses SnapshotReference's presence/absence to short-circuit a storage read for an Alert that
// has never had a snapshot attached.
public class AlertSnapshotRetrievalService : IAlertSnapshotRetrievalService
{
    private readonly WeaponDetectionDbContext _dbContext;
    private readonly IAlertSnapshotStorage _storage;

    public AlertSnapshotRetrievalService(WeaponDetectionDbContext dbContext, IAlertSnapshotStorage storage)
    {
        _dbContext = dbContext ?? throw new ArgumentNullException(nameof(dbContext));
        _storage = storage ?? throw new ArgumentNullException(nameof(storage));
    }

    public async Task<SnapshotRetrievalOutcome> GetSnapshotAsync(
        Guid alertId, CancellationToken cancellationToken = default)
    {
        var alert = await _dbContext.Alerts
            .AsNoTracking()
            .SingleOrDefaultAsync(a => a.AlertId == alertId, cancellationToken);

        if (alert is null || alert.SnapshotReference is null || alert.SnapshotContentType is null)
        {
            return SnapshotRetrievalOutcome.NotFound();
        }

        // A SnapshotReference/ContentType set on the Alert but no file on disk is a
        // storage/database inconsistency, not a client error — still 404 (missing physical file
        // handled safely, never a 500) rather than serving corrupt/absent bytes.
        var bytes = await _storage.ReadAsync(alertId, cancellationToken);
        if (bytes is null)
        {
            return SnapshotRetrievalOutcome.NotFound();
        }

        return SnapshotRetrievalOutcome.Found(bytes, alert.SnapshotContentType);
    }
}

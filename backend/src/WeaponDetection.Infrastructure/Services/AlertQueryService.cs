using Microsoft.EntityFrameworkCore;
using WeaponDetection.Application.Interfaces;
using WeaponDetection.Domain;
using WeaponDetection.Infrastructure.Persistence;

namespace WeaponDetection.Infrastructure.Services;

// Read-only Alert list/detail projections for the Admin Dashboard (FS-10 §6, IP-12 T-192). Like
// BranchService it depends on WeaponDetectionDbContext directly and lives in Infrastructure/Services
// behind an Application interface. Every query is AsNoTracking (FS-10 §6: "no N+1 query pattern") —
// Alert carries no navigation properties, so Camera/Branch are joined explicitly by their FK Guids in
// one LINQ query translated to a single SQL statement, never a per-row follow-up lookup.
//
// Alert.DeviceId is already the authenticated device's *external* DeviceId (Alert.cs's own
// documented invariant) — there is no separate Device join needed to display it.
public class AlertQueryService : IAlertQueryService
{
    private readonly WeaponDetectionDbContext _dbContext;

    public AlertQueryService(WeaponDetectionDbContext dbContext)
    {
        _dbContext = dbContext ?? throw new ArgumentNullException(nameof(dbContext));
    }

    public async Task<AlertPageResult> ListAlertsAsync(
        AlertListQuery query, CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(query);

        var joined =
            from alert in _dbContext.Alerts.AsNoTracking()
            join camera in _dbContext.Cameras.AsNoTracking() on alert.CameraId equals camera.CameraId
            join branch in _dbContext.Branches.AsNoTracking() on camera.BranchId equals branch.BranchId
            select new { alert, camera, branch };

        if (query.FromUtc is { } fromUtc)
        {
            joined = joined.Where(x => x.alert.DetectedAtUtc >= fromUtc);
        }

        if (query.ToUtc is { } toUtc)
        {
            joined = joined.Where(x => x.alert.DetectedAtUtc < toUtc);
        }

        if (query.ClassName is { } className)
        {
            joined = joined.Where(x => x.alert.ClassName == className);
        }

        if (query.BranchId is { } branchId)
        {
            joined = joined.Where(x => x.branch.BranchId == branchId);
        }

        if (query.CameraId is { } cameraId)
        {
            joined = joined.Where(x => x.camera.CameraId == cameraId);
        }

        if (query.Status is { } status && Enum.TryParse<AlertStatus>(status, ignoreCase: true, out var statusValue))
        {
            joined = joined.Where(x => x.alert.Status == statusValue);
        }

        if (query.SnapshotAvailable is { } snapshotAvailable)
        {
            joined = joined.Where(x => (x.alert.SnapshotReference != null) == snapshotAvailable);
        }

        // Whitelisted sort only (FS-10 §9.2/§11) — SortBy is an enum, never a raw string, so there is
        // no dynamic-ORDER-BY/SQL-injection surface here.
        joined = (query.SortBy, query.SortDescending) switch
        {
            (AlertSortField.ReceivedAtUtc, true) => joined.OrderByDescending(x => x.alert.ReceivedAtUtc),
            (AlertSortField.ReceivedAtUtc, false) => joined.OrderBy(x => x.alert.ReceivedAtUtc),
            (AlertSortField.DetectedAtUtc, true) => joined.OrderByDescending(x => x.alert.DetectedAtUtc),
            _ => joined.OrderBy(x => x.alert.DetectedAtUtc),
        };

        var totalCount = await joined.CountAsync(cancellationToken);

        var page = Math.Max(query.Page, 1);
        var pageSize = Math.Clamp(query.PageSize, 1, AlertListQuery.MaxPageSize);

        var rows = await joined
            .Skip((page - 1) * pageSize)
            .Take(pageSize)
            .Select(x => new AlertListItemView(
                x.alert.AlertId,
                x.alert.DetectedAtUtc,
                x.alert.ReceivedAtUtc,
                x.alert.ClassName,
                x.alert.Confidence,
                x.branch.BranchId,
                x.branch.Name,
                x.camera.CameraId,
                x.camera.Name,
                x.alert.DeviceId,
                x.alert.Status.ToString(),
                x.alert.SnapshotReference != null))
            .ToListAsync(cancellationToken);

        return new AlertPageResult(rows, totalCount, page, pageSize);
    }

    public async Task<AlertDetailView?> GetAlertAsync(
        Guid alertId, CancellationToken cancellationToken = default)
    {
        var row =
            await (
                from alert in _dbContext.Alerts.AsNoTracking()
                join camera in _dbContext.Cameras.AsNoTracking() on alert.CameraId equals camera.CameraId
                join branch in _dbContext.Branches.AsNoTracking() on camera.BranchId equals branch.BranchId
                where alert.AlertId == alertId
                select new AlertDetailView(
                    alert.AlertId,
                    alert.EventId,
                    alert.DetectedAtUtc,
                    alert.ReceivedAtUtc,
                    alert.ClassId,
                    alert.ClassName,
                    alert.Confidence,
                    branch.BranchId,
                    branch.Name,
                    camera.CameraId,
                    camera.Name,
                    alert.DeviceId,
                    alert.Status.ToString(),
                    alert.SnapshotReference != null))
            .SingleOrDefaultAsync(cancellationToken);

        return row;
    }
}

using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Options;
using WeaponDetection.Application.Interfaces;
using WeaponDetection.Infrastructure.Persistence;

namespace WeaponDetection.Infrastructure.Services;

// Assembles the bounded Admin Dashboard summary (FS-10 §6, §9.1, IP-12 T-193). Read-only: this
// service never writes a BranchDailyAlertQuota row and never touches quota enforcement, which remains
// solely AlertSyncService's responsibility (FS-09).
//
// Manual-review Correction 3: the caller always supplies the Branch explicitly. There is no
// "first Branch in the database" fallback — with more than one Branch now a normal, supported
// configuration, silently picking one would either hide a Branch's own data behind another's or
// (worse) make the picked Branch appear to be *the* system total. Every value returned belongs to
// exactly the requested Branch: its own quota row, its own Device/Camera counts, its own latest Alert.
public class DashboardSummaryService : IDashboardSummaryService
{
    private readonly WeaponDetectionDbContext _dbContext;
    private readonly TimeProvider _timeProvider;
    private readonly AlertQuotaOptions _quotaOptions;

    public DashboardSummaryService(
        WeaponDetectionDbContext dbContext, TimeProvider timeProvider, IOptions<AlertQuotaOptions> quotaOptions)
    {
        _dbContext = dbContext ?? throw new ArgumentNullException(nameof(dbContext));
        _timeProvider = timeProvider ?? throw new ArgumentNullException(nameof(timeProvider));
        _quotaOptions = quotaOptions?.Value ?? throw new ArgumentNullException(nameof(quotaOptions));
    }

    public async Task<DashboardSummaryView?> GetSummaryAsync(
        Guid branchId, CancellationToken cancellationToken = default)
    {
        var branch = await _dbContext.Branches
            .AsNoTracking()
            .Where(b => b.BranchId == branchId)
            .Select(b => new { b.BranchId, b.Name, b.TimeZoneId })
            .SingleOrDefaultAsync(cancellationToken);

        if (branch is null)
        {
            return null;
        }

        var nowUtc = _timeProvider.GetUtcNow().UtcDateTime;
        var timeZone = BranchLocalDateResolver.ResolveTimeZone(branch.TimeZoneId);
        var localDate = BranchLocalDateResolver.ResolveLocalDate(nowUtc, timeZone);
        var nextResetAtUtc = BranchLocalDateResolver.ResolveNextResetUtc(nowUtc, timeZone);

        var quotaRow = await _dbContext.BranchDailyAlertQuotas
            .AsNoTracking()
            .Where(q => q.BranchId == branch.BranchId && q.LocalDate == localDate)
            .Select(q => new
            {
                q.AcceptedAlertCount,
                q.SuppressedDetectionCount,
                q.GunSuppressedCount,
                q.KnifeSuppressedCount,
            })
            .SingleOrDefaultAsync(cancellationToken);

        // No row yet for today is a valid zero-state (FS-09 §6.1: a row is created lazily on first
        // event of the day), never an error.
        var accepted = quotaRow?.AcceptedAlertCount ?? 0;
        var suppressedTotal = quotaRow?.SuppressedDetectionCount ?? 0;
        var suppressedGun = quotaRow?.GunSuppressedCount ?? 0;
        var suppressedKnife = quotaRow?.KnifeSuppressedCount ?? 0;

        var latestAlertAtUtc = await _dbContext.Alerts
            .AsNoTracking()
            .Where(a => _dbContext.Cameras
                .Where(c => c.BranchId == branch.BranchId)
                .Select(c => c.CameraId)
                .Contains(a.CameraId))
            .OrderByDescending(a => a.DetectedAtUtc)
            .Select(a => (DateTime?)a.DetectedAtUtc)
            .FirstOrDefaultAsync(cancellationToken);

        var deviceCount = await _dbContext.Devices
            .AsNoTracking()
            .CountAsync(d => d.BranchId == branch.BranchId, cancellationToken);
        var cameraCount = await _dbContext.Cameras
            .AsNoTracking()
            .CountAsync(c => c.BranchId == branch.BranchId, cancellationToken);

        var maximum = _quotaOptions.MaximumPerBranchPerDay;

        return new DashboardSummaryView(
            branch.BranchId,
            branch.Name,
            branch.TimeZoneId,
            localDate,
            nextResetAtUtc,
            accepted,
            maximum,
            Math.Max(0, maximum - accepted),
            latestAlertAtUtc,
            suppressedTotal,
            suppressedGun,
            suppressedKnife,
            deviceCount,
            cameraCount);
    }
}

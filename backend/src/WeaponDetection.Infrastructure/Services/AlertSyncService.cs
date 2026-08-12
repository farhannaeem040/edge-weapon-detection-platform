using System.Collections.Concurrent;
using Microsoft.Data.SqlClient;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Storage;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using WeaponDetection.Application.Interfaces;
using WeaponDetection.Domain;
using WeaponDetection.Infrastructure.Persistence;
using WeaponDetection.Infrastructure.Persistence.Configurations;

namespace WeaponDetection.Infrastructure.Services;

// The idempotent-insert-with-conflict-resolution engine for POST /api/v1/sync/events (FS-06 §5.2,
// §6.3), extended by FS-09 §7/§8 with the Branch daily Alert quota. Like BranchService/DeviceService it
// depends on WeaponDetectionDbContext directly and lives in Infrastructure/Services behind an
// Application interface — the controller never sees EF entities.
//
// One transaction spans the whole batch (FS-06 §5.2): an invalid, conflicting, or quota-suppressed
// event never rolls back an independently valid one in the same batch, because each attempted write
// gets its own savepoint. The database's unique index on (DeviceId, EventId) — not a check-then-insert
// in application code — remains the sole authority for event-identity concurrent-retry safety (§5.2,
// OI-12), and is always checked strictly before any quota logic runs (FS-09 §7/§8): a duplicate retry
// never consumes quota, whether or not the original submission consumed it.
public class AlertSyncService : IAlertSyncService
{
    // Best-effort, in-process, per-(Branch, LocalDate) suppression-logging throttle (FS-09 §13). This
    // is purely a logging concern, not a correctness mechanism — the atomic quota counters in SQL
    // Server remain the only authority for accept/suppress decisions. Resetting on process restart is
    // acceptable: worst case is one extra "first reach" log line, never a missed enforcement.
    private static readonly ConcurrentDictionary<(Guid BranchId, string LocalDate), int> SuppressionLogCounts = new();
    private const int MaxIndividualSuppressionLogsPerBranchDay = 3;
    private const int SummaryLogEveryNSuppressions = 50;

    private readonly WeaponDetectionDbContext _dbContext;
    private readonly TimeProvider _timeProvider;
    private readonly AlertQuotaOptions _quotaOptions;
    private readonly ILogger<AlertSyncService> _logger;

    public AlertSyncService(
        WeaponDetectionDbContext dbContext,
        TimeProvider timeProvider,
        IOptions<AlertQuotaOptions> quotaOptions,
        ILogger<AlertSyncService> logger)
    {
        _dbContext = dbContext ?? throw new ArgumentNullException(nameof(dbContext));
        _timeProvider = timeProvider ?? throw new ArgumentNullException(nameof(timeProvider));
        _quotaOptions = quotaOptions?.Value ?? throw new ArgumentNullException(nameof(quotaOptions));
        _logger = logger ?? throw new ArgumentNullException(nameof(logger));
    }

    public async Task<IReadOnlyList<SyncEventOutcome>> SyncEventsAsync(
        Guid deviceId,
        Guid branchId,
        IReadOnlyList<DetectionEventSyncItem> events,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(events);

        var results = new List<SyncEventOutcome>(events.Count);

        // Resolved once per batch, not per event — the Branch (and therefore its timezone) is
        // constant across the whole request (FS-09 §4). Falls back to UTC, explicitly, whenever the
        // Branch has no TimeZoneId or (defensively) an unresolvable one — Branch.UpdateTimeZone
        // already rejects an unresolvable identifier at write time, so this should never trigger in
        // practice, but the quota day must never silently assume the Backend server's own timezone.
        var quotaTimeZone = await ResolveBranchTimeZoneAsync(branchId, cancellationToken);

        await using var transaction =
            await _dbContext.Database.BeginTransactionAsync(cancellationToken);

        var index = 0;
        foreach (var evt in events)
        {
            index++;

            // Validation failures never attempt a write, so they need no savepoint (FS-06 §5.2).
            if (!IsConfidenceValid(evt.Confidence))
            {
                results.Add(SyncEventOutcome.Rejected(evt.EventId, SyncEventErrorCodes.InvalidConfidence));
                continue;
            }

            if (!IsFrameAndBoundingBoxValid(evt))
            {
                results.Add(SyncEventOutcome.Rejected(evt.EventId, SyncEventErrorCodes.InvalidBoundingBox));
                continue;
            }

            // Camera resolution (FS-11 §10): the immutable Camera.CameraId GUID, scoped to the
            // authenticated device's own Branch, and only an enabled Camera qualifies. A legacy
            // Camera.Name match remains as a transitional fallback — see ResolveCameraIdAsync.
            var resolvedCameraId = await ResolveCameraIdAsync(branchId, evt.CameraId, cancellationToken);
            if (resolvedCameraId is null)
            {
                results.Add(SyncEventOutcome.Rejected(evt.EventId, SyncEventErrorCodes.UnknownCamera));
                continue;
            }

            var savepointName = $"event_{index}";
            await transaction.CreateSavepointAsync(savepointName, cancellationToken);

            // Idempotency first, unconditionally, before any quota logic runs (FS-09 §7/§8 step 1) —
            // an explicit lookup rather than an optimistic insert-then-catch, since a quota-consuming
            // insert must never be attempted for an event that already has an Alert.
            var existingAlert = await _dbContext.Alerts
                .AsNoTracking()
                .SingleOrDefaultAsync(a => a.DeviceId == deviceId && a.EventId == evt.EventId, cancellationToken);

            if (existingAlert is not null)
            {
                results.Add(ImmutableFieldsMatch(existingAlert, evt, resolvedCameraId.Value)
                    ? SyncEventOutcome.Duplicate(evt.EventId, existingAlert.AlertId)
                    : SyncEventOutcome.Rejected(evt.EventId, SyncEventErrorCodes.EventDataConflict));
                continue;
            }

            if (!_quotaOptions.Enabled)
            {
                // Quota disabled (AlertQuota:Enabled=false, IP-11 §4 Rollback): behaves exactly as
                // FS-06's original unconditional-accept-subject-to-idempotency flow.
                results.Add(await InsertAlertAsync(
                    transaction, savepointName, deviceId, branchId, evt, resolvedCameraId.Value, cancellationToken));
                continue;
            }

            var localDate = ResolveLocalDate(evt.DetectedAtUtc, quotaTimeZone);

            // Suppressed-retry idempotency (FS-09 §7/§8 step 2): a retried quota-suppressed EventId
            // must never increment suppression statistics a second time.
            var alreadySuppressed = await _dbContext.SuppressedDetectionEvents
                .AsNoTracking()
                .AnyAsync(s => s.DeviceId == deviceId && s.EventId == evt.EventId, cancellationToken);

            if (alreadySuppressed)
            {
                results.Add(SyncEventOutcome.QuotaExceeded(
                    evt.EventId, _quotaOptions.MaximumPerBranchPerDay, localDate));
                continue;
            }

            results.Add(await EnforceQuotaAndInsertAsync(
                transaction, savepointName, deviceId, branchId, evt, resolvedCameraId.Value, localDate,
                cancellationToken));
        }

        await transaction.CommitAsync(cancellationToken);

        return results;
    }

    // FS-06 §5.2's original unconditional insert path, used only when AlertQuota:Enabled=false.
    private async Task<SyncEventOutcome> InsertAlertAsync(
        IDbContextTransaction transaction,
        string savepointName,
        Guid deviceId,
        Guid branchId,
        DetectionEventSyncItem evt,
        Guid resolvedCameraId,
        CancellationToken cancellationToken)
    {
        try
        {
            var alert = BuildAlert(deviceId, resolvedCameraId, evt);
            _dbContext.Alerts.Add(alert);
            await _dbContext.SaveChangesAsync(cancellationToken);

            return SyncEventOutcome.Accepted(evt.EventId, alert.AlertId);
        }
        catch (DbUpdateException ex) when (IsUniqueIndexViolation(
            ex, AlertConfiguration.DeviceIdEventIdUniqueIndexName))
        {
            await RollbackAndReestablishSavepointAsync(transaction, savepointName, cancellationToken);

            return await ResolveDuplicateOrConflictAsync(deviceId, evt, resolvedCameraId, cancellationToken);
        }
    }

    // SQL Server does not allow rolling back to the same savepoint more than once (a second
    // ROLLBACK TRANSACTION savepoint_name against a name already used as a rollback target fails with
    // "no transaction or savepoint of that name was found"). Since a single event's processing can hit
    // more than one race in sequence (e.g. the quota-row insert-if-absent race in
    // EnsureQuotaRowExistsAsync, followed later by the Alert-insert race in the same event's
    // EnforceQuotaAndInsertAsync call), every rollback re-creates the savepoint immediately afterward
    // so it remains valid for any later write in the same event's processing.
    private async Task RollbackAndReestablishSavepointAsync(
        IDbContextTransaction transaction, string savepointName, CancellationToken cancellationToken)
    {
        await transaction.RollbackToSavepointAsync(savepointName, cancellationToken);
        _dbContext.ChangeTracker.Clear();
        await transaction.CreateSavepointAsync(savepointName, cancellationToken);
    }

    // FS-09 §7 steps 4a/4b: get-or-create the Branch/day quota row, atomically consume it, and insert
    // the Alert in the same savepoint as the increment — a failed Alert insert (a race against a
    // concurrent duplicate of the same EventId) rolls back the increment together with it, so it never
    // consumes quota (FS-09 §7 "a failed Alert insert must not consume quota").
    private async Task<SyncEventOutcome> EnforceQuotaAndInsertAsync(
        IDbContextTransaction transaction,
        string savepointName,
        Guid deviceId,
        Guid branchId,
        DetectionEventSyncItem evt,
        Guid resolvedCameraId,
        string localDate,
        CancellationToken cancellationToken)
    {
        var maximum = _quotaOptions.MaximumPerBranchPerDay;

        await EnsureQuotaRowExistsAsync(transaction, savepointName, branchId, localDate, cancellationToken);

        var incrementedRows = await _dbContext.BranchDailyAlertQuotas
            .Where(q => q.BranchId == branchId && q.LocalDate == localDate && q.AcceptedAlertCount < maximum)
            .ExecuteUpdateAsync(
                setters => setters.SetProperty(q => q.AcceptedAlertCount, q => q.AcceptedAlertCount + 1),
                cancellationToken);

        if (incrementedRows == 1)
        {
            try
            {
                var alert = BuildAlert(deviceId, resolvedCameraId, evt);
                _dbContext.Alerts.Add(alert);
                await _dbContext.SaveChangesAsync(cancellationToken);

                return SyncEventOutcome.Accepted(evt.EventId, alert.AlertId);
            }
            catch (DbUpdateException ex) when (IsUniqueIndexViolation(
                ex, AlertConfiguration.DeviceIdEventIdUniqueIndexName))
            {
                // Undoes both the Alert insert attempt and the quota increment together — the
                // increment above is in the same savepoint scope, so this single rollback satisfies
                // "a failed Alert insert must not consume quota" without any compensating write.
                await RollbackAndReestablishSavepointAsync(transaction, savepointName, cancellationToken);

                return await ResolveDuplicateOrConflictAsync(deviceId, evt, resolvedCameraId, cancellationToken);
            }
        }

        // Quota exhausted for this Branch/day (FS-09 §7 step 4b). SuppressedDetectionEvent is
        // inserted first, before any counter mutation, so a race between two concurrent submissions of
        // the same not-yet-suppressed EventId is caught by its own unique key before either counter is
        // touched — no compensating decrement is ever needed.
        var suppressedEvent = new SuppressedDetectionEvent(
            deviceId,
            evt.EventId,
            branchId,
            localDate,
            evt.ClassName,
            evt.DetectedAtUtc,
            SuppressedDetectionEvent.BranchDailyAlertQuotaReachedReason,
            _timeProvider.GetUtcNow().UtcDateTime);

        try
        {
            _dbContext.SuppressedDetectionEvents.Add(suppressedEvent);
            await _dbContext.SaveChangesAsync(cancellationToken);
        }
        catch (DbUpdateException ex) when (IsUniqueIndexViolation(
            ex, SuppressedDetectionEventConfiguration.DeviceIdEventIdPrimaryKeyName))
        {
            // Another concurrent request already recorded the suppression for this exact EventId —
            // treat this one as the same idempotent outcome, without incrementing counters again.
            await RollbackAndReestablishSavepointAsync(transaction, savepointName, cancellationToken);

            return SyncEventOutcome.QuotaExceeded(evt.EventId, maximum, localDate);
        }

        await IncrementSuppressionCountersAsync(branchId, localDate, evt.ClassName, cancellationToken);
        LogSuppression(branchId, localDate, maximum, evt.ClassName);

        return SyncEventOutcome.QuotaExceeded(evt.EventId, maximum, localDate);
    }

    // Insert-if-absent for the Branch/day quota row (FS-09 §7 step 4a), mirroring Alert's own
    // insert-then-catch idempotency style. A unique-key race here means a concurrent request for the
    // same Branch/day already created the row — that is not an error, just proceed to the conditional
    // increment against the now-existing row.
    private async Task EnsureQuotaRowExistsAsync(
        IDbContextTransaction transaction,
        string savepointName,
        Guid branchId,
        string localDate,
        CancellationToken cancellationToken)
    {
        var exists = await _dbContext.BranchDailyAlertQuotas
            .AsNoTracking()
            .AnyAsync(q => q.BranchId == branchId && q.LocalDate == localDate, cancellationToken);

        if (exists)
        {
            return;
        }

        try
        {
            _dbContext.BranchDailyAlertQuotas.Add(new BranchDailyAlertQuota(branchId, localDate));
            await _dbContext.SaveChangesAsync(cancellationToken);
        }
        catch (DbUpdateException ex) when (IsUniqueIndexViolation(
            ex, BranchDailyAlertQuotaConfiguration.BranchIdLocalDatePrimaryKeyName))
        {
            await RollbackAndReestablishSavepointAsync(transaction, savepointName, cancellationToken);
        }
    }

    private async Task IncrementSuppressionCountersAsync(
        Guid branchId, string localDate, string className, CancellationToken cancellationToken)
    {
        var normalizedClassName = className.Trim().ToLowerInvariant();
        var isGun = normalizedClassName == "gun";
        var isKnife = normalizedClassName == "knife";
        var nowUtc = _timeProvider.GetUtcNow().UtcDateTime;

        await _dbContext.BranchDailyAlertQuotas
            .Where(q => q.BranchId == branchId && q.LocalDate == localDate)
            .ExecuteUpdateAsync(
                setters => setters
                    .SetProperty(q => q.SuppressedDetectionCount, q => q.SuppressedDetectionCount + 1)
                    .SetProperty(
                        q => q.GunSuppressedCount, q => isGun ? q.GunSuppressedCount + 1 : q.GunSuppressedCount)
                    .SetProperty(
                        q => q.KnifeSuppressedCount,
                        q => isKnife ? q.KnifeSuppressedCount + 1 : q.KnifeSuppressedCount)
                    .SetProperty(q => q.FirstSuppressedAtUtc, q => q.FirstSuppressedAtUtc ?? nowUtc)
                    .SetProperty(q => q.LastSuppressedAtUtc, nowUtc),
                cancellationToken);
    }

    // FS-09 §13: branch_daily_quota_reached (once), event_suppressed_by_quota (a bounded subset), and
    // daily_quota_summary (periodic) — all safe fields only (BranchId, local date, maximum, class
    // name), never credentials or bounding boxes. Throttled in-process (see SuppressionLogCounts);
    // never one unbounded log line per suppressed detection.
    private void LogSuppression(Guid branchId, string localDate, int maximum, string className)
    {
        var key = (branchId, localDate);
        var count = SuppressionLogCounts.AddOrUpdate(key, 1, static (_, existing) => existing + 1);

        if (count == 1)
        {
            _logger.LogWarning(
                "branch_daily_quota_reached BranchId={BranchId} LocalDate={LocalDate} Maximum={Maximum}",
                branchId, localDate, maximum);
        }

        if (count <= MaxIndividualSuppressionLogsPerBranchDay)
        {
            _logger.LogInformation(
                "event_suppressed_by_quota BranchId={BranchId} LocalDate={LocalDate} ClassName={ClassName}",
                branchId, localDate, className);
        }

        if (count % SummaryLogEveryNSuppressions == 0)
        {
            _logger.LogInformation(
                "daily_quota_summary BranchId={BranchId} LocalDate={LocalDate} Maximum={Maximum} SuppressedCount={SuppressedCount}",
                branchId, localDate, maximum, count);
        }
    }

    private Alert BuildAlert(Guid deviceId, Guid resolvedCameraId, DetectionEventSyncItem evt) =>
        new(
            deviceId,
            evt.EventId,
            resolvedCameraId,
            evt.DetectedAtUtc,
            _timeProvider.GetUtcNow().UtcDateTime,
            evt.ClassId,
            evt.ClassName,
            evt.Confidence,
            evt.FrameNumber,
            evt.FrameWidth,
            evt.FrameHeight,
            evt.BoundingBox.Left,
            evt.BoundingBox.Top,
            evt.BoundingBox.Width,
            evt.BoundingBox.Height);

    private async Task<SyncEventOutcome> ResolveDuplicateOrConflictAsync(
        Guid deviceId, DetectionEventSyncItem evt, Guid resolvedCameraId, CancellationToken cancellationToken)
    {
        var existing = await _dbContext.Alerts
            .AsNoTracking()
            .SingleOrDefaultAsync(
                a => a.DeviceId == deviceId && a.EventId == evt.EventId, cancellationToken);

        if (existing is null)
        {
            // The unique index fired but the row is gone by the time we re-query (should not happen
            // inside one serializable batch transaction) — surface as an unexpected failure rather
            // than guessing at an outcome.
            throw new InvalidOperationException(
                "A duplicate-key conflict was detected for a detection event, but the " +
                "conflicting Alert could not be re-read.");
        }

        return ImmutableFieldsMatch(existing, evt, resolvedCameraId)
            ? SyncEventOutcome.Duplicate(evt.EventId, existing.AlertId)
            : SyncEventOutcome.Rejected(evt.EventId, SyncEventErrorCodes.EventDataConflict);
    }

    // FS-09 §4: resolves the Branch's configured timezone, falling back to UTC — explicitly, not
    // silently — whenever the Branch has none or (defensively) carries an identifier the local .NET
    // timezone database cannot resolve.
    private async Task<TimeZoneInfo> ResolveBranchTimeZoneAsync(Guid branchId, CancellationToken cancellationToken)
    {
        var timeZoneId = await _dbContext.Branches
            .AsNoTracking()
            .Where(b => b.BranchId == branchId)
            .Select(b => b.TimeZoneId)
            .SingleOrDefaultAsync(cancellationToken);

        if (string.IsNullOrWhiteSpace(timeZoneId))
        {
            return TimeZoneInfo.Utc;
        }

        try
        {
            return TimeZoneInfo.FindSystemTimeZoneById(timeZoneId);
        }
        catch (Exception ex) when (ex is TimeZoneNotFoundException or InvalidTimeZoneException)
        {
            return TimeZoneInfo.Utc;
        }
    }

    // FS-09 §4: the quota day boundary is [branch-local 00:00:00, next branch-local 00:00:00),
    // resolved from DetectedAtUtc (the original Jetson detection timestamp, never ReceivedAtUtc/upload
    // time — consistent with FS-06 §4.3's timestamp-provenance rule) and converted via a proper
    // timezone identifier so DST transitions are handled by the .NET timezone database, never a fixed
    // UTC offset.
    private static string ResolveLocalDate(DateTime detectedAtUtc, TimeZoneInfo timeZone)
    {
        var utc = DateTime.SpecifyKind(detectedAtUtc, DateTimeKind.Utc);
        var local = TimeZoneInfo.ConvertTimeFromUtc(utc, timeZone);
        return local.ToString("yyyy-MM-dd");
    }

    private static bool IsConfidenceValid(double confidence) =>
        !double.IsNaN(confidence) && confidence is >= 0.0 and <= 1.0;

    // Non-negative frame/box dimensions and a box that fits within the reported frame (FS-06 §4.3:
    // "finite, non-negative, box within frame").
    private static bool IsFrameAndBoundingBoxValid(DetectionEventSyncItem evt)
    {
        if (evt.FrameNumber < 0 || evt.FrameWidth < 0 || evt.FrameHeight < 0)
        {
            return false;
        }

        var box = evt.BoundingBox;
        if (!double.IsFinite(box.Left) || !double.IsFinite(box.Top)
            || !double.IsFinite(box.Width) || !double.IsFinite(box.Height))
        {
            return false;
        }

        if (box.Left < 0 || box.Top < 0 || box.Width < 0 || box.Height < 0)
        {
            return false;
        }

        return box.Left + box.Width <= evt.FrameWidth && box.Top + box.Height <= evt.FrameHeight;
    }

    // FS-11 §10: the wire `cameraId` is now the immutable Camera.CameraId GUID (never Camera.Name),
    // scoped to the authenticated device's own Branch. A disabled Camera never matches — a disabled
    // camera is treated identically to an unknown one, so the Agent cannot distinguish "no such
    // camera" from "camera turned off". An unrelated Branch's Camera GUID is rejected the same way.
    //
    // Transitional legacy support (FS-11 §10): a wire value that does not parse as a GUID is still
    // resolved by the old case-insensitive Camera.Name match, so an already-deployed Agent (not yet
    // upgraded to send the immutable id) keeps working during the coordinated rollout. Each legacy
    // resolution logs a deprecation warning; this fallback is removed in a follow-up task once
    // telemetry shows zero legacy usage.
    private async Task<Guid?> ResolveCameraIdAsync(
        Guid branchId, string wireCameraId, CancellationToken cancellationToken)
    {
        var trimmed = wireCameraId?.Trim();
        if (string.IsNullOrEmpty(trimmed))
        {
            return null;
        }

        if (Guid.TryParse(trimmed, out var cameraId))
        {
            var camera = await _dbContext.Cameras
                .AsNoTracking()
                .Where(c => c.BranchId == branchId && c.Enabled && c.CameraId == cameraId)
                .Select(c => (Guid?)c.CameraId)
                .SingleOrDefaultAsync(cancellationToken);

            return camera;
        }

        _logger.LogWarning(
            "legacy_camera_name_resolution_used: a DetectionEvent's CameraId did not parse as a GUID "
                + "and was resolved via the deprecated Camera.Name match instead (FS-11 §10). Upgrade "
                + "the originating Agent to send the immutable Camera.CameraId.");

        var camerasByName = await _dbContext.Cameras
            .AsNoTracking()
            .Where(c => c.BranchId == branchId && c.Enabled)
            .ToListAsync(cancellationToken);

        var match = camerasByName.FirstOrDefault(
            c => string.Equals(c.Name.Trim(), trimmed, StringComparison.OrdinalIgnoreCase));

        return match?.CameraId;
    }

    // Compares the retried request's immutable fields against the already-persisted Alert (FS-06
    // §5.2). Confidence is compared with a small epsilon: it round-trips through JSON as a double, and
    // a bit-for-bit comparison could reject a genuinely identical retry over floating-point noise.
    private static bool ImmutableFieldsMatch(Alert existing, DetectionEventSyncItem evt, Guid resolvedCameraId)
    {
        const double epsilon = 1e-9;

        return existing.CameraId == resolvedCameraId
            && existing.DetectedAtUtc == evt.DetectedAtUtc
            && existing.ClassId == evt.ClassId
            && string.Equals(existing.ClassName, evt.ClassName.Trim(), StringComparison.Ordinal)
            && Math.Abs(existing.Confidence - evt.Confidence) < epsilon
            && existing.FrameNumber == evt.FrameNumber
            && existing.FrameWidth == evt.FrameWidth
            && existing.FrameHeight == evt.FrameHeight
            && Math.Abs(existing.BboxLeft - evt.BoundingBox.Left) < epsilon
            && Math.Abs(existing.BboxTop - evt.BoundingBox.Top) < epsilon
            && Math.Abs(existing.BboxWidth - evt.BoundingBox.Width) < epsilon
            && Math.Abs(existing.BboxHeight - evt.BoundingBox.Height) < epsilon;
    }

    // True only for the named unique-index/primary-key violation supplied — never for an unrelated
    // DbUpdateException, which must propagate as an unexpected failure (mirrors DeviceService's
    // IsUnconsumedKeyUniqueIndexViolation). SQL Server raises error 2601 (duplicate key in a unique
    // index) or 2627 (unique/primary-key constraint); the constraint/index name pins it to the specific
    // one being checked.
    private static bool IsUniqueIndexViolation(Exception exception, string constraintOrIndexName)
    {
        for (var current = exception; current is not null; current = current.InnerException)
        {
            if (current is SqlException sqlException
                && sqlException.Number is 2601 or 2627
                && sqlException.Message.Contains(constraintOrIndexName, StringComparison.Ordinal))
            {
                return true;
            }
        }

        return false;
    }
}

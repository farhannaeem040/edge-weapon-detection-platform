namespace WeaponDetection.Infrastructure.Services;

// Shared branch-local-day conversion helpers (FS-09 §4, FS-10 §6). New code only — AlertSyncService's
// own private ResolveBranchTimeZoneAsync/ResolveLocalDate (already deployed and extensively tested,
// FS-09/IP-11) are deliberately left untouched rather than refactored onto this type, to avoid
// introducing risk into that already-proven, concurrency-critical path. DashboardSummaryService
// (read-only, IP-12) uses this utility so its resolution is provably identical in technique to the
// Backend's own quota enforcement, without editing that file.
public static class BranchLocalDateResolver
{
    // FS-09 §4: resolves the Branch's configured timezone, falling back to UTC — explicitly, not
    // silently — whenever the Branch has none or carries an identifier the local .NET timezone
    // database cannot resolve.
    public static TimeZoneInfo ResolveTimeZone(string? timeZoneId)
    {
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
    // converted via a proper timezone identifier so DST transitions are handled by the .NET timezone
    // database, never a fixed UTC offset. Identical technique to AlertSyncService.ResolveLocalDate.
    public static string ResolveLocalDate(DateTime utcTimestamp, TimeZoneInfo timeZone)
    {
        var utc = DateTime.SpecifyKind(utcTimestamp, DateTimeKind.Utc);
        var local = TimeZoneInfo.ConvertTimeFromUtc(utc, timeZone);
        return local.ToString("yyyy-MM-dd");
    }

    // FS-10 §6/§9.1: the next Branch-local midnight after `nowUtc`, converted back to UTC — the
    // dashboard's "next reset" timestamp. Read-only/presentational: never used to gate or mutate quota
    // enforcement, which remains solely AlertSyncService's responsibility.
    public static DateTime ResolveNextResetUtc(DateTime nowUtc, TimeZoneInfo timeZone)
    {
        var utc = DateTime.SpecifyKind(nowUtc, DateTimeKind.Utc);
        var localNow = TimeZoneInfo.ConvertTimeFromUtc(utc, timeZone);
        var nextLocalMidnight = DateTime.SpecifyKind(localNow.Date.AddDays(1), DateTimeKind.Unspecified);
        return TimeZoneInfo.ConvertTimeToUtc(nextLocalMidnight, timeZone);
    }
}

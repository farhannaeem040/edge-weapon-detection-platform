namespace WeaponDetection.Domain;

// The atomic per-Branch/day Alert counter (FS-09 §6.1). One row per (BranchId, LocalDate) — that
// composite is the entity's unique key and the sole concurrency-safety mechanism: AlertSyncService
// consumes quota with a single conditional UPDATE against this row (never a separate SELECT COUNT
// followed by an INSERT), so concurrent requests for the same Branch/day serialize on the row lock
// SQL Server takes for the UPDATE's predicate-evaluate-then-write, rather than on any application-level
// lock (FS-09 §7).
//
// No FK to Branch is declared, mirroring Alert's "no FK, index only" style (FS-06 §5.1) — a quota row
// is deliberately independent of Branch mutation.
public class BranchDailyAlertQuota
{
    public const int LocalDateLength = 10; // "yyyy-MM-dd"

    public Guid BranchId { get; private set; }
    public string LocalDate { get; private set; }
    public int AcceptedAlertCount { get; private set; }
    public int SuppressedDetectionCount { get; private set; }
    public int GunSuppressedCount { get; private set; }
    public int KnifeSuppressedCount { get; private set; }
    public DateTime? FirstSuppressedAtUtc { get; private set; }
    public DateTime? LastSuppressedAtUtc { get; private set; }

    // Required by EF Core for materialization; never used by application code.
    private BranchDailyAlertQuota()
    {
        LocalDate = null!;
    }

    // Constructed with AcceptedAlertCount=0 — AlertSyncService inserts this row (insert-if-absent,
    // catching the unique-index violation exactly as Alert's duplicate handling does, FS-09 §7 step
    // 4a) strictly before ever attempting the conditional increment. No factory takes a non-zero
    // starting count; every count only ever moves via the atomic SQL statements in §7, never via this
    // constructor or any other in-process mutation.
    public BranchDailyAlertQuota(Guid branchId, string localDate)
    {
        if (branchId == Guid.Empty)
        {
            throw new ArgumentException("Branch id is required.", nameof(branchId));
        }

        if (string.IsNullOrWhiteSpace(localDate) || localDate.Length != LocalDateLength)
        {
            throw new ArgumentException(
                $"Local date must be a {LocalDateLength}-character yyyy-MM-dd value.", nameof(localDate));
        }

        BranchId = branchId;
        LocalDate = localDate;
        AcceptedAlertCount = 0;
        SuppressedDetectionCount = 0;
        GunSuppressedCount = 0;
        KnifeSuppressedCount = 0;
        FirstSuppressedAtUtc = null;
        LastSuppressedAtUtc = null;
    }
}

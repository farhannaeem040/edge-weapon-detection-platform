namespace WeaponDetection.Domain;

// The idempotency record for a quota-suppressed detection (FS-09 §6.2). Unique on (DeviceId, EventId)
// — the same "unique index is the sole concurrency/idempotency authority" style as Alert's
// (DeviceId, EventId) index (FS-06 §5.1/§5.2): AlertSyncService looks this row up before ever running
// the atomic quota-counter update, so a retried quota-suppressed EventId returns quota_exceeded again
// without incrementing BranchDailyAlertQuota's suppression counters a second time (FS-09 §7 step 3).
//
// Deliberately minimal — no bounding box or snapshot evidence is stored here (FS-09 §6.2, task
// requirement).
public class SuppressedDetectionEvent
{
    public const string BranchDailyAlertQuotaReachedReason = "BRANCH_DAILY_ALERT_QUOTA_REACHED";
    public const int ClassNameMaxLength = 200;
    public const int ReasonMaxLength = 100;
    public const int LocalDateLength = 10; // "yyyy-MM-dd"

    public Guid DeviceId { get; private set; }
    public Guid EventId { get; private set; }
    public Guid BranchId { get; private set; }
    public string LocalDate { get; private set; }
    public string ClassName { get; private set; }
    public DateTime DetectedAtUtc { get; private set; }
    public string Reason { get; private set; }
    public DateTime CreatedAtUtc { get; private set; }

    // Required by EF Core for materialization; never used by application code.
    private SuppressedDetectionEvent()
    {
        LocalDate = null!;
        ClassName = null!;
        Reason = null!;
    }

    public SuppressedDetectionEvent(
        Guid deviceId,
        Guid eventId,
        Guid branchId,
        string localDate,
        string className,
        DateTime detectedAtUtc,
        string reason,
        DateTime createdAtUtc)
    {
        if (deviceId == Guid.Empty)
        {
            throw new ArgumentException("Device id is required.", nameof(deviceId));
        }

        if (eventId == Guid.Empty)
        {
            throw new ArgumentException("Event id is required.", nameof(eventId));
        }

        if (branchId == Guid.Empty)
        {
            throw new ArgumentException("Branch id is required.", nameof(branchId));
        }

        if (string.IsNullOrWhiteSpace(localDate) || localDate.Length != LocalDateLength)
        {
            throw new ArgumentException(
                $"Local date must be a {LocalDateLength}-character yyyy-MM-dd value.", nameof(localDate));
        }

        if (string.IsNullOrWhiteSpace(className))
        {
            throw new ArgumentException("Class name is required.", nameof(className));
        }

        var trimmedClassName = className.Trim();
        if (trimmedClassName.Length > ClassNameMaxLength)
        {
            throw new ArgumentException(
                $"Class name must not exceed {ClassNameMaxLength} characters.", nameof(className));
        }

        if (string.IsNullOrWhiteSpace(reason))
        {
            throw new ArgumentException("Reason is required.", nameof(reason));
        }

        var trimmedReason = reason.Trim();
        if (trimmedReason.Length > ReasonMaxLength)
        {
            throw new ArgumentException(
                $"Reason must not exceed {ReasonMaxLength} characters.", nameof(reason));
        }

        DeviceId = deviceId;
        EventId = eventId;
        BranchId = branchId;
        LocalDate = localDate;
        ClassName = trimmedClassName;
        DetectedAtUtc = detectedAtUtc;
        Reason = trimmedReason;
        CreatedAtUtc = createdAtUtc;
    }
}

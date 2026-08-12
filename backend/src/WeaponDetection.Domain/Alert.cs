namespace WeaponDetection.Domain;

// The SQL Server row created when a Jetson-generated detection event is durably synced to the
// Backend (FS-06 §5.1). Mirrors the Device/Camera entity style: private setters, a constructor
// enforcing invariants, no public mutation beyond a defined transition — and this increment defines
// none, so Status has no public mutator at all yet (FS-06 §5.3: only `New` is ever assigned).
//
// DeviceId here is the authenticated device's *external*, persistent identifier (Device.DeviceId),
// never the internal DeviceRecordId — the same distinction Device.cs documents. CameraId is the
// already-resolved Camera.CameraId GUID (FS-06 §6.3); this entity never carries the Agent's local
// wire-format camera string.
//
// DetectedAtUtc is the original detection timestamp and is preserved exactly as submitted
// (FR-SYN-004) — it is never overwritten by ReceivedAtUtc, which is the server's own UtcNow at
// persistence and serves a different purpose (delivery-time bookkeeping, not detection time).
public class Alert
{
    public const int ClassNameMaxLength = 200;
    public const int SnapshotReferenceMaxLength = 2048;
    public const int SnapshotContentTypeMaxLength = 100;

    // A lower-case hex-encoded SHA-256 digest is always exactly 64 characters (FS-08 §8/§9) —
    // enforced here so a malformed value can never reach the database, regardless of caller.
    public const int SnapshotSha256Length = 64;

    public Guid AlertId { get; private set; }
    public Guid DeviceId { get; private set; }
    public Guid EventId { get; private set; }
    public Guid CameraId { get; private set; }
    public DateTime DetectedAtUtc { get; private set; }
    public DateTime ReceivedAtUtc { get; private set; }
    public int ClassId { get; private set; }
    public string ClassName { get; private set; }
    public double Confidence { get; private set; }
    public long FrameNumber { get; private set; }
    public int FrameWidth { get; private set; }
    public int FrameHeight { get; private set; }
    public double BboxLeft { get; private set; }
    public double BboxTop { get; private set; }
    public double BboxWidth { get; private set; }
    public double BboxHeight { get; private set; }
    public string? SnapshotReference { get; private set; }

    // FS-08 §10: additive, nullable-only alongside SnapshotReference. Populated together, exactly
    // once, by AttachSnapshot below — never set individually, and never by the constructor (a newly
    // synced Alert from AlertSyncService never carries a snapshot yet).
    public string? SnapshotSha256 { get; private set; }
    public string? SnapshotContentType { get; private set; }
    public long? SnapshotSizeBytes { get; private set; }
    public DateTime? SnapshotReceivedAtUtc { get; private set; }

    public AlertStatus Status { get; private set; }

    // Required by EF Core for materialization; never used by application code.
    private Alert()
    {
        ClassName = null!;
    }

    public Alert(
        Guid deviceId,
        Guid eventId,
        Guid cameraId,
        DateTime detectedAtUtc,
        DateTime receivedAtUtc,
        int classId,
        string className,
        double confidence,
        long frameNumber,
        int frameWidth,
        int frameHeight,
        double bboxLeft,
        double bboxTop,
        double bboxWidth,
        double bboxHeight,
        string? snapshotReference = null)
    {
        if (deviceId == Guid.Empty)
        {
            throw new ArgumentException("Device id is required.", nameof(deviceId));
        }

        if (eventId == Guid.Empty)
        {
            throw new ArgumentException("Event id is required.", nameof(eventId));
        }

        if (cameraId == Guid.Empty)
        {
            throw new ArgumentException("Camera id is required.", nameof(cameraId));
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

        // Confidence bounds (FS-06 §4.3): 0.0 <= value <= 1.0. Only the limit is named — these
        // values are not sensitive, but the message stays formulaic to mirror the rest of the model.
        if (double.IsNaN(confidence) || confidence < 0.0 || confidence > 1.0)
        {
            throw new ArgumentException(
                "Confidence must be between 0.0 and 1.0 inclusive.", nameof(confidence));
        }

        if (frameNumber < 0)
        {
            throw new ArgumentException("Frame number must not be negative.", nameof(frameNumber));
        }

        if (frameWidth < 0)
        {
            throw new ArgumentException("Frame width must not be negative.", nameof(frameWidth));
        }

        if (frameHeight < 0)
        {
            throw new ArgumentException("Frame height must not be negative.", nameof(frameHeight));
        }

        if (bboxLeft < 0)
        {
            throw new ArgumentException(
                "Bounding box left must not be negative.", nameof(bboxLeft));
        }

        if (bboxTop < 0)
        {
            throw new ArgumentException(
                "Bounding box top must not be negative.", nameof(bboxTop));
        }

        if (bboxWidth < 0)
        {
            throw new ArgumentException(
                "Bounding box width must not be negative.", nameof(bboxWidth));
        }

        if (bboxHeight < 0)
        {
            throw new ArgumentException(
                "Bounding box height must not be negative.", nameof(bboxHeight));
        }

        if (snapshotReference is not null && snapshotReference.Length > SnapshotReferenceMaxLength)
        {
            throw new ArgumentException(
                $"Snapshot reference must not exceed {SnapshotReferenceMaxLength} characters.",
                nameof(snapshotReference));
        }

        AlertId = Guid.NewGuid();
        DeviceId = deviceId;
        EventId = eventId;
        CameraId = cameraId;
        DetectedAtUtc = detectedAtUtc;
        ReceivedAtUtc = receivedAtUtc;
        ClassId = classId;
        ClassName = trimmedClassName;
        Confidence = confidence;
        FrameNumber = frameNumber;
        FrameWidth = frameWidth;
        FrameHeight = frameHeight;
        BboxLeft = bboxLeft;
        BboxTop = bboxTop;
        BboxWidth = bboxWidth;
        BboxHeight = bboxHeight;
        SnapshotReference = snapshotReference;

        // Every newly created Alert starts New (FS-06 §5.3) — no caller may choose otherwise at
        // construction, and no lifecycle transition is defined in this increment.
        Status = AlertStatus.New;
    }

    // FS-08 §9/§10: the sole way a snapshot ever becomes attached to an Alert. The service layer
    // (AlertSnapshotUploadService) has already resolved ownership (Alert belongs to the
    // authenticated Device's Branch) and stored the bytes via IAlertSnapshotStorage before calling
    // this — this method owns only the "first write wins, identical retry is a no-op, differing
    // bytes are rejected" invariant, mirroring how Device.Activate/RequireReactivation own their own
    // transitions rather than leaving the caller to enforce them.
    //
    // snapshotReference is the opaque key returned to the Agent (never a filesystem path, FS-08
    // §8). sha256 is the server-recomputed digest (FS-08 §9) — the caller must never pass the
    // client-supplied value here, since this method's duplicate/conflict decision is the actual
    // integrity check.
    //
    // Duplicate and Conflict are both expected, non-exceptional outcomes (an Agent retry after a
    // dropped response is the normal case for Duplicate; a client bug or tamper attempt is the
    // normal case for Conflict) — returned as a typed result, not thrown, mirroring
    // SyncEventOutcome's "safe outcomes are typed and returned" discipline rather than Device's
    // "caller-bug transitions throw" discipline, since neither Duplicate nor Conflict is a caller
    // bug here.
    public AttachSnapshotOutcome AttachSnapshot(
        string snapshotReference, string sha256, string contentType, long sizeBytes, DateTime receivedAtUtc)
    {
        if (string.IsNullOrWhiteSpace(snapshotReference))
        {
            throw new ArgumentException("Snapshot reference is required.", nameof(snapshotReference));
        }

        if (snapshotReference.Length > SnapshotReferenceMaxLength)
        {
            throw new ArgumentException(
                $"Snapshot reference must not exceed {SnapshotReferenceMaxLength} characters.",
                nameof(snapshotReference));
        }

        if (string.IsNullOrWhiteSpace(sha256) || sha256.Length != SnapshotSha256Length)
        {
            throw new ArgumentException(
                $"Snapshot SHA-256 must be a {SnapshotSha256Length}-character hex digest.", nameof(sha256));
        }

        if (string.IsNullOrWhiteSpace(contentType))
        {
            throw new ArgumentException("Snapshot content type is required.", nameof(contentType));
        }

        if (contentType.Length > SnapshotContentTypeMaxLength)
        {
            throw new ArgumentException(
                $"Snapshot content type must not exceed {SnapshotContentTypeMaxLength} characters.",
                nameof(contentType));
        }

        if (sizeBytes <= 0)
        {
            throw new ArgumentException("Snapshot size must be positive.", nameof(sizeBytes));
        }

        if (SnapshotReference is null)
        {
            SnapshotReference = snapshotReference;
            SnapshotSha256 = sha256;
            SnapshotContentType = contentType;
            SnapshotSizeBytes = sizeBytes;
            SnapshotReceivedAtUtc = receivedAtUtc;
            return AttachSnapshotOutcome.Attached;
        }

        // Identical bytes (server-recomputed digest matches what is already stored) — an idempotent
        // re-upload, e.g. the Agent retrying after losing the first response. No field is touched:
        // the original ReceivedAtUtc/reference are preserved, never silently overwritten (FS-08 §9).
        if (string.Equals(SnapshotSha256, sha256, StringComparison.OrdinalIgnoreCase))
        {
            return AttachSnapshotOutcome.Duplicate;
        }

        // Different bytes for an Alert that already has a snapshot — reject, preserve the existing
        // one, never silently overwrite (FS-08 §9, mirroring FS-06 §5.2's EVENT_DATA_CONFLICT
        // precedent).
        return AttachSnapshotOutcome.Conflict;
    }
}

// The typed result of Alert.AttachSnapshot (FS-08 §9/§10). Attached and Duplicate both leave the
// Alert in a state where SnapshotReference is set to the caller's value (Attached) or was already
// set to that same logical snapshot (Duplicate); Conflict leaves the Alert entirely unchanged.
public enum AttachSnapshotOutcome
{
    Attached,
    Duplicate,
    Conflict,
}

// The Alert's operator-facing lifecycle status. Only `New` is defined by this increment (FS-06 §1.2
// explicitly excludes operator alert-status transitions) — the enum exists now so the column and its
// storage convention (string, mirroring Device.ActivationStatus) do not need to change shape when a
// future feature adds transitions.
public enum AlertStatus
{
    New,
}

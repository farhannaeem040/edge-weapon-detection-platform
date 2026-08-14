namespace WeaponDetection.Application.Interfaces;

// The read counterpart to IAlertSnapshotUploadService for GET /api/v1/alerts/{alertId}/snapshot
// (FS-08 §12). Kept as its own thin interface rather than folding into
// IAlertSnapshotUploadService — that interface's own doc comment scopes it specifically to the
// upload pipeline (validation/storage/attach), and retrieval has none of that: it is a single
// lookup with no write path.
public interface IAlertSnapshotRetrievalService
{
    Task<SnapshotRetrievalOutcome> GetSnapshotAsync(Guid alertId, CancellationToken cancellationToken = default);
}

// Mirrors SnapshotUploadOutcome's typed-result shape. NotFound covers all three "no snapshot to
// serve" cases uniformly (Alert does not exist; Alert exists but has never had a snapshot
// attached; Alert.SnapshotReference is set but the file is missing from storage) — the controller
// never needs to distinguish them, and collapsing them avoids leaking which case applies to an
// unauthenticated/non-owning caller.
public enum SnapshotRetrievalOutcomeKind
{
    Found,
    NotFound,
}

public sealed record SnapshotRetrievalOutcome(SnapshotRetrievalOutcomeKind Kind, byte[]? Content, string? ContentType)
{
    public static SnapshotRetrievalOutcome Found(byte[] content, string contentType) =>
        new(SnapshotRetrievalOutcomeKind.Found, content, contentType);

    public static SnapshotRetrievalOutcome NotFound() =>
        new(SnapshotRetrievalOutcomeKind.NotFound, null, null);
}

namespace WeaponDetection.Application.Interfaces;

// FS-08 §8: the filesystem abstraction backing snapshot evidence storage. Mirrors
// IDeviceSecretProtector's shape (a narrow, storage-technology-agnostic contract; the production
// implementation is FileSystemAlertSnapshotStorage, backed by a Docker volume distinct from the
// Data Protection key ring's own volume). ADR-011: image bytes are never written to SQL Server —
// this is the only place JPEG bytes are persisted.
//
// Paths are always server-generated from AlertId only (`{AlertId}.jpg`) — no caller ever supplies a
// filename or path segment, which structurally rules out path traversal/symlink-following from any
// client-controlled field (FS-08 §8).
public interface IAlertSnapshotStorage
{
    // The opaque reference SaveAsync will use/return for the given Alert, computed without touching
    // the filesystem. Exposed so callers (AlertSnapshotUploadService) can decide the
    // attached/duplicate/conflict outcome — via Alert.AttachSnapshot — before performing the actual
    // write, so a Conflict decision is reached without ever writing bytes for the rejected upload
    // (FS-08 §9: "never silently overwrite").
    string BuildReference(Guid alertId);

    // Writes the snapshot for the given Alert atomically (temp file in the same directory, then
    // rename — FS-08 §8, mirroring the Agent-side spool discipline in FS-08 §5). Overwrites are the
    // caller's responsibility to prevent: this method itself does not check for an existing file —
    // AlertSnapshotUploadService only calls it after Alert.AttachSnapshot has already decided
    // Attached is the correct outcome. Returns the opaque SnapshotReference to store on the Alert.
    Task<string> SaveAsync(Guid alertId, Stream content, CancellationToken cancellationToken = default);

    // Reads back a previously stored snapshot's bytes. Returns null if no file exists for the given
    // Alert. Used only by tests proving durability across separate storage instances (mirrors
    // DataProtectionKeyPersistenceTests) — no production endpoint currently serves snapshot bytes
    // back out (FS-08 excludes an Angular UI/read endpoint from this increment).
    Task<byte[]?> ReadAsync(Guid alertId, CancellationToken cancellationToken = default);

    // True if a snapshot file already exists for the given Alert. Used defensively by the upload
    // service to detect a storage/database inconsistency (Alert.SnapshotReference set but no file,
    // or vice versa) without reading the full file into memory.
    Task<bool> ExistsAsync(Guid alertId, CancellationToken cancellationToken = default);
}

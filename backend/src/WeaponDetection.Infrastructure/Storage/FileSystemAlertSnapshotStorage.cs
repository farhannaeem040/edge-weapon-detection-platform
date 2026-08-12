using Microsoft.Extensions.Options;
using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.Infrastructure.Storage;

// FS-08 §8: the production IAlertSnapshotStorage implementation, backed by a Docker volume separate
// from the Data Protection key ring (compose.yaml's new `alert-snapshots` volume, mounted only into
// the Backend). Paths are always server-generated from AlertId only (`{AlertId}.jpg`) — no caller
// ever supplies a filename, which structurally rules out path traversal/symlink-following from any
// client-controlled field.
//
// Write discipline mirrors the Agent-side spool contract (FS-08 §5): a temp file in the same
// directory, flushed and fsync'd, then an atomic rename to the final `{AlertId}.jpg` — a reader can
// never observe a partially written file under its final name.
public class FileSystemAlertSnapshotStorage : IAlertSnapshotStorage
{
    private readonly string _storagePath;

    public FileSystemAlertSnapshotStorage(IOptions<AlertSnapshotStorageOptions> options)
    {
        var storagePath = options.Value.StoragePath;
        if (string.IsNullOrWhiteSpace(storagePath))
        {
            throw new InvalidOperationException(
                "The 'AlertSnapshots:StoragePath' configuration value is required but was not set.");
        }

        _storagePath = storagePath;
    }

    public string BuildReference(Guid alertId) => $"{alertId:D}.jpg";

    public async Task<string> SaveAsync(Guid alertId, Stream content, CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(content);

        var reference = BuildReference(alertId);
        var finalPath = ResolvePath(reference);
        var tempPath = Path.Combine(_storagePath, $".{alertId:N}.{Guid.NewGuid():N}.tmp");

        Directory.CreateDirectory(_storagePath);

        try
        {
            await using (var tempFile = new FileStream(
                tempPath, FileMode.CreateNew, FileAccess.Write, FileShare.None, bufferSize: 81920, useAsync: true))
            {
                await content.CopyToAsync(tempFile, cancellationToken);
                await tempFile.FlushAsync(cancellationToken);
                tempFile.Flush(flushToDisk: true);
            }

            // Atomic on the same volume (the temp file is created alongside the final path above) —
            // a reader can never observe a partially written file under its final name.
            File.Move(tempPath, finalPath, overwrite: true);
        }
        finally
        {
            // Best-effort cleanup if the move above never happened (e.g. an exception before it) —
            // never leaves a stray temp file behind on a failed write.
            if (File.Exists(tempPath))
            {
                File.Delete(tempPath);
            }
        }

        return reference;
    }

    public async Task<byte[]?> ReadAsync(Guid alertId, CancellationToken cancellationToken = default)
    {
        var path = ResolvePath(BuildReference(alertId));
        if (!File.Exists(path))
        {
            return null;
        }

        return await File.ReadAllBytesAsync(path, cancellationToken);
    }

    public Task<bool> ExistsAsync(Guid alertId, CancellationToken cancellationToken = default)
    {
        var path = ResolvePath(BuildReference(alertId));
        return Task.FromResult(File.Exists(path));
    }

    private string ResolvePath(string reference) => Path.Combine(_storagePath, reference);
}

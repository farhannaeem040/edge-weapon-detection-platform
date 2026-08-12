using Microsoft.Extensions.Options;
using WeaponDetection.Infrastructure.Storage;
using Xunit;

namespace WeaponDetection.UnitTests.Storage;

// FS-08 §8: real filesystem I/O against a temp directory (no SQL Server needed) — the closest
// in-process proxy for "does a stored snapshot survive being read back by a fresh instance pointed
// at the same directory", mirroring DataProtectionKeyPersistenceTests' rationale for the Data
// Protection volume. The real container-recreation proof is a separate, later deployment step.
public class FileSystemAlertSnapshotStorageTests : IDisposable
{
    private readonly string _storagePath;

    public FileSystemAlertSnapshotStorageTests()
    {
        _storagePath = Path.Combine(Path.GetTempPath(), "wd-alert-snapshots-storage-test-" + Guid.NewGuid().ToString("N"));
    }

    public void Dispose()
    {
        if (Directory.Exists(_storagePath))
        {
            Directory.Delete(_storagePath, recursive: true);
        }
    }

    private FileSystemAlertSnapshotStorage CreateStorage() =>
        new(Options.Create(new AlertSnapshotStorageOptions { StoragePath = _storagePath }));

    [Fact]
    public void Constructor_NoStoragePathConfigured_Throws()
    {
        Assert.Throws<InvalidOperationException>(
            () => new FileSystemAlertSnapshotStorage(Options.Create(new AlertSnapshotStorageOptions())));
    }

    [Fact]
    public void BuildReference_IsDeterministic_DerivedFromAlertIdOnly()
    {
        var storage = CreateStorage();
        var alertId = Guid.NewGuid();

        var first = storage.BuildReference(alertId);
        var second = storage.BuildReference(alertId);

        Assert.Equal(first, second);
        Assert.Equal($"{alertId:D}.jpg", first);
        Assert.DoesNotContain(_storagePath, first);
    }

    [Fact]
    public async Task SaveAsync_ThenReadAsync_RoundTripsTheExactBytes()
    {
        var storage = CreateStorage();
        var alertId = Guid.NewGuid();
        var content = new byte[] { 0xFF, 0xD8, 1, 2, 3, 0xFF, 0xD9 };

        await storage.SaveAsync(alertId, new MemoryStream(content));
        var readBack = await storage.ReadAsync(alertId);

        Assert.Equal(content, readBack);
    }

    [Fact]
    public async Task SaveAsync_ThenReadFromAFreshStorageInstanceSharingTheSameDirectory_Succeeds()
    {
        // Mirrors DataProtectionKeyPersistenceTests' "instance A writes, instance B (a brand-new
        // object with no shared in-memory state) reads" shape — the closest in-process proxy for
        // surviving a container recreation.
        var alertId = Guid.NewGuid();
        var content = new byte[] { 0xFF, 0xD8, 9, 9, 9, 0xFF, 0xD9 };

        await CreateStorage().SaveAsync(alertId, new MemoryStream(content));
        var readBack = await CreateStorage().ReadAsync(alertId);

        Assert.Equal(content, readBack);
    }

    [Fact]
    public async Task ExistsAsync_NoFileWritten_ReturnsFalse()
    {
        var storage = CreateStorage();

        Assert.False(await storage.ExistsAsync(Guid.NewGuid()));
    }

    [Fact]
    public async Task ExistsAsync_AfterSaveAsync_ReturnsTrue()
    {
        var storage = CreateStorage();
        var alertId = Guid.NewGuid();

        await storage.SaveAsync(alertId, new MemoryStream([0xFF, 0xD8, 0xFF, 0xD9]));

        Assert.True(await storage.ExistsAsync(alertId));
    }

    [Fact]
    public async Task ReadAsync_NoFileWritten_ReturnsNull()
    {
        var storage = CreateStorage();

        Assert.Null(await storage.ReadAsync(Guid.NewGuid()));
    }

    [Fact]
    public async Task SaveAsync_NeverLeavesATempFileBehindInTheStorageDirectory()
    {
        var storage = CreateStorage();
        var alertId = Guid.NewGuid();

        await storage.SaveAsync(alertId, new MemoryStream([0xFF, 0xD8, 0xFF, 0xD9]));

        var files = Directory.GetFiles(_storagePath);
        Assert.Single(files);
        Assert.Equal($"{alertId:D}.jpg", Path.GetFileName(files[0]));
    }

    [Fact]
    public async Task SaveAsync_CalledTwiceForTheSameAlertId_OverwritesRatherThanCreatingASecondFile()
    {
        // FileSystemAlertSnapshotStorage itself performs no duplicate/conflict check — that is
        // AlertSnapshotUploadService's job (via Alert.AttachSnapshot, before this is ever called for
        // a second time on the same Alert). This test only proves the deterministic-path mechanism
        // that makes "no second file" possible at all.
        var storage = CreateStorage();
        var alertId = Guid.NewGuid();

        await storage.SaveAsync(alertId, new MemoryStream([0xFF, 0xD8, 1, 0xFF, 0xD9]));
        await storage.SaveAsync(alertId, new MemoryStream([0xFF, 0xD8, 2, 0xFF, 0xD9]));

        Assert.Single(Directory.GetFiles(_storagePath));
        var readBack = await storage.ReadAsync(alertId);
        Assert.Equal(new byte[] { 0xFF, 0xD8, 2, 0xFF, 0xD9 }, readBack);
    }
}

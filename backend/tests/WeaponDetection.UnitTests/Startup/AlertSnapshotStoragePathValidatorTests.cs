using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using WeaponDetection.Infrastructure.Startup;
using WeaponDetection.Infrastructure.Storage;
using Xunit;

namespace WeaponDetection.UnitTests.Startup;

// FS-08 §8, mirroring DataProtectionKeyPathValidatorTests (FS-07 §3.3/T-117) exactly: the
// I/O-performing half of storage-path validation, run once at startup.
public class AlertSnapshotStoragePathValidatorTests : IDisposable
{
    private readonly List<string> _createdDirectories = [];

    public void Dispose()
    {
        foreach (var directory in _createdDirectories)
        {
            if (Directory.Exists(directory))
            {
                Directory.Delete(directory, recursive: true);
            }
        }
    }

    private string NewTempDirectoryPath()
    {
        var path = Path.Combine(Path.GetTempPath(), "wd-alert-snapshots-path-test-" + Guid.NewGuid().ToString("N"));
        _createdDirectories.Add(path);
        return path;
    }

    private static AlertSnapshotStoragePathValidator CreateValidator(string? storagePath) =>
        new(
            Options.Create(new AlertSnapshotStorageOptions { StoragePath = storagePath }),
            NullLogger<AlertSnapshotStoragePathValidator>.Instance);

    [Fact]
    public void Validate_NoStoragePathConfigured_Throws()
    {
        var validator = CreateValidator(storagePath: null);

        Assert.Throws<InvalidOperationException>(() => validator.Validate());
    }

    [Fact]
    public void Validate_DirectoryDoesNotExist_CreatesIt()
    {
        var path = NewTempDirectoryPath();
        var validator = CreateValidator(path);

        validator.Validate();

        Assert.True(Directory.Exists(path));
    }

    [Fact]
    public void Validate_ExistingWritableDirectory_Succeeds()
    {
        var path = NewTempDirectoryPath();
        Directory.CreateDirectory(path);
        var validator = CreateValidator(path);

        var exception = Record.Exception(() => validator.Validate());

        Assert.Null(exception);
    }

    [Fact]
    public void Validate_NeverLeavesAWriteProbeFileBehind()
    {
        var path = NewTempDirectoryPath();
        var validator = CreateValidator(path);

        validator.Validate();

        Assert.Empty(Directory.GetFiles(path));
    }

    [Fact]
    public void Validate_UnwritablePath_ThrowsWithoutLeakingTheUnderlyingExceptionMessage()
    {
        // A file where a directory is expected can never be created/entered — a portable way to
        // force an IOException without relying on platform-specific permission APIs.
        var blockingFilePath = NewTempDirectoryPath();
        File.WriteAllText(blockingFilePath, string.Empty);
        var unusablePath = Path.Combine(blockingFilePath, "snapshots");
        var validator = CreateValidator(unusablePath);

        var exception = Assert.Throws<InvalidOperationException>(() => validator.Validate());

        Assert.Contains("AlertSnapshots:StoragePath", exception.Message);
        Assert.DoesNotContain(unusablePath, exception.Message);
    }
}

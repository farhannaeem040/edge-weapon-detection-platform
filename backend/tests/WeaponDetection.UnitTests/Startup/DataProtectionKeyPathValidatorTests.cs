using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using WeaponDetection.Infrastructure.Security;
using WeaponDetection.Infrastructure.Startup;
using Xunit;

namespace WeaponDetection.UnitTests.Startup;

// FS-07 §3.3/T-117: the I/O-performing half of key-path validation, run once at startup.
public class DataProtectionKeyPathValidatorTests : IDisposable
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
        var path = Path.Combine(Path.GetTempPath(), "wd-dp-keypath-test-" + Guid.NewGuid().ToString("N"));
        _createdDirectories.Add(path);
        return path;
    }

    private static DataProtectionKeyPathValidator CreateValidator(string? keyPath) =>
        new(
            Options.Create(new DeviceSecretDataProtectionOptions { KeyPath = keyPath }),
            NullLogger<DataProtectionKeyPathValidator>.Instance);

    [Fact]
    public void Validate_NoKeyPathConfigured_DoesNothing()
    {
        var validator = CreateValidator(keyPath: null);

        var exception = Record.Exception(() => validator.Validate());

        Assert.Null(exception);
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
        var unusablePath = Path.Combine(blockingFilePath, "keys");
        var validator = CreateValidator(unusablePath);

        var exception = Assert.Throws<InvalidOperationException>(() => validator.Validate());

        Assert.Contains("DataProtection:KeyPath", exception.Message);
        Assert.DoesNotContain(unusablePath, exception.Message);
    }
}

using System;
using WeaponDetection.Domain;
using Xunit;

namespace WeaponDetection.UnitTests.Domain;

// Camera invariants (FS-02 §9, ARCH-001 §13.1).
//
// Every RTSP value in this file is a non-routable placeholder (.invalid, reserved by RFC 2606) and
// every credential-shaped value is an obvious placeholder. No real camera address or credential
// appears in a committed test.
public class CameraTests
{
    private const string CameraName = "Entrance Camera";
    private const string RtspUrl = "rtsp://camera.example.invalid:554/stream1";

    [Fact]
    public void Constructor_ValidValues_SetsAllFields()
    {
        var branchId = Guid.NewGuid();

        var camera = new Camera(branchId, CameraName, RtspUrl, $"cam-{Guid.NewGuid():N}");

        Assert.Equal(branchId, camera.BranchId);
        Assert.Equal(CameraName, camera.Name);
        Assert.Equal(RtspUrl, camera.RtspUrl);
    }

    [Fact]
    public void Constructor_GeneratesAUniqueNonEmptyCameraId()
    {
        var branchId = Guid.NewGuid();

        var first = new Camera(branchId, CameraName, RtspUrl, $"cam-{Guid.NewGuid():N}");
        var second = new Camera(branchId, CameraName, RtspUrl, $"cam-{Guid.NewGuid():N}");

        Assert.NotEqual(Guid.Empty, first.CameraId);
        Assert.NotEqual(first.CameraId, second.CameraId);
    }

    [Fact]
    public void Constructor_DefaultsToEnabled()
    {
        // The approved inbound camera contract carries only a name and an RTSP URL (IP-01 §11),
        // so a newly configured camera is an enabled one.
        var camera = new Camera(Guid.NewGuid(), CameraName, RtspUrl, $"cam-{Guid.NewGuid():N}");

        Assert.True(camera.Enabled);
    }

    [Fact]
    public void Constructor_HonoursAnExplicitlyDisabledCamera()
    {
        var camera = new Camera(Guid.NewGuid(), CameraName, RtspUrl, $"cam-{Guid.NewGuid():N}", enabled: false);

        Assert.False(camera.Enabled);
    }

    [Fact]
    public void Constructor_EmptyBranchId_Throws()
    {
        Assert.Throws<ArgumentException>(() => new Camera(Guid.Empty, CameraName, RtspUrl, $"cam-{Guid.NewGuid():N}"));
    }

    // --- FS-11 §2: SourceOrder --------------------------------------------------------------------

    [Fact]
    public void Constructor_DefaultsSourceOrderToZero()
    {
        var camera = new Camera(Guid.NewGuid(), CameraName, RtspUrl, $"cam-{Guid.NewGuid():N}");

        Assert.Equal(0, camera.SourceOrder);
    }

    [Fact]
    public void Constructor_HonoursAnExplicitSourceOrder()
    {
        var camera = new Camera(Guid.NewGuid(), CameraName, RtspUrl, $"cam-{Guid.NewGuid():N}", sourceOrder: 3);

        Assert.Equal(3, camera.SourceOrder);
    }

    [Fact]
    public void Constructor_NegativeSourceOrder_Throws()
    {
        Assert.Throws<ArgumentException>(
            () => new Camera(Guid.NewGuid(), CameraName, RtspUrl, $"cam-{Guid.NewGuid():N}", sourceOrder: -1));
    }

    [Fact]
    public void UpdateConfiguration_NeverChangesSourceOrder()
    {
        // FS-11 §2: renaming/re-pointing a camera via the admin edit path must never touch its
        // pipeline-order assignment.
        var camera = new Camera(Guid.NewGuid(), CameraName, RtspUrl, $"cam-{Guid.NewGuid():N}", sourceOrder: 2);

        camera.UpdateConfiguration("Renamed Camera", "rtsp://camera.example.invalid:554/stream2");

        Assert.Equal(2, camera.SourceOrder);
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("   ")]
    public void Constructor_MissingOrBlankName_Throws(string? name)
    {
        Assert.Throws<ArgumentException>(() => new Camera(Guid.NewGuid(), name!, RtspUrl, $"cam-{Guid.NewGuid():N}"));
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("   ")]
    public void Constructor_MissingOrBlankRtspUrl_Throws(string? rtspUrl)
    {
        Assert.Throws<ArgumentException>(() => new Camera(Guid.NewGuid(), CameraName, rtspUrl!, $"cam-{Guid.NewGuid():N}"));
    }

    [Fact]
    public void Constructor_TrimsSurroundingWhitespace()
    {
        var camera = new Camera(Guid.NewGuid(), $"  {CameraName}  ", $"  {RtspUrl}  ", $"cam-{Guid.NewGuid():N}");

        Assert.Equal(CameraName, camera.Name);
        Assert.Equal(RtspUrl, camera.RtspUrl);
    }

    [Fact]
    public void Constructor_NameLongerThanTheMaximum_Throws()
    {
        var name = new string('n', Camera.NameMaxLength + 1);

        Assert.Throws<ArgumentException>(() => new Camera(Guid.NewGuid(), name, RtspUrl, $"cam-{Guid.NewGuid():N}"));
    }

    [Fact]
    public void Constructor_RtspUrlAtExactlyTheMaximumLength_IsAccepted()
    {
        var rtspUrl = BuildRtspUrlOfLength(Camera.RtspUrlMaxLength);

        var camera = new Camera(Guid.NewGuid(), CameraName, rtspUrl, $"cam-{Guid.NewGuid():N}");

        Assert.Equal(Camera.RtspUrlMaxLength, camera.RtspUrl.Length);
    }

    [Fact]
    public void Constructor_RtspUrlLongerThanTheMaximum_Throws()
    {
        var rtspUrl = BuildRtspUrlOfLength(Camera.RtspUrlMaxLength + 1);

        Assert.Throws<ArgumentException>(() => new Camera(Guid.NewGuid(), CameraName, rtspUrl, $"cam-{Guid.NewGuid():N}"));
    }

    [Fact]
    public void Constructor_OverlongRtspUrl_ExceptionNeverEchoesTheUrlOrItsEmbeddedCredentials()
    {
        // An RTSP URL can carry credentials inline (rtsp://user:pass@host/...). A validation
        // failure must not become the thing that writes them into a log or an error response, so
        // the exception states the limit and nothing else.
        const string user = "placeholder-user";
        const string password = "placeholder-password";
        var rtspUrl =
            $"rtsp://{user}:{password}@camera.example.invalid:554/" +
            new string('x', Camera.RtspUrlMaxLength);

        var exception = Assert.Throws<ArgumentException>(() =>
            new Camera(Guid.NewGuid(), CameraName, rtspUrl, $"cam-{Guid.NewGuid():N}"));

        var text = exception.ToString();

        Assert.DoesNotContain(rtspUrl, text);
        Assert.DoesNotContain(user, text);
        Assert.DoesNotContain(password, text);
        Assert.DoesNotContain("camera.example.invalid", text);
    }

    [Fact]
    public void Constructor_BlankRtspUrl_ExceptionDoesNotEchoTheInput()
    {
        var exception = Assert.Throws<ArgumentException>(() =>
            new Camera(Guid.NewGuid(), CameraName, "   ", $"cam-{Guid.NewGuid():N}"));

        Assert.DoesNotContain("rtsp://", exception.ToString());
    }

    // FS-03 §5.2/§5.3, AC-2: editing a camera changes its configurable fields and preserves its
    // identity and its branch association.
    [Fact]
    public void UpdateConfiguration_ValidValues_ReplacesNameAndUrlAndKeepsIdentity()
    {
        var branchId = Guid.NewGuid();
        var camera = new Camera(branchId, CameraName, RtspUrl, $"cam-{Guid.NewGuid():N}");
        var originalCameraId = camera.CameraId;

        camera.UpdateConfiguration("New Camera", "rtsp://camera.example.invalid:554/stream2");

        Assert.Equal("New Camera", camera.Name);
        Assert.Equal("rtsp://camera.example.invalid:554/stream2", camera.RtspUrl);
        Assert.Equal(originalCameraId, camera.CameraId);
        Assert.Equal(branchId, camera.BranchId);
    }

    [Fact]
    public void UpdateConfiguration_KeepsEnabledUnchanged()
    {
        var camera = new Camera(Guid.NewGuid(), CameraName, RtspUrl, $"cam-{Guid.NewGuid():N}");

        camera.UpdateConfiguration("New Camera", "rtsp://camera.example.invalid:554/stream2");

        Assert.True(camera.Enabled);
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    [InlineData(null)]
    public void UpdateConfiguration_BlankName_Throws(string? blank)
    {
        var camera = new Camera(Guid.NewGuid(), CameraName, RtspUrl, $"cam-{Guid.NewGuid():N}");

        Assert.Throws<ArgumentException>(() => camera.UpdateConfiguration(blank!, RtspUrl));
    }

    [Fact]
    public void UpdateConfiguration_BlankRtspUrl_ExceptionDoesNotEchoTheInput()
    {
        var camera = new Camera(Guid.NewGuid(), CameraName, RtspUrl, $"cam-{Guid.NewGuid():N}");

        var exception = Assert.Throws<ArgumentException>(() =>
            camera.UpdateConfiguration(CameraName, "   "));

        Assert.DoesNotContain("rtsp://", exception.ToString());
    }

    // --- FS-11 §11: derived per-camera output path -------------------------------------------------

    // FS-12 §2.1 — the mount is now derived from the administrator-defined CameraKey, not the GUID.
    [Fact]
    public void DeriveOutputPath_IsBuiltFromTheCameraKey()
    {
        Assert.Equal("cameras/front-entrance", Camera.DeriveOutputPath("front-entrance"));
    }

    [Fact]
    public void DeriveOutputPath_IsDeterministicForTheSameKey()
    {
        Assert.Equal(Camera.DeriveOutputPath("rear-door"), Camera.DeriveOutputPath("rear-door"));
    }

    [Fact]
    public void DeriveOutputPath_IsDistinctForDistinctKeys()
    {
        var paths = Enumerable.Range(0, 64)
            .Select(i => Camera.DeriveOutputPath($"cam-{i}"))
            .ToList();

        Assert.Equal(paths.Count, paths.Distinct().Count());
    }

    [Fact]
    public void DeriveOutputPath_IsUrlPathSafeAndRelative()
    {
        var path = Camera.DeriveOutputPath("front-entrance-2");

        Assert.DoesNotContain("..", path);
        Assert.DoesNotContain("://", path);
        Assert.DoesNotContain(@"\", path);
        Assert.DoesNotContain("?", path);
        Assert.DoesNotContain("#", path);
        Assert.False(path.StartsWith('/'));
        Assert.All(path, c => Assert.True(char.IsAsciiLetterOrDigit(c) || c is '-' or '/'));
    }

    // FS-12 §9 item 8: a rename, and a StreamUrl change, must both leave the public mount untouched.
    [Fact]
    public void DeriveOutputPath_IsUnaffectedByNameOrRtspUrl()
    {
        var camera = new Camera(Guid.NewGuid(), CameraName, RtspUrl, "front-entrance");
        var before = Camera.DeriveOutputPath(camera.CameraKey);

        camera.UpdateConfiguration("A Completely New Label", "rtsp://camera.example.invalid:554/moved");

        Assert.Equal("front-entrance", camera.CameraKey);
        Assert.Equal(before, Camera.DeriveOutputPath(camera.CameraKey));
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    [InlineData("ab")]                       // too short
    [InlineData("Front-Entrance")]           // uppercase is rejected, never normalised
    [InlineData("front entrance")]           // space
    [InlineData("front_entrance")]           // underscore
    [InlineData("front/entrance")]           // slash
    [InlineData(@"front\entrance")]          // backslash
    [InlineData("../etc")]                   // traversal
    [InlineData("-front")]                   // leading hyphen
    [InlineData("front-")]                   // trailing hyphen
    [InlineData("front?x=1")]                // query
    [InlineData("front#frag")]               // fragment
    [InlineData("front%2fentrance")]         // URL-encoding trick
    public void RequireCameraKey_RejectsInvalidKeys(string cameraKey)
    {
        Assert.Throws<ArgumentException>(() => Camera.RequireCameraKey(cameraKey));
    }

    [Theory]
    [InlineData("ds-test")]
    [InlineData("api")]
    [InlineData("admin")]
    [InlineData("health")]
    [InlineData("metrics")]
    [InlineData("cameras")]
    public void RequireCameraKey_RejectsReservedKeys(string cameraKey)
    {
        Assert.True(Camera.IsReservedCameraKey(cameraKey));
        Assert.Throws<ArgumentException>(() => Camera.RequireCameraKey(cameraKey));
    }

    [Theory]
    [InlineData("abc")]
    [InlineData("front-entrance")]
    [InlineData("cam1")]
    [InlineData("a1")]
    public void RequireCameraKey_AcceptsValidKeys(string cameraKey)
    {
        if (cameraKey.Length < Camera.CameraKeyMinLength)
        {
            Assert.Throws<ArgumentException>(() => Camera.RequireCameraKey(cameraKey));
            return;
        }

        Assert.Equal(cameraKey, Camera.RequireCameraKey(cameraKey));
    }

    [Fact]
    public void RequireCameraKey_RejectsKeyLongerThanTheMaximum()
    {
        var tooLong = new string('a', Camera.CameraKeyMaxLength + 1);

        Assert.Throws<ArgumentException>(() => Camera.RequireCameraKey(tooLong));
    }

    private static string BuildRtspUrlOfLength(int length)
    {
        const string prefix = "rtsp://camera.example.invalid:554/";
        return prefix + new string('s', length - prefix.Length);
    }
}

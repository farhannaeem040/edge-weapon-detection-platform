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

        var camera = new Camera(branchId, CameraName, RtspUrl);

        Assert.Equal(branchId, camera.BranchId);
        Assert.Equal(CameraName, camera.Name);
        Assert.Equal(RtspUrl, camera.RtspUrl);
    }

    [Fact]
    public void Constructor_GeneratesAUniqueNonEmptyCameraId()
    {
        var branchId = Guid.NewGuid();

        var first = new Camera(branchId, CameraName, RtspUrl);
        var second = new Camera(branchId, CameraName, RtspUrl);

        Assert.NotEqual(Guid.Empty, first.CameraId);
        Assert.NotEqual(first.CameraId, second.CameraId);
    }

    [Fact]
    public void Constructor_DefaultsToEnabled()
    {
        // The approved inbound camera contract carries only a name and an RTSP URL (IP-01 §11),
        // so a newly configured camera is an enabled one.
        var camera = new Camera(Guid.NewGuid(), CameraName, RtspUrl);

        Assert.True(camera.Enabled);
    }

    [Fact]
    public void Constructor_HonoursAnExplicitlyDisabledCamera()
    {
        var camera = new Camera(Guid.NewGuid(), CameraName, RtspUrl, enabled: false);

        Assert.False(camera.Enabled);
    }

    [Fact]
    public void Constructor_EmptyBranchId_Throws()
    {
        Assert.Throws<ArgumentException>(() => new Camera(Guid.Empty, CameraName, RtspUrl));
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("   ")]
    public void Constructor_MissingOrBlankName_Throws(string? name)
    {
        Assert.Throws<ArgumentException>(() => new Camera(Guid.NewGuid(), name!, RtspUrl));
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("   ")]
    public void Constructor_MissingOrBlankRtspUrl_Throws(string? rtspUrl)
    {
        Assert.Throws<ArgumentException>(() => new Camera(Guid.NewGuid(), CameraName, rtspUrl!));
    }

    [Fact]
    public void Constructor_TrimsSurroundingWhitespace()
    {
        var camera = new Camera(Guid.NewGuid(), $"  {CameraName}  ", $"  {RtspUrl}  ");

        Assert.Equal(CameraName, camera.Name);
        Assert.Equal(RtspUrl, camera.RtspUrl);
    }

    [Fact]
    public void Constructor_NameLongerThanTheMaximum_Throws()
    {
        var name = new string('n', Camera.NameMaxLength + 1);

        Assert.Throws<ArgumentException>(() => new Camera(Guid.NewGuid(), name, RtspUrl));
    }

    [Fact]
    public void Constructor_RtspUrlAtExactlyTheMaximumLength_IsAccepted()
    {
        var rtspUrl = BuildRtspUrlOfLength(Camera.RtspUrlMaxLength);

        var camera = new Camera(Guid.NewGuid(), CameraName, rtspUrl);

        Assert.Equal(Camera.RtspUrlMaxLength, camera.RtspUrl.Length);
    }

    [Fact]
    public void Constructor_RtspUrlLongerThanTheMaximum_Throws()
    {
        var rtspUrl = BuildRtspUrlOfLength(Camera.RtspUrlMaxLength + 1);

        Assert.Throws<ArgumentException>(() => new Camera(Guid.NewGuid(), CameraName, rtspUrl));
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
            new Camera(Guid.NewGuid(), CameraName, rtspUrl));

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
            new Camera(Guid.NewGuid(), CameraName, "   "));

        Assert.DoesNotContain("rtsp://", exception.ToString());
    }

    // FS-03 §5.2/§5.3, AC-2: editing a camera changes its configurable fields and preserves its
    // identity and its branch association.
    [Fact]
    public void UpdateConfiguration_ValidValues_ReplacesNameAndUrlAndKeepsIdentity()
    {
        var branchId = Guid.NewGuid();
        var camera = new Camera(branchId, CameraName, RtspUrl);
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
        var camera = new Camera(Guid.NewGuid(), CameraName, RtspUrl);

        camera.UpdateConfiguration("New Camera", "rtsp://camera.example.invalid:554/stream2");

        Assert.True(camera.Enabled);
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    [InlineData(null)]
    public void UpdateConfiguration_BlankName_Throws(string? blank)
    {
        var camera = new Camera(Guid.NewGuid(), CameraName, RtspUrl);

        Assert.Throws<ArgumentException>(() => camera.UpdateConfiguration(blank!, RtspUrl));
    }

    [Fact]
    public void UpdateConfiguration_BlankRtspUrl_ExceptionDoesNotEchoTheInput()
    {
        var camera = new Camera(Guid.NewGuid(), CameraName, RtspUrl);

        var exception = Assert.Throws<ArgumentException>(() =>
            camera.UpdateConfiguration(CameraName, "   "));

        Assert.DoesNotContain("rtsp://", exception.ToString());
    }

    private static string BuildRtspUrlOfLength(int length)
    {
        const string prefix = "rtsp://camera.example.invalid:554/";
        return prefix + new string('s', length - prefix.Length);
    }
}

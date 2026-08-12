using WeaponDetection.Domain;

namespace WeaponDetection.UnitTests.Domain;

// FS-12 §4 / §9 — the structured Jetson network configuration that replaces the persisted
// AnnotatedOutputBaseUrl as the authoritative source of a Device's advertised address.
public class DeviceNetworkTests
{
    private static Device NewDevice() => new(Guid.NewGuid());

    [Theory]
    [InlineData("100.98.226.80")]
    [InlineData("192.168.1.50")]
    [InlineData("10.20.0.15")]
    [InlineData("jetson-ljmu.local")]
    [InlineData("jetson-branch-01.example.internal")]
    public void SetNetworkConfiguration_AcceptsValidHosts(string host)
    {
        var device = NewDevice();

        device.SetNetworkConfiguration(host, 8554);

        Assert.Equal(host, device.JetsonHost);
        Assert.Equal(8554, device.RtspOutputPort);
    }

    [Theory]
    [InlineData("rtsp://100.98.226.80")]   // scheme
    [InlineData("100.98.226.80:8554")]     // embedded port
    [InlineData("user:password@host")]     // credentials
    [InlineData("host/cameras")]           // path
    [InlineData("host?token=x")]           // query
    [InlineData("host#frag")]              // fragment
    public void SetNetworkConfiguration_RejectsNonBareHosts(string host)
    {
        var device = NewDevice();

        Assert.Throws<ArgumentException>(() => device.SetNetworkConfiguration(host, 8554));
    }

    [Theory]
    [InlineData(0)]
    [InlineData(-1)]
    [InlineData(65536)]
    public void SetNetworkConfiguration_RejectsOutOfRangePorts(int port)
    {
        var device = NewDevice();

        Assert.Throws<ArgumentException>(() => device.SetNetworkConfiguration("100.98.226.80", port));
    }

    [Fact]
    public void SetNetworkConfiguration_NullPortUsesTheDefault()
    {
        var device = NewDevice();

        device.SetNetworkConfiguration("100.98.226.80", null);

        Assert.Equal(Device.DefaultRtspOutputPort, device.RtspOutputPort);
    }

    // Clearing the host clears the port with it — a port alone composes nothing.
    [Fact]
    public void SetNetworkConfiguration_BlankHostClearsBoth()
    {
        var device = NewDevice();
        device.SetNetworkConfiguration("100.98.226.80", 8554);

        device.SetNetworkConfiguration(null, null);

        Assert.Null(device.JetsonHost);
        Assert.Null(device.RtspOutputPort);
        Assert.Null(device.ComposeAnnotatedOutputBase());
    }

    [Fact]
    public void ComposeAnnotatedOutputBase_UsesStructuredFields()
    {
        var device = NewDevice();
        device.SetNetworkConfiguration("100.98.226.80", 8554);

        Assert.Equal("rtsp://100.98.226.80:8554", device.ComposeAnnotatedOutputBase());
    }

    // FS-12 §4 — an IPv6 literal must be bracketed, or the port delimiter is ambiguous.
    [Fact]
    public void ComposeAnnotatedOutputBase_BracketsIpv6()
    {
        var device = NewDevice();
        device.SetNetworkConfiguration("2001:db8::1", 8554);

        Assert.Equal("rtsp://[2001:db8::1]:8554", device.ComposeAnnotatedOutputBase());
        Assert.Equal(
            "rtsp://[2001:db8::1]:8554/cameras/front-entrance",
            device.ComposeAnnotatedOutputUrl(Camera.DeriveOutputPath("front-entrance")));
    }

    // FS-12 §5 Option A — the legacy column remains a read-fallback until a later feature drops it.
    [Fact]
    public void ComposeAnnotatedOutputBase_FallsBackToLegacyColumnWhenUnmigrated()
    {
        var device = NewDevice();
        device.SetAnnotatedOutputBaseUrl("rtsp://legacy.example.internal:8554");

        Assert.Null(device.JetsonHost);
        Assert.Equal("rtsp://legacy.example.internal:8554", device.ComposeAnnotatedOutputBase());
    }

    [Fact]
    public void ComposeAnnotatedOutputBase_StructuredFieldsWinOverLegacyColumn()
    {
        var device = NewDevice();
        device.SetAnnotatedOutputBaseUrl("rtsp://legacy.example.internal:8554");
        device.SetNetworkConfiguration("100.98.226.80", 8554);

        Assert.Equal("rtsp://100.98.226.80:8554", device.ComposeAnnotatedOutputBase());
    }

    // FS-12 §9 item 10: a network edit is client-facing only.
    [Fact]
    public void SetNetworkConfiguration_LeavesIdentityAndCredentialsUntouched()
    {
        var device = NewDevice();
        device.Activate("protected-secret");
        var deviceId = device.DeviceId;

        device.SetNetworkConfiguration("192.168.1.50", 9554);

        Assert.Equal(deviceId, device.DeviceId);
        Assert.Equal(DeviceActivationStatus.Activated, device.ActivationStatus);
        Assert.True(device.CanAuthenticate());
    }
}

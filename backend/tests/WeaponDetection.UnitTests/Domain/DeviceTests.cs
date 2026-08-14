using System;
using WeaponDetection.Domain;
using Xunit;

namespace WeaponDetection.UnitTests.Domain;

// Device invariants (FS-02 §1.3, §5.5, §5.8; ARCH-001 §13.1).
//
// Every secret-shaped value below is an obvious placeholder standing in for the output of
// IDeviceSecretProtector. No real secret appears in a committed test.
public class DeviceTests
{
    private const string ProtectedSecret = "protected-placeholder-secret-1";
    private const string RotatedProtectedSecret = "protected-placeholder-secret-2";

    [Fact]
    public void Constructor_ReservesAnUnactivatedDeviceForTheBranch()
    {
        // FS-02 §5.1 step 4: the Device row exists from branch creation, but holds no external
        // identity and no credentials until an Agent activates it.
        var branchId = Guid.NewGuid();

        var device = new Device(branchId);

        Assert.Equal(branchId, device.BranchId);
        Assert.NotEqual(Guid.Empty, device.DeviceRecordId);
        Assert.Null(device.DeviceId);
        Assert.Equal(DeviceActivationStatus.Unactivated, device.ActivationStatus);
        Assert.Null(device.ProtectedSharedSecret);
        Assert.Null(device.LastKnownAddress);
    }

    [Fact]
    public void Constructor_GeneratesAUniqueDeviceRecordId()
    {
        var first = new Device(Guid.NewGuid());
        var second = new Device(Guid.NewGuid());

        Assert.NotEqual(first.DeviceRecordId, second.DeviceRecordId);
    }

    [Fact]
    public void Constructor_EmptyBranchId_IsRejected()
    {
        Assert.Throws<ArgumentException>(() => new Device(Guid.Empty));
    }

    [Fact]
    public void Activate_AssignsADeviceIdAndMarksTheDeviceActivated()
    {
        // FS-02 §5.5 step 7 / AC-3.
        var device = new Device(Guid.NewGuid());

        device.Activate(ProtectedSecret);

        Assert.NotNull(device.DeviceId);
        Assert.NotEqual(Guid.Empty, device.DeviceId!.Value);
        Assert.Equal(DeviceActivationStatus.Activated, device.ActivationStatus);
        Assert.Equal(ProtectedSecret, device.ProtectedSharedSecret);
    }

    [Fact]
    public void Activate_AssignsADistinctDeviceIdPerDevice()
    {
        var first = new Device(Guid.NewGuid());
        var second = new Device(Guid.NewGuid());

        first.Activate(ProtectedSecret);
        second.Activate(ProtectedSecret);

        Assert.NotEqual(first.DeviceId, second.DeviceId);
    }

    [Fact]
    public void Activate_Reactivation_RetainsTheOriginalDeviceId()
    {
        // The single most important invariant in FS-02: AC-7 / §1.3 / §5.8 step 5 — a DeviceId is
        // assigned exactly once and never reassigned, so historical alerts and health records stay
        // correlated when a Jetson unit is replaced.
        var device = new Device(Guid.NewGuid());
        device.Activate(ProtectedSecret);
        var originalDeviceId = device.DeviceId;

        device.Activate(RotatedProtectedSecret);

        Assert.Equal(originalDeviceId, device.DeviceId);
    }

    [Fact]
    public void Activate_Reactivation_ReplacesTheSharedSecret()
    {
        // FS-02 §5.8 step 6 / NFR-SEC-002: rotating the secret is the security purpose of a
        // reactivation, so the previous one must not survive it.
        var device = new Device(Guid.NewGuid());
        device.Activate(ProtectedSecret);

        device.Activate(RotatedProtectedSecret);

        Assert.Equal(RotatedProtectedSecret, device.ProtectedSharedSecret);
        Assert.NotEqual(ProtectedSecret, device.ProtectedSharedSecret);
    }

    [Fact]
    public void Activate_Reactivation_LeavesTheDeviceActivated()
    {
        var device = new Device(Guid.NewGuid());
        device.Activate(ProtectedSecret);

        device.Activate(RotatedProtectedSecret);

        Assert.Equal(DeviceActivationStatus.Activated, device.ActivationStatus);
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    [InlineData(null)]
    public void Activate_MissingProtectedSecret_IsRejected(string? protectedSharedSecret)
    {
        var device = new Device(Guid.NewGuid());

        Assert.Throws<ArgumentException>(() => device.Activate(protectedSharedSecret!));
    }

    [Fact]
    public void Activate_OverlongProtectedSecret_IsRejected()
    {
        var device = new Device(Guid.NewGuid());
        var overlong = new string('x', Device.ProtectedSharedSecretMaxLength + 1);

        Assert.Throws<ArgumentException>(() => device.Activate(overlong));
    }

    [Fact]
    public void Activate_RejectedSecret_LeavesTheDeviceUntouched()
    {
        // FS-02 §5.6 step 3: a rejected activation has no side effects on the Device record.
        var device = new Device(Guid.NewGuid());

        Assert.Throws<ArgumentException>(() => device.Activate(string.Empty));

        Assert.Null(device.DeviceId);
        Assert.Equal(DeviceActivationStatus.Unactivated, device.ActivationStatus);
        Assert.Null(device.ProtectedSharedSecret);
    }

    // --- IP-05 T-48: immediate credential revocation (ReactivationRequired) ----------------------

    [Fact]
    public void RequireReactivation_FromActivated_MovesToReactivationRequiredAndRevokesTheSecret()
    {
        // FS-02 §5.3 (amended): regenerating the key of an activated device immediately revokes its
        // shared secret and moves it to ReactivationRequired, keeping the permanent DeviceId.
        var device = new Device(Guid.NewGuid());
        device.Activate(ProtectedSecret);
        var deviceId = device.DeviceId;

        device.RequireReactivation();

        Assert.Equal(DeviceActivationStatus.ReactivationRequired, device.ActivationStatus);
        Assert.Equal(deviceId, device.DeviceId); // DeviceId preserved (AC-2/AC-12)
        Assert.Null(device.ProtectedSharedSecret); // secret revoked (AC-3)
    }

    [Fact]
    public void RequireReactivation_PreservesTheDeviceId()
    {
        var device = new Device(Guid.NewGuid());
        device.Activate(ProtectedSecret);
        var originalDeviceId = device.DeviceId;

        device.RequireReactivation();

        Assert.Equal(originalDeviceId, device.DeviceId);
    }

    [Fact]
    public void RequireReactivation_OnUnactivatedDevice_IsRejectedAndLeavesItUntouched()
    {
        // An Unactivated device has no DeviceId and no secret to revoke; the regeneration service
        // must not call this path for it (§5.3). Calling it is a caller bug, not a silent no-op.
        var device = new Device(Guid.NewGuid());

        Assert.Throws<InvalidOperationException>(() => device.RequireReactivation());

        Assert.Null(device.DeviceId);
        Assert.Equal(DeviceActivationStatus.Unactivated, device.ActivationStatus);
        Assert.Null(device.ProtectedSharedSecret);
    }

    [Fact]
    public void RequireReactivation_CalledAgainWhileReactivationRequired_IsIdempotent()
    {
        // Clarification #5: regenerating again while already ReactivationRequired preserves the
        // DeviceId, keeps the secret null, and stays ReactivationRequired.
        var device = new Device(Guid.NewGuid());
        device.Activate(ProtectedSecret);
        device.RequireReactivation();
        var deviceId = device.DeviceId;

        device.RequireReactivation();

        Assert.Equal(DeviceActivationStatus.ReactivationRequired, device.ActivationStatus);
        Assert.Equal(deviceId, device.DeviceId);
        Assert.Null(device.ProtectedSharedSecret);
    }

    [Fact]
    public void Activate_FromReactivationRequired_ReturnsToActivatedRetainingDeviceIdAndStoringNewSecret()
    {
        // FS-02 §5.8: reactivation from ReactivationRequired retains the DeviceId, stores the new
        // protected secret, and returns the device to Activated.
        var device = new Device(Guid.NewGuid());
        device.Activate(ProtectedSecret);
        var originalDeviceId = device.DeviceId;
        device.RequireReactivation();

        device.Activate(RotatedProtectedSecret);

        Assert.Equal(DeviceActivationStatus.Activated, device.ActivationStatus);
        Assert.Equal(originalDeviceId, device.DeviceId);
        Assert.Equal(RotatedProtectedSecret, device.ProtectedSharedSecret);
    }

    // --- IP-05 T-48: the credential-state authentication guard (§2.1, FS-02 §11) -----------------

    [Fact]
    public void CanAuthenticate_TrueOnlyWhenActivatedWithAPresentSecret()
    {
        var device = new Device(Guid.NewGuid());
        device.Activate(ProtectedSecret);

        Assert.True(device.CanAuthenticate());
    }

    [Fact]
    public void CanAuthenticate_FalseWhenUnactivated()
    {
        var device = new Device(Guid.NewGuid());

        Assert.False(device.CanAuthenticate());
    }

    [Fact]
    public void CanAuthenticate_FalseWhenReactivationRequired()
    {
        var device = new Device(Guid.NewGuid());
        device.Activate(ProtectedSecret);
        device.RequireReactivation();

        Assert.False(device.CanAuthenticate());
    }

    [Fact]
    public void CanAuthenticate_FalseForReactivationRequired_EvenIfAStraySecretIsPresent()
    {
        // Inconsistent-state protection (clarification #2): the guard checks status first, so even a
        // legacy/inconsistent row that left a secret value present on a ReactivationRequired device
        // cannot authenticate. The public mutators cannot produce this combination, so the stray
        // secret is injected via the same private setter EF Core would use on materialization.
        var device = new Device(Guid.NewGuid());
        device.Activate(ProtectedSecret);
        device.RequireReactivation();

        typeof(Device)
            .GetProperty(nameof(Device.ProtectedSharedSecret))!
            .SetValue(device, ProtectedSecret);

        Assert.Equal(ProtectedSecret, device.ProtectedSharedSecret); // stray secret really is present
        Assert.Equal(DeviceActivationStatus.ReactivationRequired, device.ActivationStatus);
        Assert.False(device.CanAuthenticate()); // ...but status gates it out
    }

    // --- FS-11 §11: annotated-output base URL -----------------------------------------------------

    [Theory]
    [InlineData("rtsp://100.98.226.80:8554")]
    [InlineData("rtsps://host.example.invalid:8554")]
    [InlineData("rtsp://host.example.invalid")]
    public void SetAnnotatedOutputBaseUrl_AcceptsValidRtspBase(string baseUrl)
    {
        var device = new Device(Guid.NewGuid());

        device.SetAnnotatedOutputBaseUrl(baseUrl);

        Assert.Equal(baseUrl, device.AnnotatedOutputBaseUrl);
    }

    [Fact]
    public void SetAnnotatedOutputBaseUrl_TrimsTrailingSlash()
    {
        var device = new Device(Guid.NewGuid());

        device.SetAnnotatedOutputBaseUrl("rtsp://100.98.226.80:8554/");

        Assert.Equal("rtsp://100.98.226.80:8554", device.AnnotatedOutputBaseUrl);
    }

    [Theory]
    [InlineData("http://host:8554")]                              // wrong scheme
    [InlineData("https://host:8554")]
    [InlineData("rtsp://user:password@host:8554")]                // embedded credentials
    [InlineData("rtsp://host:8554/cameras/specific-camera")]      // trailing camera path
    [InlineData("rtsp://host:8554?token=value")]                  // query string
    [InlineData("rtsp://host:8554#frag")]                         // fragment
    [InlineData("not-a-uri")]
    public void SetAnnotatedOutputBaseUrl_RejectsUnsafeOrMalformedValues(string baseUrl)
    {
        var device = new Device(Guid.NewGuid());

        Assert.Throws<ArgumentException>(() => device.SetAnnotatedOutputBaseUrl(baseUrl));
        Assert.Null(device.AnnotatedOutputBaseUrl); // rejected value never stored
    }

    [Fact]
    public void SetAnnotatedOutputBaseUrl_RejectionMessageNeverEchoesTheValue()
    {
        var device = new Device(Guid.NewGuid());

        var exception = Assert.Throws<ArgumentException>(
            () => device.SetAnnotatedOutputBaseUrl("rtsp://someuser:somepassword@host:8554"));

        Assert.DoesNotContain("somepassword", exception.ToString());
        Assert.DoesNotContain("someuser", exception.ToString());
    }

    [Fact]
    public void SetAnnotatedOutputBaseUrl_OverLongValueRejected()
    {
        var device = new Device(Guid.NewGuid());
        var tooLong = "rtsp://" + new string('h', Device.AnnotatedOutputBaseUrlMaxLength) + ":8554";

        Assert.Throws<ArgumentException>(() => device.SetAnnotatedOutputBaseUrl(tooLong));
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("   ")]
    public void SetAnnotatedOutputBaseUrl_NullOrBlankClearsTheValue(string? baseUrl)
    {
        var device = new Device(Guid.NewGuid());
        device.SetAnnotatedOutputBaseUrl("rtsp://100.98.226.80:8554");

        device.SetAnnotatedOutputBaseUrl(baseUrl);

        Assert.Null(device.AnnotatedOutputBaseUrl);
    }

    [Fact]
    public void ComposeAnnotatedOutputUrl_JoinsBaseAndCameraOutputPath()
    {
        // FS-12 §2.1 — composed from the CameraKey-derived mount. This case still sets the *legacy*
        // base URL, which exercises the Option A read-fallback: a Device not yet migrated to the
        // structured host/port pair must keep composing a working URL.
        var device = new Device(Guid.NewGuid());
        device.SetAnnotatedOutputBaseUrl("rtsp://100.98.226.80:8554");

        var url = device.ComposeAnnotatedOutputUrl(Camera.DeriveOutputPath("front-entrance"));

        Assert.Equal("rtsp://100.98.226.80:8554/cameras/front-entrance", url);
    }

    [Fact]
    public void ComposeAnnotatedOutputUrl_DistinctCamerasGetDistinctUrls()
    {
        var device = new Device(Guid.NewGuid());
        device.SetAnnotatedOutputBaseUrl("rtsp://100.98.226.80:8554");

        var first = device.ComposeAnnotatedOutputUrl(Camera.DeriveOutputPath("cam-" + Guid.NewGuid().ToString("N")[..8]));
        var second = device.ComposeAnnotatedOutputUrl(Camera.DeriveOutputPath("cam-" + Guid.NewGuid().ToString("N")[..8]));

        Assert.NotEqual(first, second);
    }

    [Fact]
    public void ComposeAnnotatedOutputUrl_NullBaseYieldsNull_NeverAnInventedAddress()
    {
        var device = new Device(Guid.NewGuid());

        Assert.Null(device.ComposeAnnotatedOutputUrl(Camera.DeriveOutputPath("cam-" + Guid.NewGuid().ToString("N")[..8])));
    }

    [Fact]
    public void SetAnnotatedOutputBaseUrl_PreservesIdentityCredentialsAndActivationState()
    {
        var device = new Device(Guid.NewGuid());
        device.Activate(ProtectedSecret);
        var deviceIdBefore = device.DeviceId;

        device.SetAnnotatedOutputBaseUrl("rtsp://100.98.226.80:8554");

        // Discovery metadata only — nothing about identity, credentials or activation may move.
        Assert.Equal(deviceIdBefore, device.DeviceId);
        Assert.Equal(ProtectedSecret, device.ProtectedSharedSecret);
        Assert.Equal(DeviceActivationStatus.Activated, device.ActivationStatus);
        Assert.True(device.CanAuthenticate());
    }
}

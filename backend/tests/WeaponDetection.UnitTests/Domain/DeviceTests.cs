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
}

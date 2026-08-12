using System;
using WeaponDetection.Domain;
using Xunit;

namespace WeaponDetection.UnitTests.Domain;

// Alert invariants (FS-06 §5.1): confidence bounds and non-negative frame/bbox values are enforced
// at construction, mirroring Camera/Device's constructor-invariant style. No value under test here is
// sensitive (unlike an RTSP URL or a device secret), so the exception-message tests only need to
// confirm the limit is named, not that a value was suppressed.
public class AlertTests
{
    private static readonly Guid DeviceId = Guid.NewGuid();
    private static readonly Guid EventId = Guid.NewGuid();
    private static readonly Guid CameraId = Guid.NewGuid();
    private static readonly DateTime DetectedAtUtc = new(2026, 7, 24, 18, 30, 0, DateTimeKind.Utc);
    private static readonly DateTime ReceivedAtUtc = new(2026, 7, 24, 18, 30, 5, DateTimeKind.Utc);

    private static Alert CreateValidAlert(
        double confidence = 0.91,
        long frameNumber = 12345,
        int frameWidth = 1280,
        int frameHeight = 720,
        double bboxLeft = 420.0,
        double bboxTop = 180.0,
        double bboxWidth = 250.0,
        double bboxHeight = 190.0,
        string? snapshotReference = null) =>
        new(
            DeviceId,
            EventId,
            CameraId,
            DetectedAtUtc,
            ReceivedAtUtc,
            classId: 0,
            className: "gun",
            confidence,
            frameNumber,
            frameWidth,
            frameHeight,
            bboxLeft,
            bboxTop,
            bboxWidth,
            bboxHeight,
            snapshotReference);

    [Fact]
    public void Constructor_ValidValues_SetsAllFields()
    {
        var alert = CreateValidAlert();

        Assert.NotEqual(Guid.Empty, alert.AlertId);
        Assert.Equal(DeviceId, alert.DeviceId);
        Assert.Equal(EventId, alert.EventId);
        Assert.Equal(CameraId, alert.CameraId);
        Assert.Equal(DetectedAtUtc, alert.DetectedAtUtc);
        Assert.Equal(ReceivedAtUtc, alert.ReceivedAtUtc);
        Assert.Equal(0, alert.ClassId);
        Assert.Equal("gun", alert.ClassName);
        Assert.Equal(0.91, alert.Confidence);
        Assert.Equal(12345, alert.FrameNumber);
        Assert.Equal(1280, alert.FrameWidth);
        Assert.Equal(720, alert.FrameHeight);
        Assert.Equal(420.0, alert.BboxLeft);
        Assert.Equal(180.0, alert.BboxTop);
        Assert.Equal(250.0, alert.BboxWidth);
        Assert.Equal(190.0, alert.BboxHeight);
    }

    [Fact]
    public void Constructor_GeneratesAUniqueNonEmptyAlertId()
    {
        var first = CreateValidAlert();
        var second = CreateValidAlert();

        Assert.NotEqual(Guid.Empty, first.AlertId);
        Assert.NotEqual(first.AlertId, second.AlertId);
    }

    [Fact]
    public void Constructor_AlwaysStartsNew()
    {
        var alert = CreateValidAlert();

        Assert.Equal(AlertStatus.New, alert.Status);
    }

    [Fact]
    public void Constructor_SnapshotReferenceDefaultsToNull()
    {
        var alert = CreateValidAlert();

        Assert.Null(alert.SnapshotReference);
    }

    [Fact]
    public void Constructor_PreservesDetectedAtUtcSeparatelyFromReceivedAtUtc()
    {
        // FR-SYN-004: the original detection timestamp is never overwritten by the server's own
        // persistence time.
        var alert = CreateValidAlert();

        Assert.NotEqual(alert.ReceivedAtUtc, alert.DetectedAtUtc);
        Assert.Equal(DetectedAtUtc, alert.DetectedAtUtc);
        Assert.Equal(ReceivedAtUtc, alert.ReceivedAtUtc);
    }

    [Theory]
    [InlineData(0.0)]
    [InlineData(1.0)]
    [InlineData(0.5)]
    public void Constructor_ConfidenceWithinBounds_IsAccepted(double confidence)
    {
        var alert = CreateValidAlert(confidence: confidence);

        Assert.Equal(confidence, alert.Confidence);
    }

    [Theory]
    [InlineData(-0.0001)]
    [InlineData(1.0001)]
    [InlineData(double.NaN)]
    public void Constructor_ConfidenceOutOfBounds_Throws(double confidence)
    {
        Assert.Throws<ArgumentException>(() => CreateValidAlert(confidence: confidence));
    }

    [Fact]
    public void Constructor_NegativeFrameNumber_Throws()
    {
        Assert.Throws<ArgumentException>(() => CreateValidAlert(frameNumber: -1));
    }

    [Fact]
    public void Constructor_NegativeFrameWidth_Throws()
    {
        Assert.Throws<ArgumentException>(() => CreateValidAlert(frameWidth: -1));
    }

    [Fact]
    public void Constructor_NegativeFrameHeight_Throws()
    {
        Assert.Throws<ArgumentException>(() => CreateValidAlert(frameHeight: -1));
    }

    [Theory]
    [InlineData(-1.0, 0.0, 0.0, 0.0)]
    [InlineData(0.0, -1.0, 0.0, 0.0)]
    [InlineData(0.0, 0.0, -1.0, 0.0)]
    [InlineData(0.0, 0.0, 0.0, -1.0)]
    public void Constructor_NegativeBoundingBoxValue_Throws(
        double left, double top, double width, double height)
    {
        Assert.Throws<ArgumentException>(() =>
            CreateValidAlert(bboxLeft: left, bboxTop: top, bboxWidth: width, bboxHeight: height));
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("   ")]
    public void Constructor_MissingOrBlankClassName_Throws(string? className)
    {
        Assert.Throws<ArgumentException>(() => new Alert(
            DeviceId, EventId, CameraId, DetectedAtUtc, ReceivedAtUtc,
            classId: 0, className: className!, confidence: 0.5,
            frameNumber: 0, frameWidth: 0, frameHeight: 0,
            bboxLeft: 0, bboxTop: 0, bboxWidth: 0, bboxHeight: 0));
    }

    [Fact]
    public void Constructor_TrimsClassName()
    {
        var alert = new Alert(
            DeviceId, EventId, CameraId, DetectedAtUtc, ReceivedAtUtc,
            classId: 0, className: "  gun  ", confidence: 0.5,
            frameNumber: 0, frameWidth: 0, frameHeight: 0,
            bboxLeft: 0, bboxTop: 0, bboxWidth: 0, bboxHeight: 0);

        Assert.Equal("gun", alert.ClassName);
    }

    [Fact]
    public void Constructor_EmptyDeviceId_Throws()
    {
        Assert.Throws<ArgumentException>(() => new Alert(
            Guid.Empty, EventId, CameraId, DetectedAtUtc, ReceivedAtUtc,
            classId: 0, className: "gun", confidence: 0.5,
            frameNumber: 0, frameWidth: 0, frameHeight: 0,
            bboxLeft: 0, bboxTop: 0, bboxWidth: 0, bboxHeight: 0));
    }

    [Fact]
    public void Constructor_EmptyEventId_Throws()
    {
        Assert.Throws<ArgumentException>(() => new Alert(
            DeviceId, Guid.Empty, CameraId, DetectedAtUtc, ReceivedAtUtc,
            classId: 0, className: "gun", confidence: 0.5,
            frameNumber: 0, frameWidth: 0, frameHeight: 0,
            bboxLeft: 0, bboxTop: 0, bboxWidth: 0, bboxHeight: 0));
    }

    [Fact]
    public void Constructor_EmptyCameraId_Throws()
    {
        Assert.Throws<ArgumentException>(() => new Alert(
            DeviceId, EventId, Guid.Empty, DetectedAtUtc, ReceivedAtUtc,
            classId: 0, className: "gun", confidence: 0.5,
            frameNumber: 0, frameWidth: 0, frameHeight: 0,
            bboxLeft: 0, bboxTop: 0, bboxWidth: 0, bboxHeight: 0));
    }

    // --- AttachSnapshot (FS-08 §9/§10) ---

    private const string Sha256A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    private const string Sha256B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    private static readonly DateTime SnapshotReceivedAtUtc = new(2026, 7, 28, 12, 0, 0, DateTimeKind.Utc);

    [Fact]
    public void AttachSnapshot_FirstCall_ReturnsAttached_AndSetsAllFourFields()
    {
        var alert = CreateValidAlert();

        var outcome = alert.AttachSnapshot(
            "11111111-1111-1111-1111-111111111111.jpg", Sha256A, "image/jpeg", 12345, SnapshotReceivedAtUtc);

        Assert.Equal(AttachSnapshotOutcome.Attached, outcome);
        Assert.Equal("11111111-1111-1111-1111-111111111111.jpg", alert.SnapshotReference);
        Assert.Equal(Sha256A, alert.SnapshotSha256);
        Assert.Equal("image/jpeg", alert.SnapshotContentType);
        Assert.Equal(12345, alert.SnapshotSizeBytes);
        Assert.Equal(SnapshotReceivedAtUtc, alert.SnapshotReceivedAtUtc);
    }

    [Fact]
    public void AttachSnapshot_SecondCallWithIdenticalSha256_ReturnsDuplicate_AndDoesNotChangeStoredFields()
    {
        var alert = CreateValidAlert();
        alert.AttachSnapshot("ref.jpg", Sha256A, "image/jpeg", 100, SnapshotReceivedAtUtc);

        var later = SnapshotReceivedAtUtc.AddMinutes(5);
        var outcome = alert.AttachSnapshot("ref.jpg", Sha256A, "image/jpeg", 100, later);

        Assert.Equal(AttachSnapshotOutcome.Duplicate, outcome);
        Assert.Equal(SnapshotReceivedAtUtc, alert.SnapshotReceivedAtUtc);
    }

    [Fact]
    public void AttachSnapshot_SecondCallWithIdenticalSha256_IsCaseInsensitive()
    {
        var alert = CreateValidAlert();
        alert.AttachSnapshot("ref.jpg", Sha256A.ToUpperInvariant(), "image/jpeg", 100, SnapshotReceivedAtUtc);

        var outcome = alert.AttachSnapshot("ref.jpg", Sha256A, "image/jpeg", 100, SnapshotReceivedAtUtc);

        Assert.Equal(AttachSnapshotOutcome.Duplicate, outcome);
    }

    [Fact]
    public void AttachSnapshot_SecondCallWithDifferentSha256_ReturnsConflict_AndPreservesTheOriginal()
    {
        var alert = CreateValidAlert();
        alert.AttachSnapshot("ref.jpg", Sha256A, "image/jpeg", 100, SnapshotReceivedAtUtc);

        var outcome = alert.AttachSnapshot("ref.jpg", Sha256B, "image/jpeg", 200, SnapshotReceivedAtUtc.AddHours(1));

        Assert.Equal(AttachSnapshotOutcome.Conflict, outcome);
        Assert.Equal(Sha256A, alert.SnapshotSha256);
        Assert.Equal(100, alert.SnapshotSizeBytes);
        Assert.Equal(SnapshotReceivedAtUtc, alert.SnapshotReceivedAtUtc);
    }

    [Fact]
    public void AttachSnapshot_BlankReference_Throws()
    {
        var alert = CreateValidAlert();

        Assert.Throws<ArgumentException>(
            () => alert.AttachSnapshot("   ", Sha256A, "image/jpeg", 100, SnapshotReceivedAtUtc));
    }

    [Theory]
    [InlineData("")]
    [InlineData("tooshort")]
    public void AttachSnapshot_Sha256NotSixtyFourCharacters_Throws(string sha256)
    {
        var alert = CreateValidAlert();

        Assert.Throws<ArgumentException>(
            () => alert.AttachSnapshot("ref.jpg", sha256, "image/jpeg", 100, SnapshotReceivedAtUtc));
    }

    [Fact]
    public void AttachSnapshot_BlankContentType_Throws()
    {
        var alert = CreateValidAlert();

        Assert.Throws<ArgumentException>(
            () => alert.AttachSnapshot("ref.jpg", Sha256A, "  ", 100, SnapshotReceivedAtUtc));
    }

    [Theory]
    [InlineData(0)]
    [InlineData(-1)]
    public void AttachSnapshot_NonPositiveSizeBytes_Throws(long sizeBytes)
    {
        var alert = CreateValidAlert();

        Assert.Throws<ArgumentException>(
            () => alert.AttachSnapshot("ref.jpg", Sha256A, "image/jpeg", sizeBytes, SnapshotReceivedAtUtc));
    }
}

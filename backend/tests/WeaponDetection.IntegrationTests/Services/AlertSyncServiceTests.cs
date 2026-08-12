using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using WeaponDetection.Application.Interfaces;
using WeaponDetection.Domain;
using WeaponDetection.Infrastructure.Persistence;
using WeaponDetection.Infrastructure.Services;
using Xunit;

namespace WeaponDetection.IntegrationTests.Services;

// Verifies AlertSyncService (FS-06 §5.2, §6.3; IP-08 T-98/T-102) against a real SQL Server database:
// the savepoint-based idempotent insert and the (DeviceId, EventId) unique index are relational
// behaviors EF Core InMemory/SQLite would not faithfully reproduce (IP-01 §9, OI-12). Each test gets
// its own freshly migrated, empty database.
public class AlertSyncServiceTests : IDisposable
{
    private readonly WeaponDetectionDbContext _dbContext;
    private readonly AlertSyncService _service;
    private readonly Guid _deviceId = Guid.NewGuid();
    private Guid _branchId;

    private static readonly DateTime DetectedAtUtc = new(2026, 7, 24, 18, 30, 0, DateTimeKind.Utc);

    public AlertSyncServiceTests()
    {
        var connectionString =
            $"Server=localhost\\SQLEXPRESS;Database=WeaponDetectionAlertSyncServiceTests_{Guid.NewGuid():N};" +
            "Trusted_Connection=True;TrustServerCertificate=True;";

        var options = new DbContextOptionsBuilder<WeaponDetectionDbContext>()
            .UseSqlServer(connectionString)
            .Options;

        _dbContext = new WeaponDetectionDbContext(options);
        _dbContext.Database.Migrate();

        _service = CreateService(_dbContext);
    }

    public void Dispose()
    {
        _dbContext.Database.EnsureDeleted();
        _dbContext.Dispose();
    }

    // FS-09 §11: default matches AlertQuotaOptions' own default (Enabled=true,
    // MaximumPerBranchPerDay=15) so most existing (pre-FS-09) tests are unaffected; individual quota
    // tests override maximumPerBranchPerDay to keep test bodies short (e.g. 2 instead of sending 16
    // events to prove a boundary).
    private static AlertSyncService CreateService(
        WeaponDetectionDbContext dbContext,
        int maximumPerBranchPerDay = 15,
        bool enabled = true,
        TimeProvider? timeProvider = null) =>
        new(
            dbContext,
            timeProvider ?? TimeProvider.System,
            Options.Create(new AlertQuotaOptions { Enabled = enabled, MaximumPerBranchPerDay = maximumPerBranchPerDay }),
            NullLogger<AlertSyncService>.Instance);

    private async Task<Camera> SeedBranchWithCameraAsync(
        string cameraName = "camera1", bool enabled = true, Guid? branchId = null, int sourceOrder = 0)
    {
        var branch = branchId is null
            ? new Branch("Downtown Branch", "1 High Street", "ops@example.local")
            : null;

        if (branch is not null)
        {
            _dbContext.Branches.Add(branch);
            await _dbContext.SaveChangesAsync();
            _branchId = branch.BranchId;
        }

        var camera = new Camera(
            branchId ?? _branchId, cameraName, "rtsp://camera.example.invalid:554/stream1", $"cam-{Guid.NewGuid():N}", enabled,
            sourceOrder);
        _dbContext.Cameras.Add(camera);
        await _dbContext.SaveChangesAsync();

        return camera;
    }

    private static DetectionEventSyncItem MakeItem(
        Guid? eventId = null,
        string cameraId = "camera1",
        double confidence = 0.91,
        long frameNumber = 12345,
        int frameWidth = 1280,
        int frameHeight = 720,
        double bboxLeft = 420.0,
        double bboxTop = 180.0,
        double bboxWidth = 250.0,
        double bboxHeight = 190.0,
        DateTime? detectedAtUtc = null,
        string className = "gun") =>
        new(
            eventId ?? Guid.NewGuid(),
            cameraId,
            detectedAtUtc ?? DetectedAtUtc,
            CreatedAtUtc: DetectedAtUtc.AddMilliseconds(150),
            ClassId: 0,
            ClassName: className,
            Confidence: confidence,
            SourceId: 0,
            FrameNumber: frameNumber,
            FrameWidth: frameWidth,
            FrameHeight: frameHeight,
            BoundingBox: new BoundingBoxValue(bboxLeft, bboxTop, bboxWidth, bboxHeight));

    // --- Happy path (Phase 13 items 1-4) ---

    [Fact]
    public async Task SyncEventsAsync_ValidEvent_CreatesExactlyOneAlert_WithStatusNew()
    {
        var camera = await SeedBranchWithCameraAsync();
        var item = MakeItem();

        var outcomes = await _service.SyncEventsAsync(_deviceId, _branchId, [item]);

        Assert.Single(outcomes);
        Assert.Equal(SyncEventOutcomeKind.Accepted, outcomes[0].Kind);
        Assert.NotNull(outcomes[0].AlertId);

        var alerts = await _dbContext.Alerts.AsNoTracking()
            .Where(a => a.DeviceId == _deviceId && a.EventId == item.EventId).ToListAsync();
        Assert.Single(alerts);
        Assert.Equal(AlertStatus.New, alerts[0].Status);
        Assert.Equal(camera.CameraId, alerts[0].CameraId);
    }

    [Fact]
    public async Task SyncEventsAsync_ValidEvent_PreservesDetectedAtUtcExactly()
    {
        await SeedBranchWithCameraAsync();
        var detectedAt = new DateTime(2026, 7, 24, 18, 30, 0, 123, DateTimeKind.Utc);
        var item = MakeItem(detectedAtUtc: detectedAt);

        await _service.SyncEventsAsync(_deviceId, _branchId, [item]);

        var alert = await _dbContext.Alerts.AsNoTracking()
            .SingleAsync(a => a.DeviceId == _deviceId && a.EventId == item.EventId);
        Assert.Equal(detectedAt, alert.DetectedAtUtc);
    }

    [Fact]
    public async Task SyncEventsAsync_ValidEvent_ReceivedAtUtcIsServerTime_NotTheAgentsCreatedAtUtc()
    {
        await SeedBranchWithCameraAsync();
        var longAgo = new DateTime(2000, 1, 1, 0, 0, 0, DateTimeKind.Utc);
        var item = MakeItem() with { CreatedAtUtc = longAgo };
        var before = DateTime.UtcNow;

        await _service.SyncEventsAsync(_deviceId, _branchId, [item]);

        var alert = await _dbContext.Alerts.AsNoTracking()
            .SingleAsync(a => a.DeviceId == _deviceId && a.EventId == item.EventId);
        Assert.True(alert.ReceivedAtUtc >= before);
        Assert.NotEqual(longAgo, alert.ReceivedAtUtc);
    }

    [Fact]
    public async Task SyncEventsAsync_ValidEvent_SnapshotReferenceIsNull()
    {
        await SeedBranchWithCameraAsync();
        var item = MakeItem();

        await _service.SyncEventsAsync(_deviceId, _branchId, [item]);

        var alert = await _dbContext.Alerts.AsNoTracking()
            .SingleAsync(a => a.DeviceId == _deviceId && a.EventId == item.EventId);
        Assert.Null(alert.SnapshotReference);
    }

    // --- Camera resolution (FS-06 §6.3) ---

    [Fact]
    public async Task SyncEventsAsync_UnknownCameraName_IsRejected()
    {
        await SeedBranchWithCameraAsync(cameraName: "camera1");
        var item = MakeItem(cameraId: "does-not-exist");

        var outcomes = await _service.SyncEventsAsync(_deviceId, _branchId, [item]);

        Assert.Equal(SyncEventOutcomeKind.Rejected, outcomes[0].Kind);
        Assert.Equal(SyncEventErrorCodes.UnknownCamera, outcomes[0].ErrorCode);
        Assert.Empty(await _dbContext.Alerts.Where(a => a.EventId == item.EventId).ToListAsync());
    }

    [Fact]
    public async Task SyncEventsAsync_CameraNameMatch_IsCaseInsensitiveAndTrimmed()
    {
        await SeedBranchWithCameraAsync(cameraName: "  Camera1  ".Trim());
        var item = MakeItem(cameraId: "CAMERA1");

        var outcomes = await _service.SyncEventsAsync(_deviceId, _branchId, [item]);

        Assert.Equal(SyncEventOutcomeKind.Accepted, outcomes[0].Kind);
    }

    [Fact]
    public async Task SyncEventsAsync_DisabledCamera_IsTreatedAsUnknown()
    {
        await SeedBranchWithCameraAsync(cameraName: "camera1", enabled: false);
        var item = MakeItem(cameraId: "camera1");

        var outcomes = await _service.SyncEventsAsync(_deviceId, _branchId, [item]);

        Assert.Equal(SyncEventOutcomeKind.Rejected, outcomes[0].Kind);
        Assert.Equal(SyncEventErrorCodes.UnknownCamera, outcomes[0].ErrorCode);
    }

    [Fact]
    public async Task SyncEventsAsync_CameraBelongingToAnotherBranch_IsRejected()
    {
        // A camera named identically but owned by a different branch must never resolve — Branch
        // scoping is not optional (FS-06 §6.3).
        var otherBranch = new Branch("Other Branch", "2 Low Street", "ops2@example.local");
        _dbContext.Branches.Add(otherBranch);
        await _dbContext.SaveChangesAsync();
        _dbContext.Cameras.Add(new Camera(otherBranch.BranchId, "camera1", "rtsp://camera.example.invalid:554/x", $"cam-{Guid.NewGuid():N}"));
        await _dbContext.SaveChangesAsync();

        // The authenticated device's own branch has no matching camera.
        var ownBranch = new Branch("Own Branch", "1 High Street", "ops@example.local");
        _dbContext.Branches.Add(ownBranch);
        await _dbContext.SaveChangesAsync();
        _branchId = ownBranch.BranchId;

        var item = MakeItem(cameraId: "camera1");

        var outcomes = await _service.SyncEventsAsync(_deviceId, _branchId, [item]);

        Assert.Equal(SyncEventOutcomeKind.Rejected, outcomes[0].Kind);
        Assert.Equal(SyncEventErrorCodes.UnknownCamera, outcomes[0].ErrorCode);
    }

    // --- FS-11 §10: immutable Camera.CameraId resolution (GUID wire value) -------------------------

    [Fact]
    public async Task SyncEventsAsync_GuidCameraId_ResolvesDirectlyWithoutNameMatch()
    {
        // FS-11 §10: the camera's Name is deliberately something that would NOT match via the old
        // Camera.Name resolution path, proving the GUID branch is what actually resolved it.
        var camera = await SeedBranchWithCameraAsync(cameraName: "Front Camera");
        var item = MakeItem(cameraId: camera.CameraId.ToString());

        var outcomes = await _service.SyncEventsAsync(_deviceId, _branchId, [item]);

        Assert.Equal(SyncEventOutcomeKind.Accepted, outcomes[0].Kind);
    }

    [Fact]
    public async Task SyncEventsAsync_GuidCameraIdBelongingToAnotherBranch_IsRejected()
    {
        var otherBranch = new Branch("Other Branch", "2 Low Street", "ops2@example.local");
        _dbContext.Branches.Add(otherBranch);
        await _dbContext.SaveChangesAsync();
        var otherCamera = new Camera(otherBranch.BranchId, "Other Camera", "rtsp://camera.example.invalid:554/x", $"cam-{Guid.NewGuid():N}");
        _dbContext.Cameras.Add(otherCamera);
        await _dbContext.SaveChangesAsync();

        var ownBranch = new Branch("Own Branch", "1 High Street", "ops@example.local");
        _dbContext.Branches.Add(ownBranch);
        await _dbContext.SaveChangesAsync();
        _branchId = ownBranch.BranchId;

        var item = MakeItem(cameraId: otherCamera.CameraId.ToString());

        var outcomes = await _service.SyncEventsAsync(_deviceId, _branchId, [item]);

        Assert.Equal(SyncEventOutcomeKind.Rejected, outcomes[0].Kind);
        Assert.Equal(SyncEventErrorCodes.UnknownCamera, outcomes[0].ErrorCode);
    }

    [Fact]
    public async Task SyncEventsAsync_GuidCameraIdForDisabledCamera_IsTreatedAsUnknown()
    {
        var camera = await SeedBranchWithCameraAsync(cameraName: "Front Camera", enabled: false);
        var item = MakeItem(cameraId: camera.CameraId.ToString());

        var outcomes = await _service.SyncEventsAsync(_deviceId, _branchId, [item]);

        Assert.Equal(SyncEventOutcomeKind.Rejected, outcomes[0].Kind);
        Assert.Equal(SyncEventErrorCodes.UnknownCamera, outcomes[0].ErrorCode);
    }

    [Fact]
    public async Task SyncEventsAsync_NonGuidCameraId_FallsBackToLegacyNameResolution()
    {
        // FS-11 §10 transitional support: a non-GUID wire value (an already-deployed legacy Agent's
        // static WDA_DETECTION_CAMERA_ID) must still resolve via the deprecated Camera.Name match.
        await SeedBranchWithCameraAsync(cameraName: "camera1");
        var item = MakeItem(cameraId: "camera1");

        var outcomes = await _service.SyncEventsAsync(_deviceId, _branchId, [item]);

        Assert.Equal(SyncEventOutcomeKind.Accepted, outcomes[0].Kind);
    }

    // --- Field validation (FS-06 §4.3) ---

    [Theory]
    [InlineData(-0.01)]
    [InlineData(1.01)]
    public async Task SyncEventsAsync_ConfidenceOutOfBounds_IsRejected(double confidence)
    {
        await SeedBranchWithCameraAsync();
        var item = MakeItem(confidence: confidence);

        var outcomes = await _service.SyncEventsAsync(_deviceId, _branchId, [item]);

        Assert.Equal(SyncEventOutcomeKind.Rejected, outcomes[0].Kind);
        Assert.Equal(SyncEventErrorCodes.InvalidConfidence, outcomes[0].ErrorCode);
        Assert.Empty(await _dbContext.Alerts.Where(a => a.EventId == item.EventId).ToListAsync());
    }

    [Fact]
    public async Task SyncEventsAsync_BoundingBoxExceedsFrame_IsRejected()
    {
        await SeedBranchWithCameraAsync();
        var item = MakeItem(frameWidth: 100, frameHeight: 100, bboxLeft: 50, bboxTop: 50, bboxWidth: 100, bboxHeight: 100);

        var outcomes = await _service.SyncEventsAsync(_deviceId, _branchId, [item]);

        Assert.Equal(SyncEventOutcomeKind.Rejected, outcomes[0].Kind);
        Assert.Equal(SyncEventErrorCodes.InvalidBoundingBox, outcomes[0].ErrorCode);
    }

    [Fact]
    public async Task SyncEventsAsync_NegativeFrameNumber_IsRejected()
    {
        await SeedBranchWithCameraAsync();
        var item = MakeItem(frameNumber: -1);

        var outcomes = await _service.SyncEventsAsync(_deviceId, _branchId, [item]);

        Assert.Equal(SyncEventOutcomeKind.Rejected, outcomes[0].Kind);
        Assert.Equal(SyncEventErrorCodes.InvalidBoundingBox, outcomes[0].ErrorCode);
    }

    // --- Idempotency (FS-06 §5.2, ADR-012, OI-12) ---

    [Fact]
    public async Task SyncEventsAsync_DuplicateEventIdRetry_ReturnsDuplicate_AndCreatesNoSecondAlert()
    {
        await SeedBranchWithCameraAsync();
        var item = MakeItem();

        var first = await _service.SyncEventsAsync(_deviceId, _branchId, [item]);
        Assert.Equal(SyncEventOutcomeKind.Accepted, first[0].Kind);

        var second = await _service.SyncEventsAsync(_deviceId, _branchId, [item]);

        Assert.Equal(SyncEventOutcomeKind.Duplicate, second[0].Kind);
        Assert.Equal(first[0].AlertId, second[0].AlertId);

        var alerts = await _dbContext.Alerts.AsNoTracking()
            .Where(a => a.DeviceId == _deviceId && a.EventId == item.EventId).ToListAsync();
        Assert.Single(alerts);
    }

    [Fact]
    public async Task SyncEventsAsync_RetriedEventWithConflictingData_IsRejectedWithEventDataConflict_NotSilentlyAccepted()
    {
        await SeedBranchWithCameraAsync();
        var eventId = Guid.NewGuid();
        var original = MakeItem(eventId: eventId, confidence: 0.9);

        await _service.SyncEventsAsync(_deviceId, _branchId, [original]);

        // Same EventId, different confidence — an immutable field diverges.
        var conflicting = MakeItem(eventId: eventId, confidence: 0.2);
        var outcomes = await _service.SyncEventsAsync(_deviceId, _branchId, [conflicting]);

        Assert.Equal(SyncEventOutcomeKind.Rejected, outcomes[0].Kind);
        Assert.Equal(SyncEventErrorCodes.EventDataConflict, outcomes[0].ErrorCode);

        var alerts = await _dbContext.Alerts.AsNoTracking()
            .Where(a => a.DeviceId == _deviceId && a.EventId == eventId).ToListAsync();
        Assert.Single(alerts);
        Assert.Equal(0.9, alerts[0].Confidence);
    }

    [Fact]
    public async Task SyncEventsAsync_ConcurrentDuplicateSubmissions_CreateExactlyOneAlert()
    {
        await SeedBranchWithCameraAsync();
        var item = MakeItem();

        // Two independent AlertSyncService instances (their own DbContext, as two concurrent HTTP
        // requests would each get their own scoped DbContext), racing to insert the same EventId. The
        // unique index — not application logic — must be what prevents two Alerts (FS-06 §5.2, OI-12).
        var options = new DbContextOptionsBuilder<WeaponDetectionDbContext>()
            .UseSqlServer(_dbContext.Database.GetConnectionString())
            .Options;
        using var secondContext = new WeaponDetectionDbContext(options);
        var secondService = CreateService(secondContext);

        var task1 = _service.SyncEventsAsync(_deviceId, _branchId, [item]);
        var task2 = secondService.SyncEventsAsync(_deviceId, _branchId, [item]);
        var results = await Task.WhenAll(task1, task2);

        var kinds = results.Select(r => r[0].Kind).OrderBy(k => k).ToList();
        Assert.Contains(SyncEventOutcomeKind.Accepted, kinds);
        Assert.Contains(SyncEventOutcomeKind.Duplicate, kinds);

        var alerts = await _dbContext.Alerts.AsNoTracking()
            .Where(a => a.DeviceId == _deviceId && a.EventId == item.EventId).ToListAsync();
        Assert.Single(alerts);
    }

    // --- Mixed batch (Phase 13 item covering per-item outcomes) ---

    [Fact]
    public async Task SyncEventsAsync_MixedBatch_ReturnsCorrectPerItemOutcomes_AndOneInvalidEventDoesNotRollBackTheOthers()
    {
        await SeedBranchWithCameraAsync();

        var validItem = MakeItem();
        var invalidConfidenceItem = MakeItem(confidence: 5.0);
        var unknownCameraItem = MakeItem(cameraId: "no-such-camera");

        var outcomes = await _service.SyncEventsAsync(
            _deviceId, _branchId, [validItem, invalidConfidenceItem, unknownCameraItem]);

        Assert.Equal(3, outcomes.Count);
        Assert.Equal(SyncEventOutcomeKind.Accepted, outcomes.Single(o => o.EventId == validItem.EventId).Kind);
        Assert.Equal(
            SyncEventOutcomeKind.Rejected, outcomes.Single(o => o.EventId == invalidConfidenceItem.EventId).Kind);
        Assert.Equal(
            SyncEventOutcomeKind.Rejected, outcomes.Single(o => o.EventId == unknownCameraItem.EventId).Kind);

        var validAlert = await _dbContext.Alerts.AsNoTracking()
            .SingleOrDefaultAsync(a => a.EventId == validItem.EventId);
        Assert.NotNull(validAlert);
    }

    // --- Batch transaction boundary ---

    [Fact]
    public async Task SyncEventsAsync_DeviceIdOnPersistedAlert_IsTheAuthenticatedExternalDeviceId()
    {
        await SeedBranchWithCameraAsync();
        var item = MakeItem();

        await _service.SyncEventsAsync(_deviceId, _branchId, [item]);

        var alert = await _dbContext.Alerts.AsNoTracking().SingleAsync(a => a.EventId == item.EventId);
        Assert.Equal(_deviceId, alert.DeviceId);
    }

    // --- Branch daily Alert quota (FS-09 §7/§8, IP-11 T-174/T-175) ---

    [Fact]
    public async Task SyncEventsAsync_FirstNEventsUpToMaximum_AreAllAccepted()
    {
        await SeedBranchWithCameraAsync();
        var service = CreateService(_dbContext, maximumPerBranchPerDay: 3);
        var items = Enumerable.Range(0, 3).Select(_ => MakeItem()).ToList();

        var outcomes = await service.SyncEventsAsync(_deviceId, _branchId, items);

        Assert.All(outcomes, o => Assert.Equal(SyncEventOutcomeKind.Accepted, o.Kind));
        Assert.Equal(3, await _dbContext.Alerts.CountAsync(a => a.DeviceId == _deviceId));
    }

    [Fact]
    public async Task SyncEventsAsync_EventBeyondMaximum_ReturnsQuotaExceeded_CreatesNoAlert_AndAcceptedCountStaysAtMaximum()
    {
        await SeedBranchWithCameraAsync();
        var service = CreateService(_dbContext, maximumPerBranchPerDay: 2);
        var accepted = Enumerable.Range(0, 2).Select(_ => MakeItem()).ToList();
        var overLimit = MakeItem();

        var acceptedOutcomes = await service.SyncEventsAsync(_deviceId, _branchId, accepted);
        Assert.All(acceptedOutcomes, o => Assert.Equal(SyncEventOutcomeKind.Accepted, o.Kind));

        var overLimitOutcome = (await service.SyncEventsAsync(_deviceId, _branchId, [overLimit]))[0];

        Assert.Equal(SyncEventOutcomeKind.QuotaExceeded, overLimitOutcome.Kind);
        Assert.Null(overLimitOutcome.AlertId);
        Assert.Equal(SyncEventErrorCodes.BranchDailyAlertQuotaReached, overLimitOutcome.ErrorCode);
        Assert.NotNull(overLimitOutcome.Quota);
        Assert.Equal(2, overLimitOutcome.Quota!.Maximum);

        Assert.Equal(2, await _dbContext.Alerts.CountAsync(a => a.DeviceId == _deviceId));
        Assert.False(await _dbContext.Alerts.AnyAsync(a => a.EventId == overLimit.EventId));

        var quotaRow = await _dbContext.BranchDailyAlertQuotas.AsNoTracking()
            .SingleAsync(q => q.BranchId == _branchId);
        Assert.Equal(2, quotaRow.AcceptedAlertCount);
    }

    [Fact]
    public async Task SyncEventsAsync_QuotaSharedAcrossMultipleDevicesOnSameBranch()
    {
        await SeedBranchWithCameraAsync();
        var service = CreateService(_dbContext, maximumPerBranchPerDay: 1);
        var otherDeviceId = Guid.NewGuid();

        var first = await service.SyncEventsAsync(_deviceId, _branchId, [MakeItem()]);
        var second = await service.SyncEventsAsync(otherDeviceId, _branchId, [MakeItem()]);

        Assert.Equal(SyncEventOutcomeKind.Accepted, first[0].Kind);
        Assert.Equal(SyncEventOutcomeKind.QuotaExceeded, second[0].Kind);
    }

    [Fact]
    public async Task SyncEventsAsync_QuotaSharedAcrossMultipleCamerasOnSameBranch()
    {
        await SeedBranchWithCameraAsync(cameraName: "camera1", sourceOrder: 0);
        await SeedBranchWithCameraAsync(cameraName: "camera2", branchId: _branchId, sourceOrder: 1);
        var service = CreateService(_dbContext, maximumPerBranchPerDay: 1);

        var first = await service.SyncEventsAsync(_deviceId, _branchId, [MakeItem(cameraId: "camera1")]);
        var second = await service.SyncEventsAsync(_deviceId, _branchId, [MakeItem(cameraId: "camera2")]);

        Assert.Equal(SyncEventOutcomeKind.Accepted, first[0].Kind);
        Assert.Equal(SyncEventOutcomeKind.QuotaExceeded, second[0].Kind);
    }

    [Fact]
    public async Task SyncEventsAsync_DifferentBranches_HaveIndependentQuotas()
    {
        await SeedBranchWithCameraAsync();
        var firstBranchId = _branchId;
        var otherDeviceId = Guid.NewGuid();
        var otherBranchCamera = await SeedBranchWithCameraAsync(cameraName: "camera1", branchId: null);
        var otherBranchId = _branchId;
        var service = CreateService(_dbContext, maximumPerBranchPerDay: 1);

        var firstBranchResult = await service.SyncEventsAsync(_deviceId, firstBranchId, [MakeItem()]);
        var otherBranchResult = await service.SyncEventsAsync(otherDeviceId, otherBranchId, [MakeItem()]);

        Assert.Equal(SyncEventOutcomeKind.Accepted, firstBranchResult[0].Kind);
        Assert.Equal(SyncEventOutcomeKind.Accepted, otherBranchResult[0].Kind);
        Assert.NotEqual(firstBranchId, otherBranchId);
        Assert.NotNull(otherBranchCamera);
    }

    [Fact]
    public async Task SyncEventsAsync_DuplicateRetryOfAcceptedEvent_DoesNotConsumeQuota()
    {
        await SeedBranchWithCameraAsync();
        var service = CreateService(_dbContext, maximumPerBranchPerDay: 1);
        var item = MakeItem();

        await service.SyncEventsAsync(_deviceId, _branchId, [item]);
        var retry = await service.SyncEventsAsync(_deviceId, _branchId, [item]);

        Assert.Equal(SyncEventOutcomeKind.Duplicate, retry[0].Kind);

        var quotaRow = await _dbContext.BranchDailyAlertQuotas.AsNoTracking().SingleAsync(q => q.BranchId == _branchId);
        Assert.Equal(1, quotaRow.AcceptedAlertCount);

        // The quota still has room for a genuinely new event, proving the duplicate never consumed it.
        var next = await service.SyncEventsAsync(_deviceId, _branchId, [MakeItem()]);
        Assert.Equal(SyncEventOutcomeKind.QuotaExceeded, next[0].Kind);
    }

    [Fact]
    public async Task SyncEventsAsync_RetryingAQuotaSuppressedEventId_DoesNotIncrementSuppressionCountsTwice()
    {
        await SeedBranchWithCameraAsync();
        var service = CreateService(_dbContext, maximumPerBranchPerDay: 0);
        var item = MakeItem();

        var first = await service.SyncEventsAsync(_deviceId, _branchId, [item]);
        var retry = await service.SyncEventsAsync(_deviceId, _branchId, [item]);

        Assert.Equal(SyncEventOutcomeKind.QuotaExceeded, first[0].Kind);
        Assert.Equal(SyncEventOutcomeKind.QuotaExceeded, retry[0].Kind);

        var quotaRow = await _dbContext.BranchDailyAlertQuotas.AsNoTracking().SingleAsync(q => q.BranchId == _branchId);
        Assert.Equal(1, quotaRow.SuppressedDetectionCount);

        Assert.Equal(1, await _dbContext.SuppressedDetectionEvents.CountAsync(
            s => s.DeviceId == _deviceId && s.EventId == item.EventId));
    }

    [Fact]
    public async Task SyncEventsAsync_ConcurrentSubmissionsAtTheBoundary_NeverCreateMoreThanTheMaximum()
    {
        await SeedBranchWithCameraAsync();
        const int maximum = 15;

        var options = new DbContextOptionsBuilder<WeaponDetectionDbContext>()
            .UseSqlServer(_dbContext.Database.GetConnectionString())
            .Options;

        // 20 concurrent submissions (Alerts 1 through ~20 racing for 15 slots), each its own
        // DbContext/service — mirrors how 20 concurrent HTTP requests would each get their own scoped
        // DbContext. The atomic conditional UPDATE in EnforceQuotaAndInsertAsync — not any
        // application-level lock — must be what prevents Alert 16 (FS-09 §7, task Phase 8 item 12).
        var tasks = Enumerable.Range(0, 20).Select(async _ =>
        {
            await using var context = new WeaponDetectionDbContext(options);
            var service = CreateService(context, maximumPerBranchPerDay: maximum);
            return await service.SyncEventsAsync(_deviceId, _branchId, [MakeItem()]);
        });

        var results = await Task.WhenAll(tasks);
        var kinds = results.Select(r => r[0].Kind).ToList();

        Assert.Equal(maximum, kinds.Count(k => k == SyncEventOutcomeKind.Accepted));
        Assert.Equal(20 - maximum, kinds.Count(k => k == SyncEventOutcomeKind.QuotaExceeded));

        var alertCount = await _dbContext.Alerts.CountAsync(a => a.DeviceId == _deviceId);
        Assert.Equal(maximum, alertCount);
    }

    [Fact]
    public async Task SyncEventsAsync_MixedBatch_CanContainAccepted_Duplicate_AndQuotaExceeded()
    {
        await SeedBranchWithCameraAsync();
        var service = CreateService(_dbContext, maximumPerBranchPerDay: 1);
        var alreadyAccepted = MakeItem();
        await service.SyncEventsAsync(_deviceId, _branchId, [alreadyAccepted]);

        var duplicateRetry = alreadyAccepted;
        var overLimit = MakeItem();

        var outcomes = await service.SyncEventsAsync(_deviceId, _branchId, [duplicateRetry, overLimit]);

        Assert.Equal(SyncEventOutcomeKind.Duplicate, outcomes.Single(o => o.EventId == duplicateRetry.EventId).Kind);
        Assert.Equal(SyncEventOutcomeKind.QuotaExceeded, outcomes.Single(o => o.EventId == overLimit.EventId).Kind);
    }

    [Fact]
    public async Task SyncEventsAsync_BranchLocalDateBoundary_UsesConfiguredTimeZone_NotUtc()
    {
        var camera = await SeedBranchWithCameraAsync();
        var branch = await _dbContext.Branches.SingleAsync(b => b.BranchId == _branchId);
        branch.UpdateTimeZone("Asia/Karachi"); // UTC+05:00, no DST.
        await _dbContext.SaveChangesAsync();

        var service = CreateService(_dbContext, maximumPerBranchPerDay: 1);

        // 2026-07-24T20:30:00Z is 2026-07-25T01:30:00 in Asia/Karachi — the next branch-local day.
        var lateUtc = new DateTime(2026, 7, 24, 20, 30, 0, DateTimeKind.Utc);
        var firstDayItem = MakeItem(detectedAtUtc: DetectedAtUtc);
        var nextLocalDayItem = MakeItem(detectedAtUtc: lateUtc);

        var first = await service.SyncEventsAsync(_deviceId, _branchId, [firstDayItem]);
        var second = await service.SyncEventsAsync(_deviceId, _branchId, [nextLocalDayItem]);

        Assert.Equal(SyncEventOutcomeKind.Accepted, first[0].Kind);
        // Falls on the next Branch-local calendar day, so the quota has reset — not exhausted by the
        // first event, even though both events landed within the same UTC calendar day.
        Assert.Equal(SyncEventOutcomeKind.Accepted, second[0].Kind);
        Assert.NotNull(camera);
    }

    [Fact]
    public async Task SyncEventsAsync_NextBranchLocalDay_ResetsTheQuota()
    {
        await SeedBranchWithCameraAsync();
        var service = CreateService(_dbContext, maximumPerBranchPerDay: 1);

        var day1 = await service.SyncEventsAsync(_deviceId, _branchId, [MakeItem(detectedAtUtc: DetectedAtUtc)]);
        var day1OverLimit = await service.SyncEventsAsync(
            _deviceId, _branchId, [MakeItem(detectedAtUtc: DetectedAtUtc.AddHours(1))]);
        var day2 = await service.SyncEventsAsync(
            _deviceId, _branchId, [MakeItem(detectedAtUtc: DetectedAtUtc.AddDays(1))]);

        Assert.Equal(SyncEventOutcomeKind.Accepted, day1[0].Kind);
        Assert.Equal(SyncEventOutcomeKind.QuotaExceeded, day1OverLimit[0].Kind);
        Assert.Equal(SyncEventOutcomeKind.Accepted, day2[0].Kind);
    }

    [Fact]
    public async Task SyncEventsAsync_SuppressionAggregateCounts_AreAccurate()
    {
        await SeedBranchWithCameraAsync();
        var service = CreateService(_dbContext, maximumPerBranchPerDay: 1);

        await service.SyncEventsAsync(_deviceId, _branchId, [MakeItem()]);
        await service.SyncEventsAsync(_deviceId, _branchId, [MakeItem()]);
        await service.SyncEventsAsync(_deviceId, _branchId, [MakeItem()]);

        var quotaRow = await _dbContext.BranchDailyAlertQuotas.AsNoTracking().SingleAsync(q => q.BranchId == _branchId);
        Assert.Equal(1, quotaRow.AcceptedAlertCount);
        Assert.Equal(2, quotaRow.SuppressedDetectionCount);
        Assert.NotNull(quotaRow.FirstSuppressedAtUtc);
        Assert.NotNull(quotaRow.LastSuppressedAtUtc);
    }

    [Fact]
    public async Task SyncEventsAsync_ClassSpecificSuppressionCounts_AreAccurate()
    {
        await SeedBranchWithCameraAsync();
        var service = CreateService(_dbContext, maximumPerBranchPerDay: 0);

        await service.SyncEventsAsync(_deviceId, _branchId, [MakeItem(className: "gun")]);
        await service.SyncEventsAsync(_deviceId, _branchId, [MakeItem(className: "knife")]);
        await service.SyncEventsAsync(_deviceId, _branchId, [MakeItem(className: "gun")]);

        var quotaRow = await _dbContext.BranchDailyAlertQuotas.AsNoTracking().SingleAsync(q => q.BranchId == _branchId);
        Assert.Equal(3, quotaRow.SuppressedDetectionCount);
        Assert.Equal(2, quotaRow.GunSuppressedCount);
        Assert.Equal(1, quotaRow.KnifeSuppressedCount);
    }

    [Fact]
    public async Task SyncEventsAsync_QuotaDisabled_AcceptsEventsBeyondTheConfiguredMaximum()
    {
        await SeedBranchWithCameraAsync();
        var service = CreateService(_dbContext, maximumPerBranchPerDay: 1, enabled: false);

        var first = await service.SyncEventsAsync(_deviceId, _branchId, [MakeItem()]);
        var second = await service.SyncEventsAsync(_deviceId, _branchId, [MakeItem()]);

        Assert.Equal(SyncEventOutcomeKind.Accepted, first[0].Kind);
        Assert.Equal(SyncEventOutcomeKind.Accepted, second[0].Kind);
        Assert.Empty(_dbContext.BranchDailyAlertQuotas.AsNoTracking());
    }
}

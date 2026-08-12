using System;
using System.Collections.Generic;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.EntityFrameworkCore;
using WeaponDetection.Application.Interfaces;
using WeaponDetection.Domain;
using WeaponDetection.Infrastructure.Persistence;
using WeaponDetection.Infrastructure.Services;
using WeaponDetection.Infrastructure.Storage;
using Xunit;

namespace WeaponDetection.IntegrationTests.Services;

// Verifies AlertSnapshotUploadService (FS-08 §9, IP-10 T-153/T-156) against a real SQL Server
// database and a real filesystem-backed IAlertSnapshotStorage (temp directory per test) — mirroring
// AlertSyncServiceTests' rationale (relational/ownership behavior EF Core InMemory/SQLite would not
// faithfully reproduce). Each test gets its own freshly migrated, empty database and its own temp
// snapshot storage directory.
public class AlertSnapshotUploadServiceTests : IDisposable
{
    private readonly WeaponDetectionDbContext _dbContext;
    private readonly string _storagePath;
    private readonly FileSystemAlertSnapshotStorage _storage;
    private readonly AlertSnapshotUploadService _service;

    public AlertSnapshotUploadServiceTests()
    {
        var connectionString =
            $"Server=localhost\\SQLEXPRESS;Database=WeaponDetectionSnapshotUploadServiceTests_{Guid.NewGuid():N};" +
            "Trusted_Connection=True;TrustServerCertificate=True;";

        var options = new DbContextOptionsBuilder<WeaponDetectionDbContext>()
            .UseSqlServer(connectionString)
            .Options;

        _dbContext = new WeaponDetectionDbContext(options);
        _dbContext.Database.Migrate();

        _storagePath = Path.Combine(Path.GetTempPath(), "wd-snapshot-upload-service-test-" + Guid.NewGuid().ToString("N"));
        _storage = new FileSystemAlertSnapshotStorage(
            Microsoft.Extensions.Options.Options.Create(
                new AlertSnapshotStorageOptions { StoragePath = _storagePath }));

        _service = new AlertSnapshotUploadService(_dbContext, _storage, TimeProvider.System);
    }

    public void Dispose()
    {
        _dbContext.Database.EnsureDeleted();
        _dbContext.Dispose();

        if (Directory.Exists(_storagePath))
        {
            Directory.Delete(_storagePath, recursive: true);
        }
    }

    private async Task<Guid> SeedBranchWithCameraAsync(string name = "Downtown Branch")
    {
        var branch = new Branch(name, "1 High Street", "ops@example.local");
        _dbContext.Branches.Add(branch);
        var camera = new Camera(branch.BranchId, "camera1", "rtsp://camera.example.invalid:554/stream1", $"cam-{Guid.NewGuid():N}");
        _dbContext.Cameras.Add(camera);
        await _dbContext.SaveChangesAsync();
        return branch.BranchId;
    }

    private async Task<(Guid BranchId, Alert Alert)> SeedAlertAsync(Guid? eventId = null)
    {
        var branch = new Branch("Downtown Branch " + Guid.NewGuid(), "1 High Street", "ops@example.local");
        _dbContext.Branches.Add(branch);
        var camera = new Camera(branch.BranchId, "camera1", "rtsp://camera.example.invalid:554/stream1", $"cam-{Guid.NewGuid():N}");
        _dbContext.Cameras.Add(camera);
        await _dbContext.SaveChangesAsync();

        var alert = new Alert(
            Guid.NewGuid(),
            eventId ?? Guid.NewGuid(),
            camera.CameraId,
            new DateTime(2026, 7, 28, 12, 0, 0, DateTimeKind.Utc),
            new DateTime(2026, 7, 28, 12, 0, 1, DateTimeKind.Utc),
            classId: 0,
            className: "gun",
            confidence: 0.9,
            frameNumber: 1,
            frameWidth: 1280,
            frameHeight: 720,
            bboxLeft: 0,
            bboxTop: 0,
            bboxWidth: 10,
            bboxHeight: 10);
        _dbContext.Alerts.Add(alert);
        await _dbContext.SaveChangesAsync();

        return (branch.BranchId, alert);
    }

    // A minimal, structurally valid JPEG (SOI + SOF0 + EOI) — sufficient for JpegInspector, which
    // never fully decodes pixel data (see JpegInspectorTests).
    private static byte[] BuildJpeg(int marker = 1)
    {
        return
        [
            0xFF, 0xD8,
            0xFF, 0xC0,
            0x00, 0x0B,
            0x08,
            0x02, 0xD0, // height = 720
            0x05, 0x00, // width = 1280
            0x01,
            0x01, 0x11, 0x00,
            (byte)marker, // a differentiating payload byte so distinct "files" hash differently
            0xFF, 0xD9,
        ];
    }

    private static SnapshotUploadRequest BuildRequest(
        Guid alertId, Guid branchId, Guid eventId, byte[] content,
        string contentType = "image/jpeg", string? claimedSha256 = null) =>
        new(alertId, branchId, eventId, contentType, claimedSha256, content.Length, new MemoryStream(content));

    // --- Happy path ---

    [Fact]
    public async Task UploadAsync_ValidUpload_ReturnsAccepted_WithNonNullSnapshotReference()
    {
        var (branchId, alert) = await SeedAlertAsync();
        var content = BuildJpeg();

        var outcome = await _service.UploadAsync(BuildRequest(alert.AlertId, branchId, alert.EventId, content));

        Assert.Equal(SnapshotUploadOutcomeKind.Accepted, outcome.Kind);
        Assert.NotNull(outcome.SnapshotReference);
    }

    [Fact]
    public async Task UploadAsync_ValidUpload_PersistsSnapshotFieldsOnTheAlert_WithMatchingStoredBytes()
    {
        var (branchId, alert) = await SeedAlertAsync();
        var content = BuildJpeg();

        var outcome = await _service.UploadAsync(BuildRequest(alert.AlertId, branchId, alert.EventId, content));

        var persisted = await _dbContext.Alerts.AsNoTracking().SingleAsync(a => a.AlertId == alert.AlertId);
        Assert.Equal(outcome.SnapshotReference, persisted.SnapshotReference);
        Assert.NotNull(persisted.SnapshotSha256);
        Assert.Equal("image/jpeg", persisted.SnapshotContentType);
        Assert.Equal(content.Length, persisted.SnapshotSizeBytes);
        Assert.NotNull(persisted.SnapshotReceivedAtUtc);

        var storedBytes = await _storage.ReadAsync(alert.AlertId);
        Assert.Equal(content, storedBytes);
    }

    [Fact]
    public async Task UploadAsync_ValidUpload_NeverReturnsAFilesystemPath()
    {
        var (branchId, alert) = await SeedAlertAsync();

        var outcome = await _service.UploadAsync(BuildRequest(alert.AlertId, branchId, alert.EventId, BuildJpeg()));

        Assert.DoesNotContain(_storagePath, outcome.SnapshotReference);
        Assert.DoesNotContain(":\\", outcome.SnapshotReference ?? string.Empty);
    }

    // --- Duplicate / conflict (FS-08 §9) ---

    [Fact]
    public async Task UploadAsync_SameFileRetried_ReturnsDuplicate_WithoutCreatingASecondFile()
    {
        var (branchId, alert) = await SeedAlertAsync();
        var content = BuildJpeg();

        var first = await _service.UploadAsync(BuildRequest(alert.AlertId, branchId, alert.EventId, content));
        var second = await _service.UploadAsync(BuildRequest(alert.AlertId, branchId, alert.EventId, content));

        Assert.Equal(SnapshotUploadOutcomeKind.Accepted, first.Kind);
        Assert.Equal(SnapshotUploadOutcomeKind.Duplicate, second.Kind);
        Assert.Equal(first.SnapshotReference, second.SnapshotReference);
        Assert.Single(Directory.GetFiles(_storagePath));
    }

    [Fact]
    public async Task UploadAsync_DifferentFileForSameAlert_ReturnsConflict_AndDoesNotOverwriteTheOriginal()
    {
        var (branchId, alert) = await SeedAlertAsync();
        var original = BuildJpeg(marker: 1);
        var different = BuildJpeg(marker: 2);

        var first = await _service.UploadAsync(BuildRequest(alert.AlertId, branchId, alert.EventId, original));
        var second = await _service.UploadAsync(BuildRequest(alert.AlertId, branchId, alert.EventId, different));

        Assert.Equal(SnapshotUploadOutcomeKind.Accepted, first.Kind);
        Assert.Equal(SnapshotUploadOutcomeKind.Conflict, second.Kind);

        var persisted = await _dbContext.Alerts.AsNoTracking().SingleAsync(a => a.AlertId == alert.AlertId);
        var storedBytes = await _storage.ReadAsync(alert.AlertId);
        Assert.Equal(original, storedBytes);
        Assert.Equal(first.SnapshotReference, persisted.SnapshotReference);
    }

    // --- Ownership / not-found (non-disclosure, FS-08 §9) ---

    [Fact]
    public async Task UploadAsync_UnknownAlertId_ReturnsAlertNotOwnedOrFound()
    {
        var branchId = await SeedBranchWithCameraAsync();

        var outcome = await _service.UploadAsync(
            BuildRequest(Guid.NewGuid(), branchId, Guid.NewGuid(), BuildJpeg()));

        Assert.Equal(SnapshotUploadOutcomeKind.AlertNotOwnedOrFound, outcome.Kind);
        Assert.Equal(SnapshotUploadErrorCodes.NotFound, outcome.ErrorCode);
    }

    [Fact]
    public async Task UploadAsync_AlertBelongsToADifferentBranch_ReturnsAlertNotOwnedOrFound_NotSomeOtherError()
    {
        var (_, alert) = await SeedAlertAsync();
        var otherBranchId = await SeedBranchWithCameraAsync("Some Other Branch " + Guid.NewGuid());

        var outcome = await _service.UploadAsync(
            BuildRequest(alert.AlertId, otherBranchId, alert.EventId, BuildJpeg()));

        Assert.Equal(SnapshotUploadOutcomeKind.AlertNotOwnedOrFound, outcome.Kind);

        var persisted = await _dbContext.Alerts.AsNoTracking().SingleAsync(a => a.AlertId == alert.AlertId);
        Assert.Null(persisted.SnapshotReference);
    }

    // --- Field-level validation (FS-08 §9's validation order) ---

    [Fact]
    public async Task UploadAsync_EventIdDoesNotMatchAlert_ReturnsEventIdMismatch()
    {
        var (branchId, alert) = await SeedAlertAsync();

        var outcome = await _service.UploadAsync(
            BuildRequest(alert.AlertId, branchId, Guid.NewGuid(), BuildJpeg()));

        Assert.Equal(SnapshotUploadOutcomeKind.EventIdMismatch, outcome.Kind);
    }

    [Fact]
    public async Task UploadAsync_NullContent_ReturnsMissingFile()
    {
        var (branchId, alert) = await SeedAlertAsync();
        var request = new SnapshotUploadRequest(
            alert.AlertId, branchId, alert.EventId, "image/jpeg", null, 0, null);

        var outcome = await _service.UploadAsync(request);

        Assert.Equal(SnapshotUploadOutcomeKind.MissingFile, outcome.Kind);
    }

    [Fact]
    public async Task UploadAsync_ZeroLengthContent_ReturnsMissingFile()
    {
        var (branchId, alert) = await SeedAlertAsync();

        var outcome = await _service.UploadAsync(
            BuildRequest(alert.AlertId, branchId, alert.EventId, Array.Empty<byte>()));

        Assert.Equal(SnapshotUploadOutcomeKind.MissingFile, outcome.Kind);
    }

    [Fact]
    public async Task UploadAsync_DeclaredContentLengthExceedsTheConfiguredBound_ReturnsOversized()
    {
        var (branchId, alert) = await SeedAlertAsync();
        var oversized = new byte[AlertSnapshotUploadService.MaxSnapshotSizeBytes + 1];

        var outcome = await _service.UploadAsync(
            BuildRequest(alert.AlertId, branchId, alert.EventId, oversized));

        Assert.Equal(SnapshotUploadOutcomeKind.Oversized, outcome.Kind);
    }

    [Fact]
    public async Task UploadAsync_UnsupportedContentType_ReturnsUnsupportedMediaType()
    {
        var (branchId, alert) = await SeedAlertAsync();

        var outcome = await _service.UploadAsync(
            BuildRequest(alert.AlertId, branchId, alert.EventId, BuildJpeg(), contentType: "image/png"));

        Assert.Equal(SnapshotUploadOutcomeKind.UnsupportedMediaType, outcome.Kind);
    }

    [Fact]
    public async Task UploadAsync_NonJpegBytesClaimingImageJpeg_ReturnsMalformedImage()
    {
        var (branchId, alert) = await SeedAlertAsync();
        var notAJpeg = System.Text.Encoding.ASCII.GetBytes("this is not a jpeg file at all");

        var outcome = await _service.UploadAsync(
            BuildRequest(alert.AlertId, branchId, alert.EventId, notAJpeg));

        Assert.Equal(SnapshotUploadOutcomeKind.MalformedImage, outcome.Kind);
    }

    [Fact]
    public async Task UploadAsync_TruncatedJpegMissingEoi_ReturnsMalformedImage()
    {
        var (branchId, alert) = await SeedAlertAsync();
        var truncated = BuildJpeg()[..^2];

        var outcome = await _service.UploadAsync(
            BuildRequest(alert.AlertId, branchId, alert.EventId, truncated));

        Assert.Equal(SnapshotUploadOutcomeKind.MalformedImage, outcome.Kind);
    }

    [Fact]
    public async Task UploadAsync_ClaimedSha256DoesNotMatchActualBytes_ReturnsMalformedImage()
    {
        var (branchId, alert) = await SeedAlertAsync();

        var outcome = await _service.UploadAsync(BuildRequest(
            alert.AlertId, branchId, alert.EventId, BuildJpeg(),
            claimedSha256: new string('0', 64)));

        Assert.Equal(SnapshotUploadOutcomeKind.MalformedImage, outcome.Kind);
    }

    [Fact]
    public async Task UploadAsync_ClaimedSha256MatchingActualBytes_IsAccepted()
    {
        var (branchId, alert) = await SeedAlertAsync();
        var content = BuildJpeg();
        var correctSha256 = Convert.ToHexString(
            System.Security.Cryptography.SHA256.HashData(content)).ToLowerInvariant();

        var outcome = await _service.UploadAsync(BuildRequest(
            alert.AlertId, branchId, alert.EventId, content, claimedSha256: correctSha256));

        Assert.Equal(SnapshotUploadOutcomeKind.Accepted, outcome.Kind);
    }

    // --- Storage failure leaves no partial state (FS-08 §9) ---

    private sealed class ThrowingAlertSnapshotStorage : IAlertSnapshotStorage
    {
        public string BuildReference(Guid alertId) => $"{alertId:D}.jpg";

        public Task<string> SaveAsync(Guid alertId, Stream content, CancellationToken cancellationToken = default) =>
            throw new IOException("Simulated storage failure.");

        public Task<byte[]?> ReadAsync(Guid alertId, CancellationToken cancellationToken = default) =>
            Task.FromResult<byte[]?>(null);

        public Task<bool> ExistsAsync(Guid alertId, CancellationToken cancellationToken = default) =>
            Task.FromResult(false);
    }

    [Fact]
    public async Task UploadAsync_StorageWriteFails_PropagatesTheFailure_AndLeavesSnapshotReferenceNull()
    {
        var (branchId, alert) = await SeedAlertAsync();
        var service = new AlertSnapshotUploadService(
            _dbContext, new ThrowingAlertSnapshotStorage(), TimeProvider.System);

        await Assert.ThrowsAsync<IOException>(
            () => service.UploadAsync(BuildRequest(alert.AlertId, branchId, alert.EventId, BuildJpeg())));

        var persisted = await _dbContext.Alerts.AsNoTracking().SingleAsync(a => a.AlertId == alert.AlertId);
        Assert.Null(persisted.SnapshotReference);
    }
}

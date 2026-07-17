using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using Microsoft.EntityFrameworkCore;
using WeaponDetection.Application.Interfaces;
using WeaponDetection.Domain;
using WeaponDetection.Infrastructure.Persistence;
using WeaponDetection.Infrastructure.Security;
using WeaponDetection.Infrastructure.Services;
using Xunit;

namespace WeaponDetection.IntegrationTests.Services;

// Verifies BranchService.UpdateBranchAsync against a real SQL Server database (FS-03 §5.1, §5.2,
// §5.3, §11; AC-1–AC-9). The transactional-rollback and preservation claims cannot be verified on
// EF Core InMemory or SQLite (IP-01 §9), so they run here, mirroring BranchServiceTests. Each test
// gets its own freshly migrated, empty database.
//
// No test prints a plaintext Activation Key, its secret half, a secret hash, or a protected shared
// secret to console/log output; assertions compare values in memory only. Every RTSP value is a
// non-routable placeholder (.invalid, RFC 2606).
public class BranchUpdateServiceTests : IDisposable
{
    private readonly WeaponDetectionDbContext _dbContext;
    private readonly BranchService _branchService;
    private readonly DeviceService _deviceService;

    public BranchUpdateServiceTests()
    {
        var connectionString =
            $"Server=localhost\\SQLEXPRESS;Database=WeaponDetectionBranchUpdateTests_{Guid.NewGuid():N};" +
            "Trusted_Connection=True;TrustServerCertificate=True;";

        var options = new DbContextOptionsBuilder<WeaponDetectionDbContext>()
            .UseSqlServer(connectionString)
            .Options;

        _dbContext = new WeaponDetectionDbContext(options);
        _dbContext.Database.Migrate();

        _deviceService = new DeviceService(
            new ActivationKeyGenerator(new Pbkdf2PasswordHasher()),
            TestDeviceSecretProtector.Create(),
            _dbContext);
        _branchService = new BranchService(_dbContext, _deviceService);
    }

    public void Dispose()
    {
        _dbContext.Database.EnsureDeleted();
        _dbContext.Dispose();
    }

    private async Task<BranchView> CreateBranchAsync(int cameraCount = 2)
    {
        var cameras = Enumerable.Range(1, cameraCount)
            .Select(i => new NewCameraRequest($"Camera {i}", $"rtsp://camera.example.invalid:554/s{i}"))
            .ToList();
        var result = await _branchService.CreateBranchAsync(
            new NewBranchRequest("Downtown", "1 High Street", "ops@example.invalid", cameras));
        return result.Branch;
    }

    private static UpdateBranchRequest Update(
        Guid branchId, IReadOnlyList<CameraMutation> cameras,
        string name = "Downtown", string address = "1 High Street",
        string contact = "ops@example.invalid") =>
        new(branchId, name, address, contact, cameras);

    private static CameraMutation Existing(Guid cameraId, string name, string url) =>
        new(cameraId, name, url);

    private static CameraMutation Added(string name, string url) => new(null, name, url);

    // --- Scalar fields (AC-1) ---

    [Fact]
    public async Task Update_ScalarFields_PersistsNewValues()
    {
        var branch = await CreateBranchAsync();
        var cameras = branch.Cameras.Select(c => Existing(c.CameraId, c.Name, c.RtspUrl)).ToList();

        var result = await _branchService.UpdateBranchAsync(
            Update(branch.BranchId, cameras, "Uptown", "2 Low Road", "new@example.invalid"));

        Assert.Equal(BranchUpdateStatus.Updated, result.Status);
        var stored = await _dbContext.Branches.AsNoTracking().SingleAsync();
        Assert.Equal("Uptown", stored.Name);
        Assert.Equal("2 Low Road", stored.Address);
        Assert.Equal("new@example.invalid", stored.ContactDetails);
        Assert.Equal(branch.BranchId, stored.BranchId);
    }

    // --- Camera edit preserving CameraId (AC-2) ---

    [Fact]
    public async Task Update_ExistingCamera_UpdatesValuesAndPreservesCameraId()
    {
        var branch = await CreateBranchAsync(cameraCount: 1);
        var camera = branch.Cameras.Single();

        var result = await _branchService.UpdateBranchAsync(
            Update(branch.BranchId,
                [Existing(camera.CameraId, "Renamed", "rtsp://camera.example.invalid:554/renamed")]));

        Assert.Equal(BranchUpdateStatus.Updated, result.Status);
        var stored = await _dbContext.Cameras.AsNoTracking().SingleAsync();
        Assert.Equal(camera.CameraId, stored.CameraId);
        Assert.Equal("Renamed", stored.Name);
        Assert.Equal("rtsp://camera.example.invalid:554/renamed", stored.RtspUrl);
    }

    // --- Add cameras (AC-3) ---

    [Fact]
    public async Task Update_AddOneCamera_CreatesANewCameraWithANewId()
    {
        var branch = await CreateBranchAsync(cameraCount: 1);
        var existing = branch.Cameras.Single();

        await _branchService.UpdateBranchAsync(
            Update(branch.BranchId,
            [
                Existing(existing.CameraId, existing.Name, existing.RtspUrl),
                Added("Camera 2", "rtsp://camera.example.invalid:554/added"),
            ]));

        var stored = await _dbContext.Cameras.AsNoTracking().ToListAsync();
        Assert.Equal(2, stored.Count);
        Assert.Contains(stored, c => c.CameraId == existing.CameraId);
        var added = stored.Single(c => c.CameraId != existing.CameraId);
        Assert.Equal("Camera 2", added.Name);
        Assert.NotEqual(Guid.Empty, added.CameraId);
    }

    [Fact]
    public async Task Update_AddMultipleCameras_AllGetNewIdentities()
    {
        var branch = await CreateBranchAsync(cameraCount: 1);
        var existing = branch.Cameras.Single();

        await _branchService.UpdateBranchAsync(
            Update(branch.BranchId,
            [
                Existing(existing.CameraId, existing.Name, existing.RtspUrl),
                Added("A", "rtsp://camera.example.invalid:554/a"),
                Added("B", "rtsp://camera.example.invalid:554/b"),
            ]));

        var stored = await _dbContext.Cameras.AsNoTracking().ToListAsync();
        Assert.Equal(3, stored.Count);
        Assert.Equal(3, stored.Select(c => c.CameraId).Distinct().Count());
    }

    // --- Remove camera (AC-4) ---

    [Fact]
    public async Task Update_OmitAnExistingCamera_RemovesOnlyThatCamera()
    {
        var branch = await CreateBranchAsync(cameraCount: 2);
        var kept = branch.Cameras.First();
        var dropped = branch.Cameras.Last();

        await _branchService.UpdateBranchAsync(
            Update(branch.BranchId, [Existing(kept.CameraId, kept.Name, kept.RtspUrl)]));

        var stored = await _dbContext.Cameras.AsNoTracking().ToListAsync();
        Assert.Single(stored);
        Assert.Equal(kept.CameraId, stored[0].CameraId);
        Assert.DoesNotContain(stored, c => c.CameraId == dropped.CameraId);
    }

    [Fact]
    public async Task Update_ReplaceSeveralValuesInOneRequest_AppliesAllAtomically()
    {
        var branch = await CreateBranchAsync(cameraCount: 2);
        var first = branch.Cameras.First();
        var second = branch.Cameras.Last();

        await _branchService.UpdateBranchAsync(
            Update(branch.BranchId,
            [
                Existing(first.CameraId, "First+", "rtsp://camera.example.invalid:554/first"),
                Existing(second.CameraId, "Second+", "rtsp://camera.example.invalid:554/second"),
                Added("Third", "rtsp://camera.example.invalid:554/third"),
            ],
            name: "Renamed Branch"));

        var storedBranch = await _dbContext.Branches.AsNoTracking().SingleAsync();
        Assert.Equal("Renamed Branch", storedBranch.Name);
        var stored = await _dbContext.Cameras.AsNoTracking().ToListAsync();
        Assert.Equal(3, stored.Count);
        Assert.Equal("First+", stored.Single(c => c.CameraId == first.CameraId).Name);
        Assert.Equal("Second+", stored.Single(c => c.CameraId == second.CameraId).Name);
    }

    // --- At least one camera remains (AC-5) ---

    [Fact]
    public async Task Update_ZeroCameras_IsRejectedAndLeavesTheBranchUnchanged()
    {
        var branch = await CreateBranchAsync(cameraCount: 2);

        var result = await _branchService.UpdateBranchAsync(
            Update(branch.BranchId, Array.Empty<CameraMutation>()));

        Assert.Equal(BranchUpdateStatus.Invalid, result.Status);
        Assert.Equal(2, await _dbContext.Cameras.CountAsync());
    }

    // --- Unknown branch (AC / 404) ---

    [Fact]
    public async Task Update_UnknownBranch_ReturnsNotFound()
    {
        var result = await _branchService.UpdateBranchAsync(
            Update(Guid.NewGuid(), [Added("Camera 1", "rtsp://camera.example.invalid:554/s1")]));

        Assert.Equal(BranchUpdateStatus.NotFound, result.Status);
    }

    // --- Foreign / duplicate / invalid camera id (AC-9) ---

    [Fact]
    public async Task Update_ForeignCameraId_IsRejectedAndNothingChanges()
    {
        var branch = await CreateBranchAsync(cameraCount: 1);
        var otherBranch = await CreateBranchAsync(cameraCount: 1);
        var foreignCamera = otherBranch.Cameras.Single();

        var result = await _branchService.UpdateBranchAsync(
            Update(branch.BranchId,
                [Existing(foreignCamera.CameraId, "Hijack", "rtsp://camera.example.invalid:554/x")]));

        Assert.Equal(BranchUpdateStatus.Invalid, result.Status);
        // The foreign camera is untouched (still named as created, still on its own branch).
        var stillForeign = await _dbContext.Cameras.AsNoTracking()
            .SingleAsync(c => c.CameraId == foreignCamera.CameraId);
        Assert.Equal(foreignCamera.Name, stillForeign.Name);
        Assert.Equal(otherBranch.BranchId, stillForeign.BranchId);
    }

    [Fact]
    public async Task Update_UnknownCameraId_IsRejected()
    {
        var branch = await CreateBranchAsync(cameraCount: 1);

        var result = await _branchService.UpdateBranchAsync(
            Update(branch.BranchId,
                [Existing(Guid.NewGuid(), "Ghost", "rtsp://camera.example.invalid:554/g")]));

        Assert.Equal(BranchUpdateStatus.Invalid, result.Status);
    }

    [Fact]
    public async Task Update_DuplicateCameraId_IsRejectedAndNothingChanges()
    {
        var branch = await CreateBranchAsync(cameraCount: 1);
        var camera = branch.Cameras.Single();

        var result = await _branchService.UpdateBranchAsync(
            Update(branch.BranchId,
            [
                Existing(camera.CameraId, "One", "rtsp://camera.example.invalid:554/one"),
                Existing(camera.CameraId, "Two", "rtsp://camera.example.invalid:554/two"),
            ]));

        Assert.Equal(BranchUpdateStatus.Invalid, result.Status);
        var stored = await _dbContext.Cameras.AsNoTracking().SingleAsync();
        Assert.Equal(camera.Name, stored.Name);
    }

    [Fact]
    public async Task Update_InvalidRtspUrl_IsRejectedAndNothingChanges()
    {
        var branch = await CreateBranchAsync(cameraCount: 1);
        var camera = branch.Cameras.Single();

        var result = await _branchService.UpdateBranchAsync(
            Update(branch.BranchId, [Existing(camera.CameraId, "Renamed", "http://not-rtsp.invalid")]));

        Assert.Equal(BranchUpdateStatus.Invalid, result.Status);
        var stored = await _dbContext.Cameras.AsNoTracking().SingleAsync();
        Assert.Equal(camera.Name, stored.Name);
        Assert.Equal(camera.RtspUrl, stored.RtspUrl);
    }

    // --- Transactional rollback (AC-6) ---

    [Fact]
    public async Task Update_PersistenceFailure_RollsBackLeavingEverythingUnchanged()
    {
        var branch = await CreateBranchAsync(cameraCount: 2);
        var first = branch.Cameras.First();
        var second = branch.Cameras.Last();

        // Add a CHECK constraint the write will violate, so SaveChanges fails *inside* the
        // transaction after the branch field and one camera have already been staged for update.
        // (Dropping the table would instead fail the service's earlier read; a CHECK forces the
        // failure onto the write path, which is the persistence failure AC-6 is about.) The Domain
        // permits the sentinel name, so it reaches the database and the constraint rejects it.
        await _dbContext.Database.ExecuteSqlRawAsync(
            "ALTER TABLE Cameras ADD CONSTRAINT CK_Test_RejectBoom CHECK (Name <> 'BOOM')");

        await Assert.ThrowsAsync<InvalidOperationException>(() =>
            _branchService.UpdateBranchAsync(
                Update(branch.BranchId,
                [
                    Existing(first.CameraId, "Changed", first.RtspUrl),
                    Existing(second.CameraId, "BOOM", second.RtspUrl),
                ],
                name: "Should Not Persist")));

        // Everything is as it was: the branch name, and both cameras' names.
        var storedBranch = await _dbContext.Branches.AsNoTracking().SingleAsync();
        Assert.Equal("Downtown", storedBranch.Name);
        var cameras = await _dbContext.Cameras.AsNoTracking().ToListAsync();
        Assert.Equal(2, cameras.Count);
        Assert.Equal(first.Name, cameras.Single(c => c.CameraId == first.CameraId).Name);
        Assert.Equal(second.Name, cameras.Single(c => c.CameraId == second.CameraId).Name);

        await _dbContext.Database.ExecuteSqlRawAsync(
            "ALTER TABLE Cameras DROP CONSTRAINT CK_Test_RejectBoom");
    }

    // --- Device and Activation Key preservation (AC-7, AC-8) ---

    [Fact]
    public async Task Update_LeavesDeviceRecordIdDeviceIdStatusAndSecretUnchanged()
    {
        var branch = await CreateBranchAsync(cameraCount: 1);
        var camera = branch.Cameras.Single();

        // Activate the device first so DeviceId, ActivationStatus, and ProtectedSharedSecret are all
        // populated — the preservation guarantee is most meaningful for an activated device.
        var device = await _dbContext.Devices.AsNoTracking().SingleAsync();
        var beforeRecordId = device.DeviceRecordId;
        var keyRecord = await _dbContext.ActivationKeys.AsNoTracking().SingleAsync();
        // Re-create the plaintext by generating a fresh branch is not possible; instead activate via
        // the service using a regenerated key we control.
        var regen = await _deviceService.RegenerateActivationKeyAsync(branch.BranchId);
        var activation = await _deviceService.ActivateAsync(regen!.PlaintextActivationKey);
        Assert.True(activation.Succeeded);

        var activated = await _dbContext.Devices.AsNoTracking().SingleAsync();
        var beforeDeviceId = activated.DeviceId;
        var beforeStatus = activated.ActivationStatus;
        var beforeSecret = activated.ProtectedSharedSecret;
        var beforeKeyStatuses = await _dbContext.ActivationKeys.AsNoTracking()
            .OrderBy(k => k.ActivationKeyId)
            .Select(k => new { k.ActivationKeyId, k.Status, k.DeviceRecordId })
            .ToListAsync();

        await _branchService.UpdateBranchAsync(
            Update(branch.BranchId,
                [Existing(camera.CameraId, "Edited", camera.RtspUrl)],
                name: "Edited Branch"));

        var afterDevice = await _dbContext.Devices.AsNoTracking().SingleAsync();
        Assert.Equal(beforeRecordId, afterDevice.DeviceRecordId);
        Assert.Equal(beforeDeviceId, afterDevice.DeviceId);
        Assert.Equal(beforeStatus, afterDevice.ActivationStatus);
        Assert.Equal(DeviceActivationStatus.Activated, afterDevice.ActivationStatus);
        Assert.Equal(beforeSecret, afterDevice.ProtectedSharedSecret);

        var afterKeyStatuses = await _dbContext.ActivationKeys.AsNoTracking()
            .OrderBy(k => k.ActivationKeyId)
            .Select(k => new { k.ActivationKeyId, k.Status, k.DeviceRecordId })
            .ToListAsync();
        Assert.Equal(beforeKeyStatuses, afterKeyStatuses);
    }

    [Fact]
    public async Task Update_DoesNotRegenerateOrAddAnyActivationKey()
    {
        var branch = await CreateBranchAsync(cameraCount: 1);
        var camera = branch.Cameras.Single();
        var keyCountBefore = await _dbContext.ActivationKeys.CountAsync();

        await _branchService.UpdateBranchAsync(
            Update(branch.BranchId, [Existing(camera.CameraId, "Edited", camera.RtspUrl)]));

        Assert.Equal(keyCountBefore, await _dbContext.ActivationKeys.CountAsync());
    }
}

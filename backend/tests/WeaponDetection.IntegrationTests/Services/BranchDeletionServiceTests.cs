using System;
using System.Linq;
using System.Threading.Tasks;
using Microsoft.EntityFrameworkCore;
using WeaponDetection.Application.Interfaces;
using WeaponDetection.Infrastructure.Persistence;
using WeaponDetection.Infrastructure.Security;
using WeaponDetection.Infrastructure.Services;
using Xunit;

namespace WeaponDetection.IntegrationTests.Services;

// Verifies BranchService.DeleteBranchAsync's transactional guarantee against a real SQL Server
// database (FS-03 §5.5, §11; the delete counterpart of AC-6). The HTTP-level delete behaviors
// (removal of dependents, 404, 401, other branches unaffected) are covered by BranchDeleteApiTests;
// this file isolates the rollback-on-failure guarantee, which needs a forced mid-delete failure that
// is cleanest to arrange at the service level.
//
// Each test gets its own freshly migrated, empty database. No secret is printed to any output.
public class BranchDeletionServiceTests : IDisposable
{
    private readonly WeaponDetectionDbContext _dbContext;
    private readonly BranchService _branchService;

    public BranchDeletionServiceTests()
    {
        var connectionString =
            $"Server=localhost\\SQLEXPRESS;Database=WeaponDetectionBranchDeleteTests_{Guid.NewGuid():N};" +
            "Trusted_Connection=True;TrustServerCertificate=True;";

        var options = new DbContextOptionsBuilder<WeaponDetectionDbContext>()
            .UseSqlServer(connectionString)
            .Options;

        _dbContext = new WeaponDetectionDbContext(options);
        _dbContext.Database.Migrate();

        var deviceService = new DeviceService(
            new ActivationKeyGenerator(new Pbkdf2PasswordHasher()),
            TestDeviceSecretProtector.Create(),
            _dbContext);
        _branchService = new BranchService(_dbContext, deviceService);
    }

    public void Dispose()
    {
        _dbContext.Database.EnsureDeleted();
        _dbContext.Dispose();
    }

    private async Task<Guid> CreateBranchAsync()
    {
        var result = await _branchService.CreateBranchAsync(new NewBranchRequest(
            "Downtown", "1 High Street", "ops@example.invalid",
            [new NewCameraRequest("Camera 1", "rtsp://camera.example.invalid:554/s1")]));
        return result.Branch.BranchId;
    }

    [Fact]
    public async Task Delete_FailureMidDelete_RollsBackLeavingBranchAndDependentsIntact()
    {
        var branchId = await CreateBranchAsync();
        var cameraId = await _dbContext.Cameras.AsNoTracking()
            .Where(c => c.BranchId == branchId).Select(c => c.CameraId).SingleAsync();

        // A helper table with a *restricting* (NO ACTION) foreign key to a camera. While a row here
        // references the camera, deleting that camera is a foreign-key violation — so the delete
        // transaction fails at the Cameras step, after Activation Keys and the Device have been
        // staged. The whole operation must roll back: the branch and every dependent remain
        // (FS-03 §11).
        await _dbContext.Database.ExecuteSqlRawAsync(
            "CREATE TABLE DeleteBlocker (Id uniqueidentifier PRIMARY KEY, CameraId uniqueidentifier " +
            "NOT NULL CONSTRAINT FK_DeleteBlocker_Cameras FOREIGN KEY REFERENCES Cameras(CameraId))");
        // ExecuteSqlInterpolated parameterises the interpolated values (no raw string injection).
        await _dbContext.Database.ExecuteSqlInterpolatedAsync(
            $"INSERT INTO DeleteBlocker (Id, CameraId) VALUES ({Guid.NewGuid()}, {cameraId})");

        try
        {
            await Assert.ThrowsAsync<InvalidOperationException>(() =>
                _branchService.DeleteBranchAsync(branchId));

            Assert.Equal(1, await _dbContext.Branches.CountAsync(b => b.BranchId == branchId));
            Assert.Equal(1, await _dbContext.Cameras.CountAsync(c => c.BranchId == branchId));
            Assert.Equal(1, await _dbContext.Devices.CountAsync(d => d.BranchId == branchId));
            Assert.Equal(1, await _dbContext.ActivationKeys.CountAsync());
        }
        finally
        {
            await _dbContext.Database.ExecuteSqlRawAsync("DELETE FROM DeleteBlocker");
            await _dbContext.Database.ExecuteSqlRawAsync("DROP TABLE DeleteBlocker");
        }
    }

    [Fact]
    public async Task Delete_UnknownBranch_ReturnsNotFound()
    {
        var outcome = await _branchService.DeleteBranchAsync(Guid.NewGuid());

        Assert.Equal(BranchDeletionOutcome.NotFound, outcome);
    }
}

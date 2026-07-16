using System;
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

// Verifies BranchService's branch-creation workflow — Branch + Camera(s) + reserved Device +
// Activation Key persisted as one atomic transaction (FS-02 §5.1, AC-1, AC-2; IP-01 T-15) —
// against a real SQL Server database. The transactional atomicity claim cannot be verified on EF
// Core InMemory (no transaction support) or SQLite (different rollback/constraint semantics), so
// per IP-01 §9 it runs here, mirroring AuthServiceTests. Each test gets its own freshly migrated,
// empty database.
//
// No test in this file prints a plaintext Activation Key, its secret half, or a secret hash to
// console/log output; assertions compare values in memory only.
public class BranchServiceTests : IDisposable
{
    private readonly WeaponDetectionDbContext _dbContext;
    private readonly ActivationKeyGenerator _activationKeyGenerator;
    private readonly BranchService _branchService;

    public BranchServiceTests()
    {
        var connectionString =
            $"Server=localhost\\SQLEXPRESS;Database=WeaponDetectionBranchServiceTests_{Guid.NewGuid():N};" +
            "Trusted_Connection=True;TrustServerCertificate=True;";

        var options = new DbContextOptionsBuilder<WeaponDetectionDbContext>()
            .UseSqlServer(connectionString)
            .Options;

        _dbContext = new WeaponDetectionDbContext(options);
        _dbContext.Database.Migrate();

        _activationKeyGenerator = new ActivationKeyGenerator(new Pbkdf2PasswordHasher());
        var deviceService = new DeviceService(_activationKeyGenerator, _dbContext);
        _branchService = new BranchService(_dbContext, deviceService);
    }

    public void Dispose()
    {
        _dbContext.Database.EnsureDeleted();
        _dbContext.Dispose();
    }

    private static NewBranchRequest CompleteRequest(int cameraCount = 1)
    {
        var cameras = Enumerable.Range(1, cameraCount)
            .Select(i => new NewCameraRequest($"Camera {i}", $"rtsp://camera.example.local:554/stream{i}"))
            .ToList();

        return new NewBranchRequest("Downtown Branch", "1 High Street", "ops@example.local", cameras);
    }

    private static (string KeyId, string Secret) SplitPlaintext(string plaintextKey)
    {
        var parts = plaintextKey.Split(ActivationKeyGenerator.Delimiter);
        Assert.Equal(2, parts.Length);
        return (parts[0], parts[1]);
    }

    [Fact]
    public async Task CreateBranchAsync_CompleteRequest_PersistsExactlyOneBranchWithTheReturnedId()
    {
        var result = await _branchService.CreateBranchAsync(CompleteRequest());

        var branches = await _dbContext.Branches.AsNoTracking().ToListAsync();
        Assert.Single(branches);
        Assert.Equal(result.Branch.BranchId, branches[0].BranchId);
        Assert.Equal("Downtown Branch", branches[0].Name);
        Assert.Equal("1 High Street", branches[0].Address);
        Assert.Equal("ops@example.local", branches[0].ContactDetails);
    }

    [Fact]
    public async Task CreateBranchAsync_MultipleCameras_PersistsAllOfThemAgainstTheBranch()
    {
        var result = await _branchService.CreateBranchAsync(CompleteRequest(cameraCount: 3));

        var cameras = await _dbContext.Cameras.AsNoTracking().ToListAsync();
        Assert.Equal(3, cameras.Count);
        Assert.All(cameras, c => Assert.Equal(result.Branch.BranchId, c.BranchId));
        Assert.All(cameras, c => Assert.True(c.Enabled));
        Assert.Equal(3, result.Branch.Cameras.Count);
    }

    [Fact]
    public async Task CreateBranchAsync_CompleteRequest_PersistsExactlyOneUnactivatedDeviceForTheBranch()
    {
        var result = await _branchService.CreateBranchAsync(CompleteRequest());

        var devices = await _dbContext.Devices.AsNoTracking().ToListAsync();
        Assert.Single(devices);

        var device = devices[0];
        Assert.Equal(result.Branch.BranchId, device.BranchId);
        Assert.Null(device.DeviceId);
        Assert.Equal(DeviceActivationStatus.Unactivated, device.ActivationStatus);
        Assert.Null(device.ProtectedSharedSecret);
        Assert.Null(device.LastKnownAddress);
    }

    [Fact]
    public async Task CreateBranchAsync_CompleteRequest_ResultDeviceSummaryReflectsTheUnactivatedDevice()
    {
        var result = await _branchService.CreateBranchAsync(CompleteRequest());

        Assert.Null(result.Branch.Device.DeviceId);
        Assert.Equal(DeviceActivationStatus.Unactivated, result.Branch.Device.ActivationStatus);
        Assert.Null(result.Branch.Device.LastKnownAddress);
    }

    [Fact]
    public async Task CreateBranchAsync_CompleteRequest_PersistsExactlyOneUnconsumedActivationKeyForTheDevice()
    {
        await _branchService.CreateBranchAsync(CompleteRequest());

        var device = await _dbContext.Devices.AsNoTracking().SingleAsync();
        var activationKeys = await _dbContext.ActivationKeys.AsNoTracking().ToListAsync();

        Assert.Single(activationKeys);
        var activationKey = activationKeys[0];
        Assert.Equal(ActivationKeyStatus.Unconsumed, activationKey.Status);
        Assert.Equal(device.DeviceRecordId, activationKey.DeviceRecordId);
        Assert.False(string.IsNullOrWhiteSpace(activationKey.SecretHash));
    }

    [Fact]
    public async Task CreateBranchAsync_CompleteRequest_ReturnedPlaintextKeyMatchesTheStoredRecord()
    {
        var result = await _branchService.CreateBranchAsync(CompleteRequest());
        var (keyId, secret) = SplitPlaintext(result.PlaintextActivationKey);

        var activationKey = await _dbContext.ActivationKeys.AsNoTracking().SingleAsync();

        // The disclosed keyId is the stored, indexed lookup value (AC-14)...
        Assert.Equal(activationKey.ActivationKeyId, keyId);
        // ...and the disclosed secret verifies against the stored salted hash (AC-2).
        Assert.True(_activationKeyGenerator.VerifySecret(secret, activationKey.SecretHash));
    }

    [Fact]
    public async Task CreateBranchAsync_CompleteRequest_DoesNotStoreThePlaintextKeyOrSecret()
    {
        var result = await _branchService.CreateBranchAsync(CompleteRequest());
        var (_, secret) = SplitPlaintext(result.PlaintextActivationKey);

        var activationKey = await _dbContext.ActivationKeys.AsNoTracking().SingleAsync();

        // Only the salted hash is at rest — never the plaintext secret or the complete key
        // (FS-02 §11, ARCH-001 §15.5, AC-14).
        Assert.NotEqual(secret, activationKey.SecretHash);
        Assert.NotEqual(result.PlaintextActivationKey, activationKey.SecretHash);
    }

    [Fact]
    public async Task CreateBranchAsync_TwoBranches_EachGetsItsOwnUnactivatedDeviceAndKey()
    {
        await _branchService.CreateBranchAsync(CompleteRequest());
        await _branchService.CreateBranchAsync(CompleteRequest());

        Assert.Equal(2, await _dbContext.Branches.CountAsync());
        Assert.Equal(2, await _dbContext.Devices.CountAsync());
        Assert.Equal(2, await _dbContext.ActivationKeys.CountAsync());
        Assert.Equal(0, await _dbContext.Devices.CountAsync(d => d.DeviceId != null));
    }

    [Fact]
    public async Task CreateBranchAsync_PersistenceFailure_RollsBackLeavingNoPartialRows()
    {
        // Force the Activation Key insert — the last write in the branch-creation transaction — to
        // fail, after the Branch/Camera/Device inserts have been staged in the same transaction.
        // The whole unit must roll back: a failure partway through leaves no partial rows
        // (FS-02 §5.1, AC-1).
        await _dbContext.Database.ExecuteSqlRawAsync("DROP TABLE ActivationKeys");

        await Assert.ThrowsAsync<InvalidOperationException>(() =>
            _branchService.CreateBranchAsync(CompleteRequest()));

        Assert.Equal(0, await _dbContext.Branches.CountAsync());
        Assert.Equal(0, await _dbContext.Cameras.CountAsync());
        Assert.Equal(0, await _dbContext.Devices.CountAsync());
    }
}

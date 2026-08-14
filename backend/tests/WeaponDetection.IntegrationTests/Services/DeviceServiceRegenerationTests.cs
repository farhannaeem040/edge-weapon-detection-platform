using System;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.EntityFrameworkCore;
using WeaponDetection.Application.Exceptions;
using WeaponDetection.Application.Interfaces;
using WeaponDetection.Domain;
using WeaponDetection.Infrastructure.Persistence;
using WeaponDetection.Infrastructure.Security;
using WeaponDetection.Infrastructure.Services;
using Xunit;

namespace WeaponDetection.IntegrationTests.Services;

// Verifies DeviceService.RegenerateActivationKeyAsync (IP-01 T-17, FS-02 §5.3, AC-5) against a real
// SQL Server database (IP-01 §9): the invalidate-old/insert-new replacement is a single SQL Server
// transaction over relational rows, so its atomicity and the (DeviceRecordId, Status) constraints
// cannot be faithfully exercised by EF Core InMemory/SQLite. Each test gets its own freshly
// migrated, empty database (not a shared IClassFixture), so absolute assertions like "exactly one
// Unconsumed key" are exact.
//
// The service is exercised with its real ActivationKeyGenerator/Pbkdf2PasswordHasher collaborators
// rather than mocks, because "the newly issued secret verifies against its stored hash" is part of
// what is under test. No test prints a plaintext Activation Key, its secret half, or a secret hash
// to console/log output; captured plaintext keys are only compared in memory.
public class DeviceServiceRegenerationTests : IDisposable
{
    private readonly string _connectionString;
    private readonly WeaponDetectionDbContext _dbContext;
    private readonly ActivationKeyGenerator _generator = new(new Pbkdf2PasswordHasher());
    private readonly DeviceService _service;

    public DeviceServiceRegenerationTests()
    {
        _connectionString =
            $"Server=localhost\\SQLEXPRESS;Database=WeaponDetectionDeviceServiceRegenTests_{Guid.NewGuid():N};" +
            "Trusted_Connection=True;TrustServerCertificate=True;";

        var options = new DbContextOptionsBuilder<WeaponDetectionDbContext>()
            .UseSqlServer(_connectionString)
            .Options;

        _dbContext = new WeaponDetectionDbContext(options);
        _dbContext.Database.Migrate();

        _service = new DeviceService(_generator, TestDeviceSecretProtector.Create(), _dbContext);
    }

    private WeaponDetectionDbContext CreateContextOnSameDatabase()
    {
        var options = new DbContextOptionsBuilder<WeaponDetectionDbContext>()
            .UseSqlServer(_connectionString)
            .Options;
        return new WeaponDetectionDbContext(options);
    }

    public void Dispose()
    {
        _dbContext.Database.EnsureDeleted();
        _dbContext.Dispose();
    }

    // Seeds a Branch with its reserved (unactivated) Device and first Unconsumed Activation Key,
    // exactly as branch creation would (FS-02 §5.1). Returns the branch id and the original key's
    // keyId/hash so a test can assert the regeneration replaced them.
    private async Task<(Guid BranchId, Guid DeviceRecordId, string OriginalKeyId)> SeedBranchAsync()
    {
        var branch = new Branch("Downtown Branch", "1 High Street", "ops@example.local");
        var camera = new Camera(branch.BranchId, "Front Entrance", "rtsp://camera.example.local:554/stream1", $"cam-{Guid.NewGuid():N}");
        var provisioning = _service.ProvisionForBranch(branch.BranchId);

        _dbContext.Branches.Add(branch);
        _dbContext.Cameras.Add(camera);
        _dbContext.Devices.Add(provisioning.Device);
        _dbContext.ActivationKeys.Add(provisioning.ActivationKey);
        await _dbContext.SaveChangesAsync();

        return (branch.BranchId, provisioning.Device.DeviceRecordId, provisioning.ActivationKey.ActivationKeyId);
    }

    private static (string KeyId, string Secret) SplitPlaintext(string plaintextKey)
    {
        var parts = plaintextKey.Split(ActivationKeyGenerator.Delimiter);
        Assert.Equal(2, parts.Length);
        return (parts[0], parts[1]);
    }

    // --- Not found (FS-02 §10.2 → 404) ---

    [Fact]
    public async Task RegenerateActivationKeyAsync_UnknownBranch_ReturnsNull()
    {
        await SeedBranchAsync();

        var result = await _service.RegenerateActivationKeyAsync(Guid.NewGuid());

        Assert.Null(result);
    }

    [Fact]
    public async Task RegenerateActivationKeyAsync_EmptyBranchId_ReturnsNull()
    {
        var result = await _service.RegenerateActivationKeyAsync(Guid.Empty);

        Assert.Null(result);
    }

    [Fact]
    public async Task RegenerateActivationKeyAsync_UnknownBranch_LeavesExistingKeysUntouched()
    {
        var (_, deviceRecordId, originalKeyId) = await SeedBranchAsync();

        await _service.RegenerateActivationKeyAsync(Guid.NewGuid());

        // No key record was invalidated or added for the real device — nothing was written.
        var keys = await _dbContext.ActivationKeys.AsNoTracking()
            .Where(k => k.DeviceRecordId == deviceRecordId)
            .ToListAsync();
        var only = Assert.Single(keys);
        Assert.Equal(originalKeyId, only.ActivationKeyId);
        Assert.Equal(ActivationKeyStatus.Unconsumed, only.Status);
    }

    // --- Regeneration of a never-consumed key (FS-02 §5.3, T-03, AC-5) ---

    [Fact]
    public async Task RegenerateActivationKeyAsync_ReturnsANewCompleteTwoPartKey()
    {
        var (branchId, _, originalKeyId) = await SeedBranchAsync();

        var result = await _service.RegenerateActivationKeyAsync(branchId);

        Assert.NotNull(result);
        var (newKeyId, secret) = SplitPlaintext(result!.PlaintextActivationKey);
        Assert.False(string.IsNullOrWhiteSpace(secret));
        // A regeneration issues a fresh keyId/secret pair, not the old one (FS-02 §5.3 step 4).
        Assert.NotEqual(originalKeyId, newKeyId);
    }

    [Fact]
    public async Task RegenerateActivationKeyAsync_InvalidatesTheOldKeyAndCreatesANewUnconsumedOne()
    {
        var (branchId, deviceRecordId, originalKeyId) = await SeedBranchAsync();

        var result = await _service.RegenerateActivationKeyAsync(branchId);
        var (newKeyId, _) = SplitPlaintext(result!.PlaintextActivationKey);

        var keys = await _dbContext.ActivationKeys.AsNoTracking()
            .Where(k => k.DeviceRecordId == deviceRecordId)
            .ToListAsync();

        // The old record is retained as history but Invalidated; the new record is Unconsumed.
        Assert.Equal(2, keys.Count);
        var oldKey = keys.Single(k => k.ActivationKeyId == originalKeyId);
        var newKey = keys.Single(k => k.ActivationKeyId == newKeyId);
        Assert.Equal(ActivationKeyStatus.Invalidated, oldKey.Status);
        Assert.Equal(ActivationKeyStatus.Unconsumed, newKey.Status);
    }

    [Fact]
    public async Task RegenerateActivationKeyAsync_LeavesExactlyOneNonInvalidatedKey()
    {
        var (branchId, deviceRecordId, _) = await SeedBranchAsync();

        await _service.RegenerateActivationKeyAsync(branchId);

        // The invariant regeneration preserves: at any moment a Device has exactly one live
        // (non-Invalidated) key (FS-02 §5.3). This is what the atomic invalidate+insert guarantees.
        var live = await _dbContext.ActivationKeys.AsNoTracking()
            .CountAsync(k => k.DeviceRecordId == deviceRecordId
                && k.Status != ActivationKeyStatus.Invalidated);
        Assert.Equal(1, live);
    }

    [Fact]
    public async Task RegenerateActivationKeyAsync_NewSecretVerifiesAgainstItsStoredHash()
    {
        var (branchId, deviceRecordId, _) = await SeedBranchAsync();

        var result = await _service.RegenerateActivationKeyAsync(branchId);
        var (newKeyId, secret) = SplitPlaintext(result!.PlaintextActivationKey);

        var newKey = await _dbContext.ActivationKeys.AsNoTracking()
            .SingleAsync(k => k.ActivationKeyId == newKeyId && k.DeviceRecordId == deviceRecordId);

        // Only a salted hash is persisted, and the disclosed secret verifies against it (FS-02 §1.4).
        Assert.NotEqual(secret, newKey.SecretHash);
        Assert.True(_generator.VerifySecret(secret, newKey.SecretHash));
    }

    [Fact]
    public async Task RegenerateActivationKeyAsync_DoesNotChangeTheDevicesActivationState()
    {
        var (branchId, deviceRecordId, _) = await SeedBranchAsync();

        await _service.RegenerateActivationKeyAsync(branchId);

        // Regeneration touches only Activation Key records; the Device stays unactivated with no
        // external identity or secret (FS-02 §5.3 — regeneration never activates a device).
        var device = await _dbContext.Devices.AsNoTracking()
            .SingleAsync(d => d.DeviceRecordId == deviceRecordId);
        Assert.Equal(DeviceActivationStatus.Unactivated, device.ActivationStatus);
        Assert.Null(device.DeviceId);
        Assert.Null(device.ProtectedSharedSecret);
    }

    [Fact]
    public async Task RegenerateActivationKeyAsync_RepeatedRegeneration_KeepsOnlyTheLatestKeyLive()
    {
        var (branchId, deviceRecordId, originalKeyId) = await SeedBranchAsync();

        var first = await _service.RegenerateActivationKeyAsync(branchId);
        var second = await _service.RegenerateActivationKeyAsync(branchId);
        var (firstKeyId, _) = SplitPlaintext(first!.PlaintextActivationKey);
        var (secondKeyId, _) = SplitPlaintext(second!.PlaintextActivationKey);

        var keys = await _dbContext.ActivationKeys.AsNoTracking()
            .Where(k => k.DeviceRecordId == deviceRecordId)
            .ToListAsync();

        // Three records accumulate; only the newest is Unconsumed, the two earlier ones Invalidated.
        Assert.Equal(3, keys.Count);
        Assert.Equal(ActivationKeyStatus.Invalidated, keys.Single(k => k.ActivationKeyId == originalKeyId).Status);
        Assert.Equal(ActivationKeyStatus.Invalidated, keys.Single(k => k.ActivationKeyId == firstKeyId).Status);
        Assert.Equal(ActivationKeyStatus.Unconsumed, keys.Single(k => k.ActivationKeyId == secondKeyId).Status);
    }

    // --- Regeneration when the current key was already consumed (reactivation, FS-02 §5.8, AC-5) ---

    // Simulates a device that has already activated: its Device is Activated and its key Consumed
    // (the state the T-19 flow leaves behind). Mutating through the same context the service uses
    // keeps its tracked state consistent with the database.
    private async Task<Guid> ActivateSeededDeviceAsync(Guid branchId, string originalKeyId)
    {
        var device = await _dbContext.Devices.SingleAsync(d => d.BranchId == branchId);
        device.Activate("protected-test-secret");
        var key = await _dbContext.ActivationKeys.SingleAsync(k => k.ActivationKeyId == originalKeyId);
        key.Consume();
        await _dbContext.SaveChangesAsync();
        return device.DeviceId!.Value;
    }

    [Fact]
    public async Task RegenerateActivationKeyAsync_LeavesAnAlreadyConsumedKeyConsumed_AndAddsANewUnconsumed()
    {
        var (branchId, deviceRecordId, originalKeyId) = await SeedBranchAsync();
        await ActivateSeededDeviceAsync(branchId, originalKeyId);

        var result = await _service.RegenerateActivationKeyAsync(branchId);
        var (newKeyId, _) = SplitPlaintext(result!.PlaintextActivationKey);

        var keys = await _dbContext.ActivationKeys.AsNoTracking()
            .Where(k => k.DeviceRecordId == deviceRecordId)
            .ToListAsync();

        // IP-05 T-50: only Unconsumed keys are invalidated. The already-Consumed key is historical and
        // is left Consumed (not incorrectly altered); the fresh key is the single Unconsumed one.
        Assert.Equal(ActivationKeyStatus.Consumed, keys.Single(k => k.ActivationKeyId == originalKeyId).Status);
        Assert.Equal(ActivationKeyStatus.Unconsumed, keys.Single(k => k.ActivationKeyId == newKeyId).Status);
        Assert.Single(keys, k => k.Status == ActivationKeyStatus.Unconsumed);
    }

    // --- IP-05 T-50: immediate credential revocation on regeneration ---------------------------

    [Fact]
    public async Task RegenerateActivationKeyAsync_ForAnActivatedDevice_RevokesSecret_AndRequiresReactivation()
    {
        var (branchId, deviceRecordId, originalKeyId) = await SeedBranchAsync();
        var assignedDeviceId = await ActivateSeededDeviceAsync(branchId, originalKeyId);

        await _service.RegenerateActivationKeyAsync(branchId);

        // Regenerating an activated device's key immediately revokes its shared secret and moves it to
        // ReactivationRequired, retaining the permanent DeviceId (FS-02 §5.3 amended, AC-2/AC-3/AC-4).
        var device = await _dbContext.Devices.AsNoTracking()
            .SingleAsync(d => d.DeviceRecordId == deviceRecordId);
        Assert.Equal(DeviceActivationStatus.ReactivationRequired, device.ActivationStatus);
        Assert.Null(device.ProtectedSharedSecret);
        Assert.Equal(assignedDeviceId, device.DeviceId);

        var unconsumed = await _dbContext.ActivationKeys.AsNoTracking()
            .CountAsync(k => k.DeviceRecordId == deviceRecordId && k.Status == ActivationKeyStatus.Unconsumed);
        Assert.Equal(1, unconsumed);
    }

    [Fact]
    public async Task RegenerateActivationKeyAsync_ForAReactivationRequiredDevice_StaysReactivationRequired_AndReplacesTheUnconsumedKey()
    {
        var (branchId, deviceRecordId, originalKeyId) = await SeedBranchAsync();
        var assignedDeviceId = await ActivateSeededDeviceAsync(branchId, originalKeyId);

        // First regeneration takes the device Activated -> ReactivationRequired and leaves one
        // Unconsumed key; the second regenerates while already ReactivationRequired.
        var first = await _service.RegenerateActivationKeyAsync(branchId);
        var (firstKeyId, _) = SplitPlaintext(first!.PlaintextActivationKey);
        var second = await _service.RegenerateActivationKeyAsync(branchId);
        var (secondKeyId, _) = SplitPlaintext(second!.PlaintextActivationKey);

        var device = await _dbContext.Devices.AsNoTracking()
            .SingleAsync(d => d.DeviceRecordId == deviceRecordId);
        Assert.Equal(DeviceActivationStatus.ReactivationRequired, device.ActivationStatus);
        Assert.Null(device.ProtectedSharedSecret);
        Assert.Equal(assignedDeviceId, device.DeviceId);

        var keys = await _dbContext.ActivationKeys.AsNoTracking()
            .Where(k => k.DeviceRecordId == deviceRecordId)
            .ToListAsync();
        Assert.Equal(ActivationKeyStatus.Invalidated, keys.Single(k => k.ActivationKeyId == firstKeyId).Status);
        Assert.Equal(ActivationKeyStatus.Unconsumed, keys.Single(k => k.ActivationKeyId == secondKeyId).Status);
        Assert.Single(keys, k => k.Status == ActivationKeyStatus.Unconsumed);
    }

    [Fact]
    public async Task RegenerateActivationKeyAsync_ForAnUnactivatedDevice_StaysUnactivated_WithExactlyOneUnconsumedKey()
    {
        var (branchId, deviceRecordId, _) = await SeedBranchAsync();

        // Succeeds without throwing — RequireReactivation() (which throws on an Unactivated device) is
        // deliberately not called for an Unactivated device (T-50 item 8); the device is untouched.
        var result = await _service.RegenerateActivationKeyAsync(branchId);
        Assert.NotNull(result);

        var device = await _dbContext.Devices.AsNoTracking()
            .SingleAsync(d => d.DeviceRecordId == deviceRecordId);
        Assert.Equal(DeviceActivationStatus.Unactivated, device.ActivationStatus);
        Assert.Null(device.DeviceId);
        Assert.Null(device.ProtectedSharedSecret);

        var unconsumed = await _dbContext.ActivationKeys.AsNoTracking()
            .CountAsync(k => k.DeviceRecordId == deviceRecordId && k.Status == ActivationKeyStatus.Unconsumed);
        Assert.Equal(1, unconsumed);
    }

    [Fact]
    public async Task RegenerateActivationKeyAsync_WhenPersistenceFails_RollsBackEverything_ReturnsNoKey_AndIsNotAConflict()
    {
        var (branchId, deviceRecordId, originalKeyId) = await SeedBranchAsync();
        var assignedDeviceId = await ActivateSeededDeviceAsync(branchId, originalKeyId);

        // A generator whose keyId collides with the existing Activation Key's primary key forces the
        // replacement insert to fail with a PK violation (2627) — a persistence failure that is NOT
        // the Unconsumed unique-index conflict, so it must surface as an unexpected error (not a
        // regeneration conflict) and roll the whole operation back.
        var collidingService = new DeviceService(
            new FixedActivationKeyGenerator(originalKeyId),
            TestDeviceSecretProtector.Create(),
            _dbContext);

        var exception = await Assert.ThrowsAsync<InvalidOperationException>(
            () => collidingService.RegenerateActivationKeyAsync(branchId));
        Assert.IsNotType<ActivationKeyRegenerationConflictException>(exception);
        Assert.DoesNotContain("secret", exception.ToString(), StringComparison.OrdinalIgnoreCase);

        // The rollback preserved everything: the device is still Activated with its secret and its
        // DeviceId, the original key is unchanged, and no replacement row was committed.
        using var verify = CreateContextOnSameDatabase();
        var device = await verify.Devices.AsNoTracking().SingleAsync(d => d.DeviceRecordId == deviceRecordId);
        Assert.Equal(DeviceActivationStatus.Activated, device.ActivationStatus);
        Assert.NotNull(device.ProtectedSharedSecret);
        Assert.Equal(assignedDeviceId, device.DeviceId);

        var keys = await verify.ActivationKeys.AsNoTracking()
            .Where(k => k.DeviceRecordId == deviceRecordId).ToListAsync();
        var only = Assert.Single(keys);
        Assert.Equal(originalKeyId, only.ActivationKeyId);
        Assert.Equal(ActivationKeyStatus.Consumed, only.Status);
    }

    [Fact]
    public async Task RegenerateActivationKeyAsync_TwoConcurrentRequests_ExactlyOneCommits_TheOtherConflictsWithNoKey()
    {
        var (branchId, deviceRecordId, originalKeyId) = await SeedBranchAsync();
        var assignedDeviceId = await ActivateSeededDeviceAsync(branchId, originalKeyId);

        // Two independent contexts/services on the same database contend for the single Unconsumed
        // slot. The hooked service synchronizes both AFTER they have read the pre-state and BEFORE
        // either writes, so they genuinely race on the T-49 unique index; the index — not timing —
        // makes the outcome deterministic: exactly one commits, the other rolls back with a conflict.
        using var barrier = new Barrier(2);

        async Task<(ActivationKeyRegenerationResult? Result, Exception? Error)> Regenerate()
        {
            await using var context = CreateContextOnSameDatabase();
            var service = new BarrierHookedDeviceService(
                barrier,
                new ActivationKeyGenerator(new Pbkdf2PasswordHasher()),
                TestDeviceSecretProtector.Create(),
                context);
            try
            {
                var result = await service.RegenerateActivationKeyAsync(branchId);
                return (result, null);
            }
            catch (Exception ex)
            {
                return (null, ex);
            }
        }

        var outcomes = await Task.WhenAll(Task.Run(Regenerate), Task.Run(Regenerate));

        var winners = outcomes.Where(o => o.Result is not null).ToList();
        var losers = outcomes.Where(o => o.Error is not null).ToList();
        var winner = Assert.Single(winners);
        var loser = Assert.Single(losers);

        // The loser rolled back with the typed conflict and returned no plaintext key or secret text.
        Assert.IsType<ActivationKeyRegenerationConflictException>(loser.Error);
        Assert.Null(loser.Result);
        Assert.DoesNotContain("secret", loser.Error!.ToString(), StringComparison.OrdinalIgnoreCase);

        // Exactly one Unconsumed key survives, and it is the winner's — its disclosed secret verifies
        // against the single committed row's stored hash.
        using var verify = CreateContextOnSameDatabase();
        var live = await verify.ActivationKeys.AsNoTracking()
            .Where(k => k.DeviceRecordId == deviceRecordId && k.Status == ActivationKeyStatus.Unconsumed)
            .ToListAsync();
        var liveKey = Assert.Single(live);
        var (winnerKeyId, winnerSecret) = SplitPlaintext(winner.Result!.PlaintextActivationKey);
        Assert.Equal(winnerKeyId, liveKey.ActivationKeyId);
        Assert.True(_generator.VerifySecret(winnerSecret, liveKey.SecretHash));

        // Final device state: ReactivationRequired, secret revoked, permanent DeviceId unchanged.
        var device = await verify.Devices.AsNoTracking().SingleAsync(d => d.DeviceRecordId == deviceRecordId);
        Assert.Equal(DeviceActivationStatus.ReactivationRequired, device.ActivationStatus);
        Assert.Null(device.ProtectedSharedSecret);
        Assert.Equal(assignedDeviceId, device.DeviceId);
    }

    // A generator that returns a caller-chosen keyId, so a test can force a primary-key collision on
    // the replacement insert. No real credential material — placeholder values only.
    private sealed class FixedActivationKeyGenerator : IActivationKeyGenerator
    {
        private readonly string _keyId;

        public FixedActivationKeyGenerator(string keyId) => _keyId = keyId;

        public GeneratedActivationKey Generate() =>
            new(_keyId, $"{_keyId}.placeholder-secret", "placeholder-secret-hash");

        public bool VerifySecret(string secret, string secretHash) => false;
    }

    // Overrides the regeneration write-barrier hook so two concurrent regenerations synchronize after
    // reading the pre-state and before writing, producing a deterministic race on the T-49 index.
    private sealed class BarrierHookedDeviceService : DeviceService
    {
        private readonly Barrier _barrier;

        public BarrierHookedDeviceService(
            Barrier barrier,
            IActivationKeyGenerator generator,
            IDeviceSecretProtector protector,
            WeaponDetectionDbContext dbContext)
            : base(generator, protector, dbContext) => _barrier = barrier;

        protected override Task OnBeforeRegenerationWriteAsync(CancellationToken cancellationToken)
        {
            _barrier.SignalAndWait(cancellationToken);
            return Task.CompletedTask;
        }
    }
}

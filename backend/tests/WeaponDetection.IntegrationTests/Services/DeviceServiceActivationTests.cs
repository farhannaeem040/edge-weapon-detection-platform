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

// Verifies DeviceService.ActivateAsync (IP-01 T-19, §8 steps 3-6; FS-02 §5.5-§5.7) against a real
// SQL Server database (IP-01 §9): the parse/lookup/verify/consume flow is a transaction over
// relational rows with an indexed keyId lookup and an update-locked consumption, none of which EF
// Core InMemory/SQLite reproduces faithfully. Each test gets its own freshly migrated, empty
// database (not a shared IClassFixture), so absolute assertions are exact.
//
// The service is exercised with its real ActivationKeyGenerator/Pbkdf2PasswordHasher and a real
// DataProtectionDeviceSecretProtector (a single shared instance, so the stored protected secret can
// be unprotected in assertions to prove the round-trip). No test prints a plaintext Activation Key,
// its secret half, or a device shared secret to console/log output; captured values are only
// compared in memory.
//
// The dedicated two-concurrent-requests test for AC-16 is a later task (IP-01 T-21); this file
// covers the single-caller behaviour — every rejection path, first activation, reactivation
// identity retention, and secret rotation.
public class DeviceServiceActivationTests : IDisposable
{
    private readonly WeaponDetectionDbContext _dbContext;
    private readonly ActivationKeyGenerator _generator = new(new Pbkdf2PasswordHasher());
    private readonly DataProtectionDeviceSecretProtector _protector = TestDeviceSecretProtector.Create();
    private readonly DeviceService _service;

    public DeviceServiceActivationTests()
    {
        var connectionString =
            $"Server=localhost\\SQLEXPRESS;Database=WeaponDetectionDeviceServiceActivationTests_{Guid.NewGuid():N};" +
            "Trusted_Connection=True;TrustServerCertificate=True;";

        var options = new DbContextOptionsBuilder<WeaponDetectionDbContext>()
            .UseSqlServer(connectionString)
            .Options;

        _dbContext = new WeaponDetectionDbContext(options);
        _dbContext.Database.Migrate();

        _service = new DeviceService(_generator, _protector, _dbContext);
    }

    public void Dispose()
    {
        _dbContext.Database.EnsureDeleted();
        _dbContext.Dispose();
    }

    // Seeds a Branch with its reserved (unactivated) Device and first Unconsumed Activation Key, as
    // branch creation would (FS-02 §5.1), and returns the branch id, the device's internal record id,
    // and the complete plaintext key.
    private async Task<(Guid BranchId, Guid DeviceRecordId, string PlaintextKey)> SeedBranchAsync()
    {
        var branch = new Branch("Downtown Branch", "1 High Street", "ops@example.local");
        var camera = new Camera(branch.BranchId, "Front Entrance", "rtsp://camera.example.local:554/stream1");
        var provisioning = _service.ProvisionForBranch(branch.BranchId);

        _dbContext.Branches.Add(branch);
        _dbContext.Cameras.Add(camera);
        _dbContext.Devices.Add(provisioning.Device);
        _dbContext.ActivationKeys.Add(provisioning.ActivationKey);
        await _dbContext.SaveChangesAsync();

        return (branch.BranchId, provisioning.Device.DeviceRecordId, provisioning.PlaintextActivationKey);
    }

    private static string KeyIdOf(string plaintextKey) =>
        plaintextKey.Split(ActivationKeyGenerator.Delimiter)[0];

    private async Task<Device> ReloadDeviceAsync(Guid deviceRecordId) =>
        await _dbContext.Devices.AsNoTracking().SingleAsync(d => d.DeviceRecordId == deviceRecordId);

    private async Task<ActivationKey> ReloadKeyAsync(string keyId) =>
        await _dbContext.ActivationKeys.AsNoTracking().SingleAsync(k => k.ActivationKeyId == keyId);

    // --- First activation (FS-02 §5.5, AC-3, AC-12) ---

    [Fact]
    public async Task ActivateAsync_ValidUnconsumedKey_Succeeds()
    {
        var (branchId, _, key) = await SeedBranchAsync();

        var result = await _service.ActivateAsync(key);

        Assert.True(result.Succeeded);
        Assert.NotNull(result.Success);
        Assert.NotEqual(Guid.Empty, result.Success!.DeviceId);
        Assert.False(string.IsNullOrWhiteSpace(result.Success.SharedSecret));
        Assert.Equal(branchId, result.Success.BranchId);
    }

    [Fact]
    public async Task ActivateAsync_ValidKey_AssignsDeviceIdMarksActivatedAndConsumesTheKey()
    {
        var (_, deviceRecordId, key) = await SeedBranchAsync();

        var result = await _service.ActivateAsync(key);

        var device = await ReloadDeviceAsync(deviceRecordId);
        Assert.Equal(DeviceActivationStatus.Activated, device.ActivationStatus);
        Assert.Equal(result.Success!.DeviceId, device.DeviceId);

        var storedKey = await ReloadKeyAsync(KeyIdOf(key));
        Assert.Equal(ActivationKeyStatus.Consumed, storedKey.Status);
    }

    [Fact]
    public async Task ActivateAsync_ValidKey_StoresOnlyTheProtectedSecretWhichUnprotectsToTheReturnedValue()
    {
        var (_, deviceRecordId, key) = await SeedBranchAsync();

        var result = await _service.ActivateAsync(key);

        var device = await ReloadDeviceAsync(deviceRecordId);
        Assert.NotNull(device.ProtectedSharedSecret);
        // Only the protected form is stored, never the plaintext (FS-02 §11) — and it recovers the
        // exact secret returned to the caller (ARCH-001 §13.3, recoverable-but-protected).
        Assert.NotEqual(result.Success!.SharedSecret, device.ProtectedSharedSecret);
        Assert.Equal(result.Success.SharedSecret, _protector.Unprotect(device.ProtectedSharedSecret!));
    }

    // --- Malformed key (FS-02 §5.6, §12, AC-15; T-09) ---

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    [InlineData("no-delimiter-here")]
    [InlineData("keyid.")]
    [InlineData(".secret")]
    [InlineData("keyid.secret.extra")]
    public async Task ActivateAsync_MalformedKey_RejectedWithoutLookup(string malformed)
    {
        // A branch exists, so a malformed value is genuinely rejected by parsing rather than by an
        // empty database.
        await SeedBranchAsync();

        var result = await _service.ActivateAsync(malformed);

        Assert.False(result.Succeeded);
        Assert.Equal(DeviceActivationFailureReason.Malformed, result.FailureReason);
    }

    // --- Unknown keyId (FS-02 §5.6, AC-15; T-10) ---

    [Fact]
    public async Task ActivateAsync_UnknownKeyId_Rejected()
    {
        var (_, deviceRecordId, _) = await SeedBranchAsync();

        var result = await _service.ActivateAsync("unknownkeyid.somesecretvalue");

        Assert.False(result.Succeeded);
        Assert.Equal(DeviceActivationFailureReason.UnknownKeyId, result.FailureReason);

        // No side effects on the existing device (FS-02 §5.6).
        var device = await ReloadDeviceAsync(deviceRecordId);
        Assert.Equal(DeviceActivationStatus.Unactivated, device.ActivationStatus);
        Assert.Null(device.DeviceId);
    }

    // --- Correct keyId, wrong secret (FS-02 §5.6, AC-15; T-11) ---

    [Fact]
    public async Task ActivateAsync_CorrectKeyIdWrongSecret_Rejected_WithNoSideEffects()
    {
        var (_, deviceRecordId, key) = await SeedBranchAsync();
        var wrong = $"{KeyIdOf(key)}.definitely-not-the-real-secret";

        var result = await _service.ActivateAsync(wrong);

        Assert.False(result.Succeeded);
        Assert.Equal(DeviceActivationFailureReason.IncorrectSecret, result.FailureReason);

        var device = await ReloadDeviceAsync(deviceRecordId);
        Assert.Equal(DeviceActivationStatus.Unactivated, device.ActivationStatus);
        Assert.Null(device.DeviceId);
        var storedKey = await ReloadKeyAsync(KeyIdOf(key));
        Assert.Equal(ActivationKeyStatus.Unconsumed, storedKey.Status);
    }

    // --- Consumed key reuse (FS-02 §5.7, AC-4, AC-9; T-08) ---

    [Fact]
    public async Task ActivateAsync_ConsumedKeyReuse_Rejected_WithNoChangeToTheDevice()
    {
        var (_, deviceRecordId, key) = await SeedBranchAsync();
        var first = await _service.ActivateAsync(key);
        Assert.True(first.Succeeded);

        var replay = await _service.ActivateAsync(key);

        Assert.False(replay.Succeeded);
        Assert.Equal(DeviceActivationFailureReason.Consumed, replay.FailureReason);

        // The device created by the first activation is unaffected by the rejected replay (FS-02 §5.7).
        var device = await ReloadDeviceAsync(deviceRecordId);
        Assert.Equal(DeviceActivationStatus.Activated, device.ActivationStatus);
        Assert.Equal(first.Success!.DeviceId, device.DeviceId);
        Assert.Equal(
            first.Success.SharedSecret, _protector.Unprotect(device.ProtectedSharedSecret!));
    }

    // --- Invalidated key reuse (FS-02 §5.7, AC-5; T-12) ---

    [Fact]
    public async Task ActivateAsync_KeyInvalidatedByRegeneration_Rejected()
    {
        var (branchId, deviceRecordId, key) = await SeedBranchAsync();

        // Regeneration invalidates the original key (T-17).
        await _service.RegenerateActivationKeyAsync(branchId);

        var result = await _service.ActivateAsync(key);

        Assert.False(result.Succeeded);
        Assert.Equal(DeviceActivationFailureReason.Invalidated, result.FailureReason);

        var device = await ReloadDeviceAsync(deviceRecordId);
        Assert.Equal(DeviceActivationStatus.Unactivated, device.ActivationStatus);
    }

    // --- Reactivation: identity retained, secret rotated (FS-02 §5.8, AC-7, AC-9, AC-12; T-14) ---

    [Fact]
    public async Task ActivateAsync_Reactivation_RetainsDeviceIdAndRotatesSharedSecret()
    {
        var (branchId, deviceRecordId, firstKey) = await SeedBranchAsync();

        var firstActivation = await _service.ActivateAsync(firstKey);
        Assert.True(firstActivation.Succeeded);

        // Reactivation requires a freshly regenerated key (BR-003) — the old one is now consumed.
        var regeneration = await _service.RegenerateActivationKeyAsync(branchId);
        var secondActivation = await _service.ActivateAsync(regeneration!.PlaintextActivationKey);

        Assert.True(secondActivation.Succeeded);
        // The persistent DeviceId is assigned once and retained across reactivation (AC-7)...
        Assert.Equal(firstActivation.Success!.DeviceId, secondActivation.Success!.DeviceId);
        // ...while a new shared secret is issued, replacing the previous one (NFR-SEC-002, ADR-015).
        Assert.NotEqual(firstActivation.Success.SharedSecret, secondActivation.Success.SharedSecret);

        var device = await ReloadDeviceAsync(deviceRecordId);
        Assert.Equal(secondActivation.Success.DeviceId, device.DeviceId);
        Assert.Equal(
            secondActivation.Success.SharedSecret, _protector.Unprotect(device.ProtectedSharedSecret!));
    }

    [Fact]
    public async Task ActivateAsync_AfterReactivation_TheRegeneratedKeyIsConsumedAndOriginalStaysInvalidated()
    {
        var (branchId, deviceRecordId, firstKey) = await SeedBranchAsync();
        await _service.ActivateAsync(firstKey);
        var regeneration = await _service.RegenerateActivationKeyAsync(branchId);
        await _service.ActivateAsync(regeneration!.PlaintextActivationKey);

        var keys = await _dbContext.ActivationKeys.AsNoTracking()
            .Where(k => k.DeviceRecordId == deviceRecordId)
            .ToListAsync();

        // First key: consumed by the first activation, then invalidated by regeneration → Invalidated.
        // Second key: consumed by the reactivation → Consumed. Exactly one of each state remains.
        Assert.Equal(2, keys.Count);
        Assert.Equal(ActivationKeyStatus.Invalidated, keys.Single(k => k.ActivationKeyId == KeyIdOf(firstKey)).Status);
        Assert.Equal(
            ActivationKeyStatus.Consumed,
            keys.Single(k => k.ActivationKeyId == KeyIdOf(regeneration.PlaintextActivationKey)).Status);
    }
}

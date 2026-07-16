using Microsoft.EntityFrameworkCore;
using WeaponDetection.Application.Interfaces;
using WeaponDetection.Domain;
using WeaponDetection.Infrastructure.Persistence;

namespace WeaponDetection.Infrastructure.Services;

// Device operations behind an Application interface (ARCH-001 §10.1). Responsibilities today:
//
//  - ProvisionForBranch (T-15): a pure, database-free build of a branch's reserved Device and its
//    first Activation Key; BranchService persists the result inside the branch-creation transaction.
//  - GetDeviceByDeviceIdAsync (T-16): a read-only lookup of an activated Device by its external
//    DeviceId, for GET /api/v1/devices/{id} (FS-02 §10.3).
//  - RegenerateActivationKeyAsync (T-17): a transactional invalidate-old/insert-new replacement of a
//    branch's Activation Key (FS-02 §5.3).
//
// The remaining write operation (T-19 activation consumption) is a later task. The plaintext
// Activation Key produced by provisioning/regeneration is never logged (FS-02 §11); it flows
// straight back to the caller for the single disclosure and is otherwise discarded.
public class DeviceService : IDeviceService
{
    private readonly IActivationKeyGenerator _activationKeyGenerator;
    private readonly WeaponDetectionDbContext _dbContext;

    public DeviceService(
        IActivationKeyGenerator activationKeyGenerator,
        WeaponDetectionDbContext dbContext)
    {
        _activationKeyGenerator = activationKeyGenerator
            ?? throw new ArgumentNullException(nameof(activationKeyGenerator));
        _dbContext = dbContext ?? throw new ArgumentNullException(nameof(dbContext));
    }

    public async Task<DeviceDetailView?> GetDeviceByDeviceIdAsync(
        Guid deviceId,
        CancellationToken cancellationToken = default)
    {
        // A Device is addressable here only by its external DeviceId, which is non-null only once
        // activated (FS-02 §1.3). An all-zero id is never a valid assigned DeviceId, so it is
        // treated as "no such device" without querying — this also stops it from matching an
        // unactivated row's NULL DeviceId under any provider that might coerce the comparison.
        if (deviceId == Guid.Empty)
        {
            return null;
        }

        var device = await _dbContext.Devices
            .AsNoTracking()
            .SingleOrDefaultAsync(d => d.DeviceId == deviceId, cancellationToken);
        if (device is null)
        {
            return null;
        }

        // DeviceId is guaranteed non-null on the matched row (it was the lookup key); the summary
        // type exposes only externally-safe fields — never DeviceRecordId or ProtectedSharedSecret.
        return new DeviceDetailView(
            device.DeviceId!.Value,
            device.BranchId,
            device.ActivationStatus,
            device.LastKnownAddress);
    }

    public DeviceProvisioning ProvisionForBranch(Guid branchId)
    {
        if (branchId == Guid.Empty)
        {
            throw new ArgumentException("Branch id is required.", nameof(branchId));
        }

        // The Device entity fixes the reserved, pre-activation state itself (DeviceId NULL,
        // ActivationStatus Unactivated) — this method does not get to choose otherwise.
        var device = new Device(branchId);

        // Only the keyId and the salted secret hash are persisted on the ActivationKey; the
        // complete plaintext key is carried back out for the single disclosure and never stored.
        var generated = _activationKeyGenerator.Generate();
        var activationKey = new ActivationKey(
            generated.KeyId, device.DeviceRecordId, generated.SecretHash);

        return new DeviceProvisioning(device, activationKey, generated.PlaintextKey);
    }

    public async Task<ActivationKeyRegenerationResult?> RegenerateActivationKeyAsync(
        Guid branchId,
        CancellationToken cancellationToken = default)
    {
        // An all-zero id is never a valid BranchId, so it is "no such branch" (404) without a query
        // — mirroring GetDeviceByDeviceIdAsync's treatment of Guid.Empty, and short-circuiting
        // before any transaction is opened.
        if (branchId == Guid.Empty)
        {
            return null;
        }

        // The invalidation of the old key and the insertion of its replacement are one atomic unit:
        // either both happen or neither does, so a failure partway through can never leave the
        // Device with two live keys or none (FS-02 §5.3, §12; IP-01 T-17). SQL Server enforces the
        // atomicity — hence this method's behavioural coverage is an integration test against a real
        // database (IP-01 §9). The existence read is kept inside the transaction so it shares the
        // same consistent snapshot as the writes.
        await using var transaction =
            await _dbContext.Database.BeginTransactionAsync(cancellationToken);

        var device = await _dbContext.Devices
            .SingleOrDefaultAsync(d => d.BranchId == branchId, cancellationToken);
        if (device is null)
        {
            // Nothing has been written; the disposing transaction rolls back cleanly. Null maps to
            // 404 at the API layer (FS-02 §10.2).
            return null;
        }

        // The branch's current key is the single non-Invalidated record for this Device (a Device
        // accumulates a history of keys, of which the older ones are already Invalidated — FS-02
        // §5.3). It is invalidated regardless of whether it is Unconsumed or already Consumed
        // (FS-02 §5.3 step 3, AC-5): an already-consumed key cannot be reused anyway, but marking it
        // Invalidated keeps exactly one non-Invalidated key per Device as an invariant. Loaded
        // tracked (no AsNoTracking) so the status transition is persisted. The Device row itself is
        // never modified here — regeneration does not change activation status.
        var currentKeys = await _dbContext.ActivationKeys
            .Where(k => k.DeviceRecordId == device.DeviceRecordId
                && k.Status != ActivationKeyStatus.Invalidated)
            .ToListAsync(cancellationToken);
        foreach (var currentKey in currentKeys)
        {
            currentKey.Invalidate();
        }

        // The replacement stores only the new keyId and the salted secret hash; the complete
        // plaintext key is carried back out for the single disclosure and never persisted or logged
        // (FS-02 §1.4, §11). The new key points at the same internal DeviceRecordId, never the
        // (still possibly NULL) external DeviceId (FS-02 §1.3).
        var generated = _activationKeyGenerator.Generate();
        var replacement = new ActivationKey(
            generated.KeyId, device.DeviceRecordId, generated.SecretHash);
        _dbContext.ActivationKeys.Add(replacement);

        try
        {
            await _dbContext.SaveChangesAsync(cancellationToken);
            await transaction.CommitAsync(cancellationToken);
        }
        catch (DbUpdateException ex)
        {
            await transaction.RollbackAsync(cancellationToken);

            // The plaintext key/secret is never interpolated into the message — an exception is a
            // log entry waiting to happen (FS-02 §11).
            throw new InvalidOperationException(
                "Activation key regeneration failed while persisting the new key.", ex);
        }

        return new ActivationKeyRegenerationResult(generated.PlaintextKey);
    }
}

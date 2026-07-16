using Microsoft.EntityFrameworkCore;
using WeaponDetection.Application.Interfaces;
using WeaponDetection.Domain;
using WeaponDetection.Infrastructure.Persistence;

namespace WeaponDetection.Infrastructure.Services;

// Device operations behind an Application interface (ARCH-001 §10.1). Two responsibilities today:
//
//  - ProvisionForBranch (T-15): a pure, database-free build of a branch's reserved Device and its
//    first Activation Key; BranchService persists the result inside the branch-creation transaction.
//  - GetDeviceByDeviceIdAsync (T-16): a read-only lookup of an activated Device by its external
//    DeviceId, for GET /api/v1/devices/{id} (FS-02 §10.3).
//
// Later tasks (T-17 regeneration, T-19 activation) add the database-backed write operations; none
// of them changes these two methods. The plaintext Activation Key produced by provisioning is never
// logged (FS-02 §11); it flows straight back to the caller for the single disclosure and is
// otherwise discarded.
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
}

using WeaponDetection.Application.Interfaces;
using WeaponDetection.Domain;

namespace WeaponDetection.Infrastructure.Services;

// Provisions a branch's reserved Device and its first Activation Key (FS-02 §5.1 steps 4–5,
// IP-01 T-15). Placed in Infrastructure/Services alongside AuthService for consistency, though for
// this task it depends only on IActivationKeyGenerator and touches no DbContext: provisioning
// constructs the entities and generates the key, and BranchService persists them inside the single
// branch-creation transaction. Later tasks (T-17 regeneration, T-19 activation) will add the
// database-backed operations; none of them changes this method.
//
// The plaintext Activation Key produced here is never logged (FS-02 §11); it flows straight back to
// the caller for the single disclosure and is otherwise discarded.
public class DeviceService : IDeviceService
{
    private readonly IActivationKeyGenerator _activationKeyGenerator;

    public DeviceService(IActivationKeyGenerator activationKeyGenerator)
    {
        _activationKeyGenerator = activationKeyGenerator
            ?? throw new ArgumentNullException(nameof(activationKeyGenerator));
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

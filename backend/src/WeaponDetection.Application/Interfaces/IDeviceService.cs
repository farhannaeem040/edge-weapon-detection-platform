using WeaponDetection.Domain;

namespace WeaponDetection.Application.Interfaces;

// Provisions the single Device reserved for a Branch, together with that Device's first
// Activation Key (FS-02 §5.1 steps 4–5, IP-01 T-15). This is the only DeviceService behavior
// Increment A's branch-creation workflow needs; activation consumption (T-19) and key
// regeneration (T-17) are separate, later operations and are deliberately not on this interface
// yet.
//
// ProvisionForBranch performs no I/O: it constructs the Domain entities and generates the key,
// but does not persist anything. Persistence — and its atomicity — belong to BranchService, which
// adds the returned entities inside the single branch-creation transaction (FS-02 §5.1). Keeping
// provisioning free of the DbContext is what makes it a pure, database-independent unit
// (mirroring how IActivationKeyGenerator and IJwtIssuer stay off the persistence path).
public interface IDeviceService
{
    // Builds an unactivated Device for the given branch and a matching Unconsumed Activation Key,
    // and returns them alongside the complete plaintext key to disclose exactly once. Throws for an
    // empty branch id; every other value is internally generated and always valid.
    DeviceProvisioning ProvisionForBranch(Guid branchId);

    // Looks up a Device by its external, persistent DeviceId for GET /api/v1/devices/{id}
    // (FS-02 §10.3). The lookup key is deliberately DeviceId, never the internal DeviceRecordId,
    // which is never exposed by any API (FS-02 §1.3): a Device is addressable by /devices/{id} only
    // once it has an external identity, i.e. only after activation. An unactivated Device (DeviceId
    // NULL) is therefore not independently addressable and is viewed through its branch instead;
    // an unknown DeviceId returns null (mapped to 404 by the API layer).
    Task<DeviceDetailView?> GetDeviceByDeviceIdAsync(
        Guid deviceId,
        CancellationToken cancellationToken = default);
}

// A single activated Device's detail, for GET /api/v1/devices/{id}. Because the lookup key is the
// external DeviceId, any Device this projects is necessarily activated, so DeviceId is non-null
// here (unlike DeviceSummaryView's nullable DeviceId within a branch). BranchId correlates the
// device back to its branch and is already a client-visible identifier. The internal DeviceRecordId
// and the ProtectedSharedSecret are never included (FS-02 §1.3, §11).
public sealed record DeviceDetailView(
    Guid DeviceId,
    Guid BranchId,
    DeviceActivationStatus ActivationStatus,
    string? LastKnownAddress);

// The result of provisioning a branch's Device and its first Activation Key. All three parts are
// produced together and belong together:
//
//  - Device — the reserved, unactivated Device record (DeviceId NULL, ActivationStatus
//    Unactivated). Its DeviceRecordId is what ActivationKey points at (FS-02 §1.3).
//  - ActivationKey — the Unconsumed key record storing only the keyId and the salted secret hash.
//  - PlaintextActivationKey — the complete `keyId.secret` string, the sole representation of the
//    secret material outside the stored hash. It is disclosed once by the branch-creation response
//    (FS-02 §5.1 step 6) and never persisted or logged (FS-02 §11).
//
// These are Domain entities and an Application-owned string, so the type respects the layer
// dependency direction (Application → Domain); no EF or Infrastructure type crosses this boundary.
public sealed record DeviceProvisioning(
    Device Device,
    ActivationKey ActivationKey,
    string PlaintextActivationKey);

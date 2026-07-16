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
//
// RegenerateActivationKeyAsync (T-17), by contrast, is inherently database-backed: it invalidates
// the current key record and inserts the replacement as one atomic unit, so its behaviour is
// verified by an integration test against real SQL Server (IP-01 §9), not a pure unit.
public interface IDeviceService
{
    // Builds an unactivated Device for the given branch and a matching Unconsumed Activation Key,
    // and returns them alongside the complete plaintext key to disclose exactly once. Throws for an
    // empty branch id; every other value is internally generated and always valid.
    DeviceProvisioning ProvisionForBranch(Guid branchId);

    // Regenerates the Activation Key for the Device belonging to the given branch (FS-02 §5.3,
    // IP-01 T-17): in a single SQL Server transaction it invalidates the branch's current Activation
    // Key record — regardless of whether that key was Unconsumed or already Consumed (FS-02 §5.3
    // step 3, AC-5) — and inserts a new Unconsumed key (new keyId, new salted secret hash) for the
    // same Device. Returns the complete new plaintext key to disclose exactly once (FS-02 §5.3
    // step 5); the plaintext key and its secret half are never persisted or logged (FS-02 §11).
    //
    // The lookup key is the branch id, not an external DeviceId: regeneration must work before a
    // Device has ever activated (FS-02 §15 T-03), when its DeviceId is still NULL and so cannot
    // address it, and the internal DeviceRecordId is never exposed to any client (FS-02 §1.3). The
    // branch id is the only always-present, client-visible handle to the branch's single Device
    // (BR-002/CON-007). Returns null when no branch/device has that id (mapped to 404 by the API
    // layer, FS-02 §10.2). This operation never changes the Device's activation status — it touches
    // only Activation Key records — so it leaves an unactivated Device unactivated and, in the
    // reactivation flow (§5.8), an activated Device activated.
    Task<ActivationKeyRegenerationResult?> RegenerateActivationKeyAsync(
        Guid branchId,
        CancellationToken cancellationToken = default);

    // Validates and consumes a presented plaintext Activation Key, activating the associated Device
    // (FS-02 §5.5–§5.7, IP-01 §8 steps 3–6, T-19). In a single SQL Server transaction it: parses the
    // value into keyId.secret, rejecting a malformed value before any lookup (§5.5 step 3, §12);
    // resolves the Activation Key record by an indexed keyId lookup, never a scan/hash-compare across
    // every stored row (AC-14); verifies the presented secret against the stored salted hash;
    // confirms the record is Unconsumed (not Consumed or Invalidated); then marks the key Consumed,
    // assigns the Device its persistent DeviceId on first activation and retains the existing one on
    // reactivation (AC-7), marks it Activated, and issues a freshly generated shared secret stored
    // only in protected form (IDeviceSecretProtector, NFR-SEC-002/ADR-015).
    //
    // Returns a success carrying the DeviceId, the plaintext shared secret — disclosed exactly once,
    // never persisted in plaintext or logged (FS-02 §11) — and the BranchId; or a typed rejection.
    // The typed reason exists only so the service is testable: the API layer (T-20) collapses every
    // rejection to a single uniform 401 with no distinguishing detail (FS-02 §5.6, §13, AC-15). A
    // rejection never modifies any Device or Activation Key record (§5.6/§5.7). Replay of a Consumed
    // key is rejected (AC-4/AC-9); concurrent activations of one key are serialised so exactly one
    // succeeds (AC-16).
    Task<DeviceActivationResult> ActivateAsync(
        string activationKey,
        CancellationToken cancellationToken = default);

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

// The successful outcome of Activation Key regeneration (FS-02 §5.3, §10.2): the complete new
// plaintext key (`keyId.secret`), disclosed exactly once. Like BranchCreationResult, it carries the
// plaintext key on its own so a read path can never accidentally surface it; the plaintext key and
// its secret half are never re-derivable after this single disclosure and are never persisted or
// logged (FS-02 §11). A not-found branch/device is signalled by a null result rather than a variant
// on this type — there is no failure data to model.
public sealed record ActivationKeyRegenerationResult(string PlaintextActivationKey);

// The outcome of a device activation attempt (FS-02 §5.5–§5.7, IP-01 T-19). Either the Device was
// activated — Success carries the assigned/retained DeviceId, the freshly issued plaintext shared
// secret (disclosed once), and the BranchId — or the attempt was Rejected with a typed reason. The
// typed reason is an internal, testable distinction only: the API layer (T-20) maps every rejection
// to one uniform 401 that never reveals which check failed (FS-02 §5.6, §13, AC-15). A rejection
// carries no side effect on any Device or Activation Key record (§5.6/§5.7).
public sealed class DeviceActivationResult
{
    public bool Succeeded => Success is not null;
    public DeviceActivationSuccess? Success { get; }
    public DeviceActivationFailureReason? FailureReason { get; }

    private DeviceActivationResult(
        DeviceActivationSuccess? success, DeviceActivationFailureReason? failureReason)
    {
        Success = success;
        FailureReason = failureReason;
    }

    public static DeviceActivationResult Activated(DeviceActivationSuccess success) =>
        new(success ?? throw new ArgumentNullException(nameof(success)), null);

    public static DeviceActivationResult Rejected(DeviceActivationFailureReason reason) =>
        new(null, reason);
}

// The five distinguishable ways an activation attempt can fail (FS-02 §5.6/§5.7, §13). Malformed,
// UnknownKeyId, and IncorrectSecret are the §5.6 trio; Consumed and Invalidated are the §5.7 pair.
// All five collapse to the same externally observable outcome at the API layer (AC-15); the
// distinction exists only for internal control flow and test assertions.
public enum DeviceActivationFailureReason
{
    Malformed,
    UnknownKeyId,
    IncorrectSecret,
    Consumed,
    Invalidated,
}

// A successful activation's result. SharedSecret is the plaintext device shared secret, disclosed to
// the caller exactly once (FS-02 §5.5 step 8) and never persisted in plaintext or logged (§11) — the
// Backend stores only its protected form. ToString is overridden to redact the secret: a record's
// synthesized ToString prints every member, and an accidental interpolation of this result into a
// log line must never leak the secret (§11). DeviceId is the external, persistent identity assigned
// on first activation and retained on reactivation (AC-7); BranchId lets the endpoint (T-20) assemble
// the branch/camera configuration it returns. The internal DeviceRecordId is deliberately absent.
public sealed record DeviceActivationSuccess(Guid DeviceId, string SharedSecret, Guid BranchId)
{
    public override string ToString() =>
        $"{nameof(DeviceActivationSuccess)} {{ DeviceId = {DeviceId}, BranchId = {BranchId} }}";
}

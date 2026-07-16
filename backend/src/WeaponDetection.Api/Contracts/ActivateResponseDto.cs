using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.Api.Contracts;

// The outbound body for a successful POST /api/v1/activate (FS-02 §10.4, §5.5 step 8). The envelope's
// `data` carries exactly what the Agent needs and nothing more:
//
//  - DeviceId — the persistent, external device identity assigned on first activation and retained
//    across every reactivation (FS-02 §1.3, AC-7). This is the only device identifier ever exposed;
//    the internal DeviceRecordId is never a member of this DTO and so can never be serialized.
//  - SharedSecret — the freshly issued plaintext device shared secret. This is the ONLY response that
//    ever carries it, disclosed exactly once (FS-02 §5.5 step 8); the Backend stores only its
//    protected form (§11), and no read endpoint returns it.
//  - BranchId — the branch the activated device belongs to ("Branch ID or approved branch metadata",
//    FS-02 §10.4). BranchId is an already client-visible identifier.
//
// No operational-configuration fields are included: this milestone models no Configuration entity or
// operational parameters (IP-01 §4), and inventing values here is out of scope. Delivering the full
// branch/camera/operational configuration to the Agent is the concern of the later configuration
// feature; FS-02 only establishes that activation is where that exchange will occur.
public sealed record ActivateResponseDto(Guid DeviceId, string SharedSecret, Guid BranchId)
{
    public static ActivateResponseDto From(DeviceActivationSuccess success) =>
        new(success.DeviceId, success.SharedSecret, success.BranchId);
}

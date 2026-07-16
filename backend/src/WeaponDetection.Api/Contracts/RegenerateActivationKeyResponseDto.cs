using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.Api.Contracts;

// The outbound body for POST /api/v1/devices/{id}/activation-key/regenerate (FS-02 §10.2). The
// envelope's `data` carries exactly one field: the new complete plaintext Activation Key
// (`keyId.secret`), disclosed here for the only time (FS-02 §5.3 step 5). The field name matches
// BranchResponseDto.ActivationKey so the Dashboard reads the regenerated key from the same shape it
// reads the create-time key.
//
// This DTO deliberately carries nothing else — no old key, no key hash, no DeviceRecordId, no
// protected secret, no EF entity (FS-02 §1.3, §11): those simply have no member to be serialized
// into.
public sealed record RegenerateActivationKeyResponseDto(string ActivationKey)
{
    public static RegenerateActivationKeyResponseDto From(ActivationKeyRegenerationResult result) =>
        new(result.PlaintextActivationKey);
}

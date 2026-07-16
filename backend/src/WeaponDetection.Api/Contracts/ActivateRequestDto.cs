namespace WeaponDetection.Api.Contracts;

// IP-01 §11 ActivateRequestDto — the inbound body for POST /api/v1/activate (FS-02 §10.4). It
// carries the single complete plaintext Activation Key (`keyId.secret`); the Agent presents nothing
// else, since the key itself is the credential.
//
// ActivationKey is deliberately nullable and carries no [Required]/[NotBlank]/[MaxLength] attribute.
// That is intentional: a missing, empty, or malformed key must produce the SAME uniform 401 as an
// unknown keyId or a wrong secret (FS-02 §5.6, §13, AC-15). A validation attribute would instead
// short-circuit an empty value into a 400 from the model-state filter, giving an empty key a
// different observable outcome from every other rejected key and revealing that the request never
// reached activation. So all key-content checks (including "present and parseable") are left to
// IDeviceService.ActivateAsync, which returns Malformed for a null/blank/unparseable value and the
// controller maps every rejection to one identical response.
public sealed record ActivateRequestDto(string? ActivationKey);

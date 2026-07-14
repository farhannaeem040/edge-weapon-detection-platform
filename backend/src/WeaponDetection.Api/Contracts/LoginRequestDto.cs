using System.ComponentModel.DataAnnotations;
using WeaponDetection.Api.Validation;

namespace WeaponDetection.Api.Contracts;

// IP-01 §11 LoginRequestDto. MaxLength on CredentialIdentifier matches
// AdminUserConfiguration's column length (256); Password has no domain-imposed length (it is
// hashed, never stored), so a generous sensible upper bound is applied here purely as an
// oversized-payload guard.
public sealed record LoginRequestDto(
    [NotBlank]
    [MaxLength(256)]
    string CredentialIdentifier,

    [NotBlank]
    [MaxLength(512)]
    string Password);

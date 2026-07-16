using System.ComponentModel.DataAnnotations;
using WeaponDetection.Api.Validation;
using WeaponDetection.Domain;

namespace WeaponDetection.Api.Contracts;

// IP-01 §11 CreateBranchRequestDto — the inbound contract for POST /api/v1/branches (FS-02 §10.1).
// Presence/length are enforced here as DataAnnotations so [ApiController] rejects a blank or
// oversized submission as a 400 (via Program.cs's InvalidModelStateResponseFactory) before any
// controller code runs. The MaxLength limits are the Domain entities' own constants, so the DTO and
// the entity invariants cannot drift apart. RTSP URL *format* is deliberately not checked here — it
// is an Application-layer rule (IP-01 §11, BranchService) — so a well-formed but non-rtsp URL passes
// model validation and is rejected by the service, which the controller translates to a 400.
public sealed record CreateBranchRequestDto(
    [NotBlank]
    [MaxLength(Branch.NameMaxLength)]
    string Name,

    [NotBlank]
    [MaxLength(Branch.AddressMaxLength)]
    string Address,

    [NotBlank]
    [MaxLength(Branch.ContactDetailsMaxLength)]
    string ContactDetails,

    // At least one camera is required at branch creation (FS-02 §12). Each element is validated in
    // turn by the framework's recursive model validation.
    [Required(ErrorMessage = "At least one camera is required.")]
    [MinLength(1, ErrorMessage = "At least one camera is required.")]
    IReadOnlyList<CameraConfigDto> Cameras);

// IP-01 §11 CameraConfigDto — one camera in a branch-creation request. Only a name and an RTSP URL,
// the two fields FS-02/ARCH-001 attach to a Camera at creation (enablement is not a creation input).
public sealed record CameraConfigDto(
    [NotBlank]
    [MaxLength(Camera.NameMaxLength)]
    string Name,

    [NotBlank]
    [MaxLength(Camera.RtspUrlMaxLength)]
    string RtspUrl);

using System.ComponentModel.DataAnnotations;
using WeaponDetection.Api.Validation;
using WeaponDetection.Domain;

namespace WeaponDetection.Api.Contracts;

// IP-03 §3.5 UpdateBranchRequestDto — the inbound contract for PUT /api/v1/branches/{branchId}
// (FS-03 §10.1). It mirrors CreateBranchRequestDto's DataAnnotations so [ApiController] rejects a
// blank/oversized field or an empty camera list as a 400 (via Program.cs's
// InvalidModelStateResponseFactory) before any controller code runs. The MaxLength limits are the
// Domain entities' own constants, so the DTO and the entity invariants cannot drift apart. RTSP-URL
// *format*, and the business rules over camera identity (unknown/foreign/duplicate CameraId), are
// Application-layer checks in BranchService.UpdateBranchAsync — a request that is DTO-valid but
// business-invalid becomes a 400 there, exactly as the create path handles its equivalent.
//
// The branch id is taken from the route, not this body, so it is deliberately not a member here.
public sealed record UpdateBranchRequestDto(
    [NotBlank]
    [MaxLength(Branch.NameMaxLength)]
    string Name,

    [NotBlank]
    [MaxLength(Branch.AddressMaxLength)]
    string Address,

    [NotBlank]
    [MaxLength(Branch.ContactDetailsMaxLength)]
    string ContactDetails,

    // At least one camera must remain after an edit (FS-03 §5.2, AC-5); the only way to leave zero
    // is to submit zero, so an empty collection is rejected here.
    [Required(ErrorMessage = "At least one camera is required.")]
    [MinLength(1, ErrorMessage = "At least one camera is required.")]
    IReadOnlyList<UpdateCameraDto> Cameras);

// IP-03 §3.5 UpdateCameraDto — one camera in an update request. CameraId is the existing public
// Camera.CameraId when the camera already exists and is being edited (update in place), or null when
// it is being added (a new identity is generated on add) — FS-03 §5.2, §1.3. This is the identifier
// the read DTOs already return; no new identifier is introduced. Name/RtspUrl carry the same
// annotations as CameraConfigDto.
public sealed record UpdateCameraDto(
    Guid? CameraId,

    [NotBlank]
    [MaxLength(Camera.NameMaxLength)]
    string Name,

    [NotBlank]
    [MaxLength(Camera.RtspUrlMaxLength)]
    string RtspUrl);

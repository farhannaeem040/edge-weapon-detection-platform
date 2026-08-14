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

    // FS-12 §4 — the Jetson's reachable address. Required at branch creation: the Device reserved
    // here is the network host for every one of the Branch's Camera outputs, and a Branch whose
    // annotated streams have no address is not usable. Format (no scheme/port/path/credentials) is
    // an Application-layer rule enforced by Device.RequireJetsonHost, not duplicated here.
    [NotBlank]
    [MaxLength(Device.JetsonHostMaxLength)]
    string JetsonHost,

    // FS-12 §4 — optional; null means the 8554 default. Range is enforced by
    // Device.RequireRtspOutputPort so the DTO and the entity invariant cannot drift.
    int? RtspOutputPort,

    // At least one camera is required at branch creation (FS-02 §12). Each element is validated in
    // turn by the framework's recursive model validation.
    [Required(ErrorMessage = "At least one camera is required.")]
    [MinLength(1, ErrorMessage = "At least one camera is required.")]
    IReadOnlyList<CameraConfigDto> Cameras);

// IP-01 §11 CameraConfigDto — one camera in a branch-creation request. Only a name and an RTSP URL,
// the two fields FS-02/ARCH-001 attach to a Camera at creation (enablement is not a creation input).
//
// FS-12 §3 adds CameraKey. SourceOrder and Enabled are deliberately still NOT creation inputs: order
// remains auto-assigned from array position and enablement remains defaulted, exactly as FS-02
// defines. Accepting them here would change existing branch-creation semantics for no requirement in
// this feature (IP-14 §1 decision 1).
public sealed record CameraConfigDto(
    [NotBlank]
    [MaxLength(Camera.NameMaxLength)]
    string Name,

    [NotBlank]
    [MaxLength(Camera.RtspUrlMaxLength)]
    string RtspUrl,

    // The administrator-entered public mount identifier (FS-12 §3). Presence and length only here;
    // the pattern, the reserved list and Branch-scoped uniqueness are Application-layer rules, so a
    // malformed key produces its own named error code rather than a generic model-state 400.
    [NotBlank]
    [MaxLength(Camera.CameraKeyMaxLength)]
    string CameraKey);

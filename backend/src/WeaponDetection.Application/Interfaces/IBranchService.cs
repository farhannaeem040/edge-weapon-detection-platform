using WeaponDetection.Domain;

namespace WeaponDetection.Application.Interfaces;

// Branch/Camera/Device orchestration behind an Application interface (ARCH-001 §10.1): the API
// layer (T-16) depends on this, never on EF or the DbContext directly. Two responsibilities today:
//
//  - CreateBranchAsync (T-15): creates a Branch with its Cameras, the single reserved (unactivated)
//    Device, and that Device's first Activation Key — all in one database transaction — and returns
//    the created branch/camera/device summary together with the complete plaintext Activation Key,
//    disclosed exactly once (FS-02 §5.1).
//  - ListBranchesAsync / GetBranchAsync (T-16): read-only branch/camera/device projections for the
//    Dashboard's list and detail views (FS-02 §5.4, §10.3). These never carry the plaintext
//    Activation Key or the internal DeviceRecordId (FS-02 §1.3, §7).
//
// Neither exposes HTTP concerns (status codes, request/response DTOs); that translation belongs to
// the API layer (T-16).
public interface IBranchService
{
    Task<BranchCreationResult> CreateBranchAsync(
        NewBranchRequest request,
        CancellationToken cancellationToken = default);

    // Every branch with its cameras and its single Device summary, for the Dashboard branch list
    // (FS-02 §5.4). Ordering is by creation is not guaranteed by the store; callers that need a
    // stable order impose it. Returns an empty list when no branches exist.
    Task<IReadOnlyList<BranchView>> ListBranchesAsync(
        CancellationToken cancellationToken = default);

    // A single branch with its cameras and Device summary, or null when no branch has that id
    // (mapped to 404 by the API layer, FS-02 §10.3).
    Task<BranchView?> GetBranchAsync(
        Guid branchId,
        CancellationToken cancellationToken = default);

    // Edits a branch and reconciles its cameras in a single SQL Server transaction (FS-03 §5.1,
    // §5.2, §11). The request is the desired end-state: the branch's scalar fields plus the complete
    // intended camera collection, each camera either carrying an existing CameraId (update in place)
    // or none (add). Any stored camera whose CameraId is absent from the request is removed, subject
    // to the "at least one camera remains" rule.
    //
    // The Device record and every Activation Key record are never written by this operation — an
    // edit preserves the Device identity, activation status, protected shared secret, and all key
    // records unchanged (FS-03 §5.3, AC-7, AC-8; ADR-015). A failure at any point rolls the whole
    // update back (AC-6).
    //
    // Expected non-success outcomes are first-class results, not exceptions: an unknown branch is
    // NotFound (→ 404), and a business-invalid request (zero cameras, an unknown/foreign/duplicate
    // CameraId, or an invalid camera field) is Invalid (→ 400). This mirrors how the activation and
    // regeneration paths model their expected non-success outcomes rather than throwing.
    Task<BranchUpdateResult> UpdateBranchAsync(
        UpdateBranchRequest request,
        CancellationToken cancellationToken = default);
}

// The Application-layer branch-creation request. Deliberately independent of any API DTO or EF
// type: the API layer (T-16) maps its inbound CreateBranchRequestDto onto this, and this is what
// the service validates. "Contact details" is a single free-text field (FS-02 §19 leaves its exact
// composition open; ARCH-001 §13.1 lists it as one field).
public sealed record NewBranchRequest(
    string Name,
    string Address,
    string ContactDetails,
    IReadOnlyList<NewCameraRequest> Cameras);

// One camera configuration in a branch-creation request. Only a name and an RTSP URL are carried:
// the same two fields ARCH-001/FS-02 attach to a Camera at creation time. Enablement is not a
// creation-time input (Camera defaults to enabled).
public sealed record NewCameraRequest(string Name, string RtspUrl);

// The Application-layer branch-update request (FS-03 §5.1, §10.1). Like NewBranchRequest it is
// independent of any API DTO or EF type. BranchId identifies the target; the Cameras collection is
// the full desired end-state, reconciled against the stored cameras by CameraId (§5.2).
public sealed record UpdateBranchRequest(
    Guid BranchId,
    string Name,
    string Address,
    string ContactDetails,
    IReadOnlyList<CameraMutation> Cameras);

// One camera in an update request. CameraId is the existing public Camera.CameraId when the camera
// already exists and is being edited (update in place), or null when it is being added (a new
// identity is generated on add). This is the already-public identifier the read DTOs return
// (FS-03 §1.3) — no new identifier is introduced. Name/RtspUrl are validated exactly as at creation.
public sealed record CameraMutation(Guid? CameraId, string Name, string RtspUrl);

// The outcome of an update attempt. Exactly one of the three states holds:
//
//  - Updated  — the branch was edited; Branch carries the re-projected read view.
//  - NotFound — no branch has the requested id (→ 404).
//  - Invalid  — the request is business-invalid (zero cameras; a camera field failing validation;
//               an unknown, foreign, or duplicated CameraId) (→ 400). The reason is intentionally
//               not carried on the wire: the API layer returns one generic validation envelope that
//               never echoes a submitted value (FS-03 §6, §12).
public sealed class BranchUpdateResult
{
    public BranchUpdateStatus Status { get; }
    public BranchView? Branch { get; }

    private BranchUpdateResult(BranchUpdateStatus status, BranchView? branch)
    {
        Status = status;
        Branch = branch;
    }

    public static BranchUpdateResult Updated(BranchView branch) =>
        new(BranchUpdateStatus.Updated, branch ?? throw new ArgumentNullException(nameof(branch)));

    public static BranchUpdateResult NotFound() => new(BranchUpdateStatus.NotFound, null);

    public static BranchUpdateResult Invalid() => new(BranchUpdateStatus.Invalid, null);
}

public enum BranchUpdateStatus
{
    Updated,
    NotFound,
    Invalid,
}

// The successful outcome of branch creation: the created branch/camera/device summary plus the
// plaintext Activation Key, shown exactly once (FS-02 §5.1 step 6, §10.1). Invalid input never
// reaches this type — the service rejects it by throwing before any persistence, exactly as
// AuthService guards its inputs — so there is no failure variant to model here.
//
// PlaintextActivationKey is the only place the complete key appears after generation; the API layer
// forwards it in the single create response and it is never re-derivable afterward (FS-02 §11).
public sealed record BranchCreationResult(
    BranchView Branch,
    string PlaintextActivationKey);

// The persisted branch/camera/device view — the shared read model for both the create response and
// the list/detail read endpoints. It never carries the internal DeviceRecordId (never exposed by
// any API, FS-02 §1.3) and never carries the plaintext Activation Key (which BranchCreationResult
// holds separately, so a read result cannot accidentally disclose it). For a freshly created branch
// the Device's external DeviceId is null because activation has not happened yet (FS-02 §5.1).
public sealed record BranchView(
    Guid BranchId,
    string Name,
    string Address,
    string ContactDetails,
    IReadOnlyList<CameraView> Cameras,
    DeviceSummaryView Device);

// RtspUrl is the value as configured — it may embed credentials (rtsp://user:pass@host/...). The
// Application layer returns the domain-accurate value; deciding what is safe to serialize to a
// client is an API-presentation concern, so the API layer redacts any embedded credentials before
// putting a camera on the wire (T-16 security constraint, ARCH-001 §15.6).
public sealed record CameraView(
    Guid CameraId,
    string Name,
    string RtspUrl,
    bool Enabled);

// The Device as summarised within a branch. DeviceId is null until first activation; ActivationStatus
// is Unactivated at branch creation. LastKnownAddress is null until first operational contact.
// DeviceRecordId is intentionally absent (FS-02 §1.3).
public sealed record DeviceSummaryView(
    Guid? DeviceId,
    DeviceActivationStatus ActivationStatus,
    string? LastKnownAddress);

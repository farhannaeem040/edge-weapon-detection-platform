using WeaponDetection.Domain;

namespace WeaponDetection.Application.Interfaces;

// FS-02 §5.1, IP-01 §8/T-15: creates a Branch with its Cameras, the single reserved (unactivated)
// Device, and that Device's first Activation Key — all in one database transaction — and returns
// the created branch/camera/device summary together with the complete plaintext Activation Key,
// disclosed exactly once. Does not expose HTTP concerns (status codes, request/response DTOs);
// that translation belongs to the API layer (T-16).
public interface IBranchService
{
    Task<BranchCreationResult> CreateBranchAsync(
        NewBranchRequest request,
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

// The successful outcome of branch creation: the created branch/camera/device summary plus the
// plaintext Activation Key, shown exactly once (FS-02 §5.1 step 6, §10.1). Invalid input never
// reaches this type — the service rejects it by throwing before any persistence, exactly as
// AuthService guards its inputs — so there is no failure variant to model here.
//
// PlaintextActivationKey is the only place the complete key appears after generation; the API layer
// forwards it in the single create response and it is never re-derivable afterward (FS-02 §11).
public sealed record BranchCreationResult(
    CreatedBranch Branch,
    string PlaintextActivationKey);

// The persisted branch/camera/device view returned to the caller. It never carries the internal
// DeviceRecordId (never exposed by any API, FS-02 §1.3) and, for a freshly created branch, the
// Device's external DeviceId is always null because activation has not happened yet (FS-02 §5.1).
public sealed record CreatedBranch(
    Guid BranchId,
    string Name,
    string Address,
    string ContactDetails,
    IReadOnlyList<CreatedCamera> Cameras,
    CreatedDeviceSummary Device);

public sealed record CreatedCamera(
    Guid CameraId,
    string Name,
    string RtspUrl,
    bool Enabled);

// DeviceId is null until first activation; ActivationStatus is Unactivated at branch creation.
// LastKnownAddress is null until first operational contact. DeviceRecordId is intentionally absent.
public sealed record CreatedDeviceSummary(
    Guid? DeviceId,
    DeviceActivationStatus ActivationStatus,
    string? LastKnownAddress);

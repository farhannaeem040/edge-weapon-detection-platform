using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.Api.Contracts;

// IP-01 §11 BranchResponseDto — the outbound branch representation for the create response
// (POST /api/v1/branches) and the read responses (GET /api/v1/branches, GET /api/v1/branches/{id}).
//
// Two disclosure rules are encoded in the two factory methods rather than left to each call site
// (FS-02 §1.3, §7, §10.1):
//
//  - ActivationKey (the complete plaintext key) appears ONLY in the create response, exactly once
//    (ForCreate). Every read (ForRead) leaves it null, so it is omitted from the wire entirely (the
//    host's WhenWritingNull JSON policy drops null members). There is no code path that puts the key
//    in a list/detail response.
//  - The internal DeviceRecordId is never a member of this DTO or any nested one, so it cannot be
//    serialized on any path.
public sealed record BranchResponseDto(
    Guid BranchId,
    string Name,
    string Address,
    string ContactDetails,
    IReadOnlyList<CameraResponseDto> Cameras,
    DeviceSummaryDto Device,
    string? ActivationKey)
{
    // The read shape: no plaintext Activation Key (FS-02 §5.4, §10.3).
    public static BranchResponseDto ForRead(BranchView branch) =>
        Map(branch, activationKey: null);

    // The create shape: carries the complete plaintext Activation Key for its single disclosure
    // (FS-02 §5.1 step 6, §10.1).
    public static BranchResponseDto ForCreate(BranchView branch, string plaintextActivationKey) =>
        Map(branch, plaintextActivationKey);

    private static BranchResponseDto Map(BranchView branch, string? activationKey) =>
        new(
            branch.BranchId,
            branch.Name,
            branch.Address,
            branch.ContactDetails,
            branch.Cameras.Select(CameraResponseDto.From).ToList(),
            DeviceSummaryDto.From(branch.Device),
            activationKey);
}

// A camera as returned to a client. RtspUrl is redacted of any embedded credentials before it
// leaves the Backend (RtspUrlSanitizer) — the stored value may contain userinfo, which is a secret.
public sealed record CameraResponseDto(
    Guid CameraId,
    string Name,
    string RtspUrl,
    bool Enabled)
{
    public static CameraResponseDto From(CameraView camera) =>
        new(camera.CameraId, camera.Name, RtspUrlSanitizer.Redact(camera.RtspUrl), camera.Enabled);
}

// The Device as summarised within a branch (FS-02 §10.1/§10.3). DeviceId is present only once the
// Device is activated (null otherwise, and then omitted from the wire); ActivationStatus is the
// enum's name ("Unactivated"/"Activated") to match FS-02's literal values rather than a bare
// integer. The internal DeviceRecordId and the ProtectedSharedSecret are never included.
public sealed record DeviceSummaryDto(
    Guid? DeviceId,
    string ActivationStatus,
    string? LastKnownAddress)
{
    public static DeviceSummaryDto From(DeviceSummaryView device) =>
        new(device.DeviceId, device.ActivationStatus.ToString(), device.LastKnownAddress);
}

using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.Api.Contracts;

// The outbound representation for GET /api/v1/devices/{id} (FS-02 §10.3). A device is addressable by
// this route only through its external DeviceId, which exists only once activated, so DeviceId is
// always present here (non-null) and ActivationStatus is always "Activated". BranchId correlates the
// device to its branch and is already a client-visible identifier. The internal DeviceRecordId and
// the ProtectedSharedSecret are never included (FS-02 §1.3, §11).
public sealed record DeviceDetailResponseDto(
    Guid DeviceId,
    Guid BranchId,
    string ActivationStatus,
    string? LastKnownAddress)
{
    public static DeviceDetailResponseDto From(DeviceDetailView device) =>
        new(device.DeviceId, device.BranchId, device.ActivationStatus.ToString(), device.LastKnownAddress);
}

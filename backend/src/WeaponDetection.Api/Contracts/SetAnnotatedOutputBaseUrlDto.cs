using System.ComponentModel.DataAnnotations;
using WeaponDetection.Application.Interfaces;
using WeaponDetection.Domain;

namespace WeaponDetection.Api.Contracts;

// FS-12 §4 — the request body for PUT /api/v1/devices/{branchId}/network.
//
// Replaces the former SetAnnotatedOutputBaseUrlRequestDto (IP-14 §1 decision 3). Storing the host and
// the port separately rather than one pre-composed URL string is the point of the change: the two are
// independent facts, and the composed base is now derived from them rather than being state a client
// has to assemble — and the Backend re-parse — correctly.
//
// `JetsonHost` is nullable so an Admin can clear a previously configured address, which returns every
// Camera's outputStreamUrl to null and lets the UI render "not configured" rather than advertising
// something unreachable. Clearing the host clears the port with it.
//
// Deliberately not named for Tailscale: the POC's Tailscale IP is only one of the values this field
// legitimately carries, alongside LAN IPs, routed private IPs, public IPs and DNS hostnames.
//
// Every format rule — no scheme, no port, no path, no query, no fragment, no credentials, IPv6
// handling — is enforced by the Device entity itself, not duplicated here. Only the length bound is
// mirrored, so an oversized value is rejected as a cheap 400 before any database work.
public sealed record SetDeviceNetworkRequestDto(
    [MaxLength(Device.JetsonHostMaxLength)]
    string? JetsonHost,
    int? RtspOutputPort);

// The stored result. Echoes the persisted host and port plus the *computed* base URL, so a client can
// see exactly what its Cameras' output URLs will now compose to — including IPv6 bracketing, which it
// should not have to reproduce. Carries no credential and no internal DeviceRecordId; DeviceId is
// null when the Device has not activated yet, which is a success, not an error.
public sealed record SetDeviceNetworkResponseDto(
    Guid BranchId,
    Guid? DeviceId,
    string? JetsonHost,
    int? RtspOutputPort,
    string? AnnotatedOutputBaseUrl)
{
    public static SetDeviceNetworkResponseDto From(DeviceNetworkUpdate update) =>
        new(
            update.BranchId,
            update.DeviceId,
            update.JetsonHost,
            update.RtspOutputPort,
            update.AnnotatedOutputBaseUrl);
}

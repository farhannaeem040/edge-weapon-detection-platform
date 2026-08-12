namespace WeaponDetection.Api.Contracts;

// The inbound body for POST /api/v1/sync/events (FS-06 §4.1). DeviceId never appears here — it comes
// only from the authenticated X-Device-Id header (FS-06 §4.3), exactly as the credential-validation
// endpoint takes no body at all. Events is deliberately nullable and unattributed for the same reason
// ActivateRequestDto's ActivationKey is: a missing/null Events list must be handled by the controller
// (400) rather than surfacing a different observable shape from an empty list, since the caller
// (the sync worker) can tell them apart in its own retry logic.
public sealed record SyncEventsRequest(List<DetectionEventDto>? Events);

namespace WeaponDetection.Api.Contracts;

// The outbound body for a successful POST /api/v1/sync/events (FS-06 §4.2, `data` shown inside the
// uniform envelope). One result per submitted event, in the same order the Agent sent them — the
// Agent matches by EventId, not by position, but a stable order keeps logs/traces easy to follow.
public sealed record SyncEventsResponse(List<SyncEventResultDto> Results);

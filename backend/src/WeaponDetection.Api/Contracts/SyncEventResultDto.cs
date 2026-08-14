namespace WeaponDetection.Api.Contracts;

// One event's outcome within a POST /api/v1/sync/events response (FS-06 §4.2, extended FS-09 §5).
// Outcome is one of the four approved wire strings — "accepted", "duplicate", "rejected",
// "quota_exceeded" — defined as constants on SyncEventOutcomeNames (Application layer) so the
// controller and service share one spelling. AlertId is populated for "accepted" (the newly created
// Alert) and "duplicate" (the pre-existing Alert); ErrorCode is populated for "rejected" and
// "quota_exceeded"; Quota is populated only for "quota_exceeded".
public sealed record SyncEventResultDto(
    Guid EventId, string Outcome, Guid? AlertId, string? ErrorCode, SyncEventQuotaDto? Quota = null);

// FS-09 §5: the safe quota context returned alongside a "quota_exceeded" outcome — the configured
// maximum and the resolved branch-local (or UTC-fallback) quota day only. No internal database
// identifiers or counts are ever exposed.
public sealed record SyncEventQuotaDto(int Maximum, string LocalDate);

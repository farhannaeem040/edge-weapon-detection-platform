namespace WeaponDetection.Api.Contracts;

// The Alert list's query-string parameters (FS-10 §9.2). Every field is a raw, optional string/value
// — parsing and whitelist validation happen in the controller (AlertController.List), never here and
// never via a dynamically-built SQL fragment.
public sealed record AlertListRequestDto(
    int? Page,
    int? PageSize,
    DateTime? FromUtc,
    DateTime? ToUtc,
    string? ClassName,
    Guid? BranchId,
    Guid? CameraId,
    string? Status,
    bool? SnapshotAvailable,
    string? SortBy,
    bool? SortDescending);

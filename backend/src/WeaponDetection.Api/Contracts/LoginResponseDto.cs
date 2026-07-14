namespace WeaponDetection.Api.Contracts;

// IP-01 §11 LoginResponseDto — data contains only the issued JWT (FS-01 §9.1); no expiry,
// issued-at, or token-type field is added since neither FS-01 nor IP-01 requires one here.
public sealed record LoginResponseDto(string Token);

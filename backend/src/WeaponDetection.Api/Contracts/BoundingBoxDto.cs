namespace WeaponDetection.Api.Contracts;

// The Agent-reported bounding box for one detection event (FS-06 §4.1). Values are taken as
// submitted — bounds validation (non-negative, box within frame) is an Application-layer concern
// (AlertSyncService), not this DTO's job, mirroring how ActivateRequestDto leaves content validation
// to the service it feeds.
public sealed record BoundingBoxDto(double Left, double Top, double Width, double Height);

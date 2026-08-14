namespace WeaponDetection.Infrastructure.Media;

// Bound from configuration section "MediaGateway" (MediaGateway:BaseUrl / MediaGateway__BaseUrl) —
// mirrors AlertSnapshotStorageOptions' shape (FS-14 §5, IP-16 T-8). BaseUrl is the media gateway's
// internal control-API origin (e.g. "http://mediamtx:9997") — reachable only over the compose
// `internal` network, never published to the host.
public class MediaGatewayOptions
{
    public const string SectionName = "MediaGateway";

    public string? BaseUrl { get; set; }

    // How long a MediaMTX on-demand path is kept pulling its source after its last WebRTC viewer
    // disconnects (FS-14 §5) — short enough that this feature adds no meaningful idle server/Jetson
    // load, long enough that a brief reconnect (e.g. a mode switch) doesn't force a fresh RTSP pull.
    public int SourceOnDemandCloseAfterSeconds { get; set; } = 30;
}

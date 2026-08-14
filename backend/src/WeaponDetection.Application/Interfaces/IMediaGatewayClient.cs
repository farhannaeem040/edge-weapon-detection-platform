namespace WeaponDetection.Application.Interfaces;

// FS-14 §5, IP-16 T-5. The only thing in this codebase that talks to the media gateway (MediaMTX)
// control API. Deliberately narrow — "ensure this path exists, pointed at this source" — so
// LiveStreamService never has to know MediaMTX's own request/response shape.
public interface IMediaGatewayClient
{
    // Idempotent: safe to call every time a stream is requested for the same (pathName, sourceUrl),
    // even if the path was already created by an earlier request. sourceUrl is passed through to the
    // gateway only — never returned to a caller of LiveStreamService.
    Task EnsurePathAsync(string pathName, string sourceUrl, CancellationToken cancellationToken = default);
}

// Thrown when the gateway cannot be reached or rejects the request for a reason that isn't simply
// "the path already exists." Caught by LiveStreamService and mapped to LiveStreamOutcomeKind.
// GatewayUnavailable — never allowed to surface as an unhandled 500 with gateway internals attached.
public sealed class MediaGatewayException : Exception
{
    public MediaGatewayException(string message)
        : base(message)
    {
    }
}

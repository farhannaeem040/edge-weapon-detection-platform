using System.Net.Http.Json;
using System.Text.Json.Serialization;
using Microsoft.Extensions.Options;
using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.Infrastructure.Media;

// FS-14 §5, IP-16 T-5. Talks to MediaMTX's own config API (bound internally only, never published to
// the host — see compose.yaml/mediamtx.yml). "Ensure" is implemented as add-then-patch rather than a
// single call: MediaMTX's add endpoint fails if the path already exists (deterministic path names
// mean a second request for the same Camera+mode is expected, not exceptional), so a failed add is
// retried as a patch (update) before this is treated as a real failure. sourceUrl — which may embed
// credentials for monitoring-mode Cameras — is sent to MediaMTX only, never logged, never returned.
public class MediaMtxGatewayClient : IMediaGatewayClient
{
    private readonly HttpClient _httpClient;
    private readonly MediaGatewayOptions _options;

    public MediaMtxGatewayClient(HttpClient httpClient, IOptions<MediaGatewayOptions> options)
    {
        _httpClient = httpClient ?? throw new ArgumentNullException(nameof(httpClient));
        _options = options?.Value ?? throw new ArgumentNullException(nameof(options));
    }

    public async Task EnsurePathAsync(
        string pathName, string sourceUrl, CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrWhiteSpace(pathName))
        {
            throw new ArgumentException("Path name is required.", nameof(pathName));
        }

        if (string.IsNullOrWhiteSpace(sourceUrl))
        {
            throw new ArgumentException("Source URL is required.", nameof(sourceUrl));
        }

        var payload = new MediaMtxPathConfig(
            sourceUrl, SourceOnDemand: true, $"{_options.SourceOnDemandCloseAfterSeconds}s");

        using var addResponse = await _httpClient.PostAsJsonAsync(
            $"v3/config/paths/add/{Uri.EscapeDataString(pathName)}", payload, cancellationToken);
        if (addResponse.IsSuccessStatusCode)
        {
            return;
        }

        using var patchResponse = await _httpClient.PatchAsJsonAsync(
            $"v3/config/paths/patch/{Uri.EscapeDataString(pathName)}", payload, cancellationToken);
        if (patchResponse.IsSuccessStatusCode)
        {
            return;
        }

        // Never includes sourceUrl in the exception message — it may embed credentials.
        throw new MediaGatewayException(
            $"Media gateway rejected path '{pathName}' (add={(int)addResponse.StatusCode}, "
                + $"patch={(int)patchResponse.StatusCode}).");
    }

    // MediaMTX's config API expects exactly these lowerCamelCase JSON field names — pinned with
    // explicit attributes rather than relying on ambient JsonSerializerOptions, so this keeps working
    // regardless of how the Backend's own controllers are configured to (de)serialize.
    private sealed record MediaMtxPathConfig(
        [property: JsonPropertyName("source")] string Source,
        [property: JsonPropertyName("sourceOnDemand")] bool SourceOnDemand,
        [property: JsonPropertyName("sourceOnDemandCloseAfter")] string SourceOnDemandCloseAfter);
}

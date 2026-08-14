using Microsoft.Extensions.Options;

namespace WeaponDetection.Infrastructure.Media;

// Runs eagerly at application startup via ValidateOnStart() (see DependencyInjection.cs), mirroring
// AlertSnapshotStorageOptionsValidator's shape exactly.
public class MediaGatewayOptionsValidator : IValidateOptions<MediaGatewayOptions>
{
    public ValidateOptionsResult Validate(string? name, MediaGatewayOptions options)
    {
        if (string.IsNullOrWhiteSpace(options.BaseUrl))
        {
            return ValidateOptionsResult.Fail("MediaGateway:BaseUrl is required.");
        }

        if (!Uri.TryCreate(options.BaseUrl, UriKind.Absolute, out var uri)
            || uri.Scheme is not ("http" or "https"))
        {
            return ValidateOptionsResult.Fail("MediaGateway:BaseUrl must be an absolute http(s) URL.");
        }

        if (options.SourceOnDemandCloseAfterSeconds <= 0)
        {
            return ValidateOptionsResult.Fail(
                "MediaGateway:SourceOnDemandCloseAfterSeconds must be positive.");
        }

        return ValidateOptionsResult.Success;
    }
}

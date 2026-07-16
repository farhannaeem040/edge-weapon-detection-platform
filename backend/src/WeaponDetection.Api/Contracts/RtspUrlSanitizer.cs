namespace WeaponDetection.Api.Contracts;

// A camera's RTSP URL is stored exactly as the Admin configured it, and it may embed credentials in
// its userinfo component (rtsp://user:pass@host:554/stream). Those credentials are a secret that
// must not be handed back on the wire — ARCH-001 §15.6 forbids credentials appearing in responses,
// and the T-16 security constraint calls for omission or redaction of an embedded RTSP credential
// unless the API contract explicitly requires the full value (FS-02 does not).
//
// FS-02 §10.1/§10.3 place the camera's RtspUrl in create and read responses so the Admin can
// recognise the camera, so this redacts rather than omits: the credential-bearing userinfo is
// replaced with a fixed "***" marker while the host, port, and path — which identify the camera and
// carry no secret — are preserved verbatim. The transformation is done on the raw string rather than
// via UriBuilder so a valid URL is never rewritten (re-encoded, default port added, trailing slash
// changed); only the userinfo span is touched.
public static class RtspUrlSanitizer
{
    private const string RedactionMarker = "***";
    private const string SchemeSeparator = "://";

    // Returns the URL with any embedded userinfo replaced by "***"; a URL with no userinfo is
    // returned unchanged. A value that does not contain a "scheme://" separator is returned as-is:
    // stored camera URLs are validated as absolute rtsp:// URIs at creation (BranchService), so this
    // path is not reached for a persisted camera, but returning the input unchanged is the safe
    // default because a string with no authority component cannot carry userinfo credentials.
    public static string Redact(string rtspUrl)
    {
        if (string.IsNullOrEmpty(rtspUrl))
        {
            return rtspUrl;
        }

        var schemeIndex = rtspUrl.IndexOf(SchemeSeparator, StringComparison.Ordinal);
        if (schemeIndex < 0)
        {
            return rtspUrl;
        }

        var authorityStart = schemeIndex + SchemeSeparator.Length;

        // The authority ends at the first '/' (path), or the whole remainder if there is no path.
        var authorityEnd = rtspUrl.IndexOf('/', authorityStart);
        if (authorityEnd < 0)
        {
            authorityEnd = rtspUrl.Length;
        }

        // The userinfo, when present, is delimited from the host by the first '@' within the
        // authority. No '@' in that span means there are no embedded credentials to redact.
        var atIndex = rtspUrl.IndexOf('@', authorityStart);
        if (atIndex < 0 || atIndex >= authorityEnd)
        {
            return rtspUrl;
        }

        // Keep everything up to "scheme://", drop the userinfo, and resume from the '@' so the
        // host/port/path survive intact: rtsp://user:pass@host/s -> rtsp://***@host/s.
        return string.Concat(
            rtspUrl.AsSpan(0, authorityStart),
            RedactionMarker,
            rtspUrl.AsSpan(atIndex));
    }
}

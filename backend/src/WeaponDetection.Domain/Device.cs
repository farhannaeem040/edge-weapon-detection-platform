namespace WeaponDetection.Domain;

// The single Jetson device reserved for a Branch (FS-02 §1.3, ARCH-001 §13.1). Created together
// with its Branch and left unactivated until an Agent presents a valid Activation Key.
//
// Two identifiers, and the difference between them is the whole point of this entity (FS-02 §1.3):
//
//  - DeviceRecordId — the internal primary key. It exists from branch creation, because the
//    Activation Key record needs something to point at before any device has activated. It is
//    never returned by any API, never logged, and never rendered.
//  - DeviceId — the external, persistent identity an Agent uses in its `X-Device-Id` header. It is
//    NULL until the first successful activation, assigned exactly once at that moment, and then
//    retained unchanged forever — including across a reactivation (AC-7, §5.8). This is what keeps
//    historical alerts and health records correlated when a Jetson unit is replaced.
//
// ProtectedSharedSecret holds the *protected* form only. The plaintext shared secret never enters
// this entity: the Application layer protects it (IDeviceSecretProtector, IP-01 §7) before calling
// Activate. Nothing here is ever interpolated into an exception message (FS-02 §11 — secrets are
// never written to logs, and an exception is a log entry waiting to happen).
public class Device
{
    public const int ProtectedSharedSecretMaxLength = 1024;
    public const int LastKnownAddressMaxLength = 256;
    public const int AnnotatedOutputBaseUrlMaxLength = 512;

    // FS-12 §4. 255 is the maximum length of a DNS name, which bounds every accepted form (an IPv4
    // or IPv6 literal is far shorter).
    public const int JetsonHostMaxLength = 255;
    public const int DefaultRtspOutputPort = 8554;

    public Guid DeviceRecordId { get; private set; }
    public Guid? DeviceId { get; private set; }
    public Guid BranchId { get; private set; }
    public DeviceActivationStatus ActivationStatus { get; private set; }
    public string? ProtectedSharedSecret { get; private set; }
    public string? LastKnownAddress { get; private set; }

    // FS-11 §11: the externally reachable base of this Device's own annotated-output RTSP server
    // (e.g. "rtsp://100.98.226.80:8554"), shared by every Camera on the Device. Discovery metadata
    // only — it is NOT a credential, NOT a Camera input URL, and deliberately NOT part of the Agent's
    // pipelineconfigurationVersion: changing the advertised host/port leaves every Bridge mount path
    // identical, so it must never provoke a pipeline restart.
    //
    // NULL until an Admin configures it (a Device is created reserved/unactivated, long before its
    // Jetson's reachable address is known). NULL means "not configured", never "guess one".
    public string? AnnotatedOutputBaseUrl { get; private set; }

    // FS-12 §2/§4 — the structured replacement for AnnotatedOutputBaseUrl. Two independent facts
    // stored as two fields instead of one opaque URL string that had to be re-parsed to be useful.
    //
    // Deliberately NOT named for Tailscale (FS-12 §1). Tailscale is only the current POC network
    // simulation; this field must stay equally valid for a LAN IP, a routed private IP, a public IP
    // and a DNS hostname. Naming it TailscaleIp would bake a temporary deployment choice into the
    // schema.
    //
    // Both NULL until an Admin configures them — a Device is created reserved, long before its
    // Jetson's reachable address is known. NULL means "not configured", never "guess one".
    public string? JetsonHost { get; private set; }

    public int? RtspOutputPort { get; private set; }

    // Required by EF Core for materialization; never used by application code.
    private Device()
    {
    }

    public Device(Guid branchId)
    {
        if (branchId == Guid.Empty)
        {
            throw new ArgumentException("Branch id is required.", nameof(branchId));
        }

        DeviceRecordId = Guid.NewGuid();
        BranchId = branchId;

        // The reserved, pre-activation state (FS-02 §5.1 step 4). Every one of these is what
        // "unactivated" means, and no caller may choose otherwise at construction.
        DeviceId = null;
        ActivationStatus = DeviceActivationStatus.Unactivated;
        ProtectedSharedSecret = null;
        LastKnownAddress = null;
        AnnotatedOutputBaseUrl = null;
        JetsonHost = null;
        RtspOutputPort = null;
    }

    // FS-12 §4. Sets (or clears, with a null host) the Jetson's reachable address and RTSP output
    // port. Like SetAnnotatedOutputBaseUrl before it, this touches nothing else: DeviceId,
    // ActivationStatus, ProtectedSharedSecret and every Camera are untouched, because this is
    // client-facing discovery metadata. Changing it must never disturb identity, credentials,
    // activation state, or the running pipeline — and because host/port are excluded from the
    // Agent's configurationVersion (FS-12 §6), it cannot provoke a Bridge restart.
    public void SetNetworkConfiguration(string? jetsonHost, int? rtspOutputPort)
    {
        if (string.IsNullOrWhiteSpace(jetsonHost))
        {
            // Clearing the host clears the port too: a port without a host composes nothing, and
            // leaving a stale port behind would be state that no longer means anything.
            JetsonHost = null;
            RtspOutputPort = null;
            return;
        }

        JetsonHost = RequireJetsonHost(jetsonHost);
        RtspOutputPort = RequireRtspOutputPort(rtspOutputPort ?? DefaultRtspOutputPort);
    }

    // FS-12 §4. A bare host: no scheme, no port, no path, no query, no fragment, no credentials.
    //
    // Validated by construction rather than by regex — the host is placed into a throwaway absolute
    // URI and the parser is asked whether it survived unchanged. That rejects every embedded-port,
    // embedded-path and embedded-credential form in one step, without a hand-written pattern that
    // would have to anticipate each of them.
    public static string RequireJetsonHost(string jetsonHost)
    {
        if (string.IsNullOrWhiteSpace(jetsonHost))
        {
            throw new ArgumentException("Jetson host is required.", nameof(jetsonHost));
        }

        var trimmed = jetsonHost.Trim();

        if (trimmed.Length > JetsonHostMaxLength)
        {
            throw new ArgumentException(
                $"Jetson host must not exceed {JetsonHostMaxLength} characters.", nameof(jetsonHost));
        }

        if (trimmed.Any(character => character < ' ' || character == '\x7f'))
        {
            throw new ArgumentException(
                "Jetson host must not contain control characters.", nameof(jetsonHost));
        }

        // Any of these means the caller supplied more than a host. Checked explicitly so the failure
        // is unambiguous rather than surfacing as a confusing round-trip mismatch below.
        if (trimmed.Contains("://", StringComparison.Ordinal)
            || trimmed.Contains('/')
            || trimmed.Contains('\\')
            || trimmed.Contains('@')
            || trimmed.Contains('?')
            || trimmed.Contains('#'))
        {
            throw new ArgumentException(
                "Jetson host must be a bare IP address or hostname, without a scheme, port, path, "
                    + "query, fragment or credentials.",
                nameof(jetsonHost));
        }

        // An IPv6 literal must be supplied bare (`2001:db8::1`), not pre-bracketed — bracketing is
        // this type's job when it composes a URI, so accepting both forms would let two different
        // strings mean the same host.
        var isIpv6 = System.Net.IPAddress.TryParse(trimmed, out var parsedAddress)
            && parsedAddress.AddressFamily == System.Net.Sockets.AddressFamily.InterNetworkV6;

        // A bare `host:8554` is rejected here: a colon is legal only in an IPv6 literal.
        if (!isIpv6 && trimmed.Contains(':'))
        {
            throw new ArgumentException(
                "Jetson host must not include a port.", nameof(jetsonHost));
        }

        var candidate = isIpv6 ? $"[{trimmed}]" : trimmed;

        if (!Uri.TryCreate($"rtsp://{candidate}", UriKind.Absolute, out var uri)
            || uri.AbsolutePath is not ("" or "/")
            || !string.IsNullOrEmpty(uri.UserInfo)
            || !string.IsNullOrEmpty(uri.Query)
            || !string.IsNullOrEmpty(uri.Fragment))
        {
            throw new ArgumentException(
                "Jetson host must be a valid IP address or hostname.", nameof(jetsonHost));
        }

        return trimmed;
    }

    public static int RequireRtspOutputPort(int rtspOutputPort) =>
        rtspOutputPort is < 1 or > 65535
            ? throw new ArgumentException(
                "RTSP output port must be between 1 and 65535.", nameof(rtspOutputPort))
            : rtspOutputPort;

    // FS-11 §11. Sets (or clears, with null/blank) the Device's advertised annotated-output base.
    //
    // Deliberately touches nothing else: DeviceId, ActivationStatus, ProtectedSharedSecret and every
    // Camera are untouched, because this is discovery metadata and changing it must never disturb
    // identity, credentials, activation state, or the running pipeline.
    //
    // Validation is enforced here rather than left to callers, so no code path can persist a base URL
    // that would compose into an unusable — or credential-bearing — client URL.
    public void SetAnnotatedOutputBaseUrl(string? baseUrl)
    {
        if (string.IsNullOrWhiteSpace(baseUrl))
        {
            AnnotatedOutputBaseUrl = null;
            return;
        }

        var trimmed = baseUrl.Trim().TrimEnd('/');

        if (trimmed.Length > AnnotatedOutputBaseUrlMaxLength)
        {
            throw new ArgumentException(
                $"Annotated output base URL must not exceed {AnnotatedOutputBaseUrlMaxLength} characters.",
                nameof(baseUrl));
        }

        if (!Uri.TryCreate(trimmed, UriKind.Absolute, out var uri))
        {
            throw new ArgumentException(
                "Annotated output base URL must be an absolute URI.", nameof(baseUrl));
        }

        if (uri.Scheme is not ("rtsp" or "rtsps"))
        {
            throw new ArgumentException(
                "Annotated output base URL must use the rtsp or rtsps scheme.", nameof(baseUrl));
        }

        if (string.IsNullOrEmpty(uri.Host))
        {
            throw new ArgumentException(
                "Annotated output base URL must include a host.", nameof(baseUrl));
        }

        // A base URL carrying credentials would leak them into every composed, admin-visible output
        // URL. The value is never echoed back in the message.
        if (!string.IsNullOrEmpty(uri.UserInfo))
        {
            throw new ArgumentException(
                "Annotated output base URL must not embed a username or password.", nameof(baseUrl));
        }

        if (!string.IsNullOrEmpty(uri.Query))
        {
            throw new ArgumentException(
                "Annotated output base URL must not include a query string.", nameof(baseUrl));
        }

        if (!string.IsNullOrEmpty(uri.Fragment))
        {
            throw new ArgumentException(
                "Annotated output base URL must not include a fragment.", nameof(baseUrl));
        }

        // The per-Camera path is appended by the composer; a base that already carries one would
        // produce ".../cameras/x/cameras/y".
        if (uri.AbsolutePath is not ("" or "/"))
        {
            throw new ArgumentException(
                "Annotated output base URL must not include a path.", nameof(baseUrl));
        }

        AnnotatedOutputBaseUrl = trimmed;
    }

    // FS-11 §11: the full client-facing URL for one Camera's annotated stream, or null when no base
    // is configured. Never invents an address — a null base yields a null URL, so the UI can say
    // "not configured" instead of advertising something unreachable.
    public string? ComposeAnnotatedOutputUrl(string cameraOutputPath)
    {
        var baseUrl = ComposeAnnotatedOutputBase();
        return baseUrl is null ? null : $"{baseUrl}/{cameraOutputPath.TrimStart('/')}";
    }

    // FS-12 §5 (Option A — transitional). The structured JetsonHost/RtspOutputPort pair is
    // authoritative; the legacy AnnotatedOutputBaseUrl column is consulted only when the structured
    // fields are unset, so a Device that has not yet been migrated keeps composing a working URL.
    //
    // The legacy column is retained deliberately and is still populated by the migration, which is
    // what makes rolling back to the previous build safe. A later feature removes it, and this
    // fallback branch with it.
    //
    // An IPv6 literal is bracketed here (FS-12 §4) — `rtsp://[2001:db8::1]:8554/...` — because an
    // unbracketed IPv6 host makes the port delimiter ambiguous and yields an unusable URL.
    public string? ComposeAnnotatedOutputBase()
    {
        if (JetsonHost is null)
        {
            return AnnotatedOutputBaseUrl;
        }

        var isIpv6 = System.Net.IPAddress.TryParse(JetsonHost, out var parsedAddress)
            && parsedAddress.AddressFamily == System.Net.Sockets.AddressFamily.InterNetworkV6;

        var host = isIpv6 ? $"[{JetsonHost}]" : JetsonHost;

        return $"rtsp://{host}:{RtspOutputPort ?? DefaultRtspOutputPort}";
    }

    // Called on first activation and on every reactivation alike (FS-02 §5.5 step 7, §5.8 steps
    // 5–6). The caller does not get to supply the DeviceId: assigning it here, and only when it is
    // still NULL, is what makes "assigned exactly once, never reassigned" (AC-7) an invariant of
    // the entity rather than a rule each caller has to remember.
    //
    // The shared secret, by contrast, is replaced on *every* activation — that rotation is the
    // security purpose of a reactivation (NFR-SEC-002, ADR-015).
    public void Activate(string protectedSharedSecret)
    {
        if (string.IsNullOrWhiteSpace(protectedSharedSecret))
        {
            throw new ArgumentException(
                "Protected shared secret is required.", nameof(protectedSharedSecret));
        }

        if (protectedSharedSecret.Length > ProtectedSharedSecretMaxLength)
        {
            // States only the limit, never the value.
            throw new ArgumentException(
                $"Protected shared secret must not exceed {ProtectedSharedSecretMaxLength} characters.",
                nameof(protectedSharedSecret));
        }

        DeviceId ??= Guid.NewGuid();

        ActivationStatus = DeviceActivationStatus.Activated;
        ProtectedSharedSecret = protectedSharedSecret;
    }

    // The security-first credential reset (IP-05, FS-02 §5.3 amended, ADR-015 amended). When the
    // Admin regenerates the Activation Key of a device that has already activated, the device's
    // current shared secret is revoked *immediately* — not left valid until a later reactivation.
    //
    // This is the only transition into ReactivationRequired, and it does two things atomically at the
    // entity level: it moves the status to ReactivationRequired and clears ProtectedSharedSecret, so
    // the old secret can never authenticate again (the credential-state form of revocation, §11 —
    // there is no live device-auth endpoint yet). The permanent DeviceId is deliberately untouched
    // (FR-BRN-007, AC-2/AC-12): a credential reset is not a new identity.
    //
    // Only valid for a device that has completed a first activation (DeviceId assigned). An
    // Unactivated device has no DeviceId and no secret to revoke, so calling this on one is a caller
    // bug (the regeneration service must branch on status, §5.3) — a thrown invariant violation, not
    // a silent no-op. Calling it again while already ReactivationRequired is safe and idempotent:
    // the status stays ReactivationRequired, the DeviceId is preserved, and the secret stays null.
    public void RequireReactivation()
    {
        if (DeviceId is null)
        {
            // States only the invariant, never any credential value.
            throw new InvalidOperationException(
                "A device that has never activated cannot be moved to ReactivationRequired.");
        }

        ActivationStatus = DeviceActivationStatus.ReactivationRequired;
        ProtectedSharedSecret = null;
    }

    // The credential-state prerequisite for device authentication (IP-05 §2.1, FS-02 §11). Every
    // current or future device-authentication path must gate on this *in addition to* cryptographic
    // secret verification — it does not replace that verification, it precedes it.
    //
    // Authentication is permitted only when the device is Activated AND a protected shared secret is
    // present. Status is checked first, so a device in ReactivationRequired (or Unactivated) is
    // rejected even if inconsistent/legacy data left a secret value present: a regenerated-away
    // credential can never authenticate. This makes revocation robust against a stray secret rather
    // than relying solely on ProtectedSharedSecret having been nulled.
    public bool CanAuthenticate() =>
        ActivationStatus == DeviceActivationStatus.Activated
        && !string.IsNullOrEmpty(ProtectedSharedSecret);
}

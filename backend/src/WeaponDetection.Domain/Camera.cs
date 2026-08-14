namespace WeaponDetection.Domain;

// A camera configured against a Branch (ARCH-001 §13.1, FS-02 §9). A Branch owns one or more of
// these — the relationship is one-to-many, not one-to-one (ARCH-001 §13.1 "Owns one or more Camera
// records"; FS-02 §12 requires at least one camera at branch creation). The one-per-branch
// uniqueness rule in the approved specs applies to Device (BR-002/CON-007), not to Camera.
//
// RtspUrl is operational configuration that may embed credentials (rtsp://user:pass@host/...).
// Two rules follow, and both are enforced here rather than left to callers:
//
//  1. Its value is never interpolated into an exception message, so a credential cannot leak into
//     a log or an error response by way of a validation failure.
//  2. Its *format* is deliberately not validated here. IP-01 §11 assigns RTSP URL format-checking
//     to the Application layer, not the Domain; this constructor enforces only presence and a
//     sane length. No connectivity check is performed at any layer in this task.
public class Camera
{
    public const int NameMaxLength = 200;
    public const int RtspUrlMaxLength = 2048;

    // FS-12 §3. The administrator-entered public mount identifier. Bounds are part of the frozen
    // pattern (a key is 3–64 characters), so they live here rather than in a DTO annotation that
    // could drift from the regex.
    public const int CameraKeyMinLength = 3;
    public const int CameraKeyMaxLength = 64;

    public Guid CameraId { get; private set; }
    public Guid BranchId { get; private set; }
    public string Name { get; private set; }
    public string RtspUrl { get; private set; }
    public bool Enabled { get; private set; }

    // FS-12 §2: the stable, administrator-defined *public* identifier for this Camera's annotated
    // stream. Deliberately distinct from CameraId, which stays the immutable internal identity used
    // by DetectionEvent, Alert and the Agent's source_id map — a CameraKey is what an operator reads
    // and types, never what the detection pipeline correlates on.
    //
    // Immutable after creation for this increment (FS-12 §2): there is no mutator, and
    // UpdateConfiguration deliberately does not touch it, so no code path can rename a live RTSP
    // mount out from under a monitoring client.
    public string CameraKey { get; private set; }

    // FS-11 §2: an explicit, stable, non-negative integer that determines this Camera's DeepStream
    // `source_id` when it is included in its Device's pipeline. Deliberately never derived from
    // `Name` or `RtspUrl`, and never inferred from database return order. For this increment it is
    // assigned once, automatically, from the camera's position within its creation request (see
    // BranchService) — there is no approved admin workflow yet to reorder it after creation.
    public int SourceOrder { get; private set; }

    // FS-11 §11 (per-camera annotated outputs): the stable RTSP mount this Camera's annotated stream
    // is published on by the Device's Bridge. Deliberately *derived* from the immutable CameraId
    // rather than stored, so it is unique by construction, survives a rename or a StreamUrl change
    // untouched, and needs no migration to persist redundant data. The full GUID is used (never a
    // truncated prefix) so uniqueness needs no collision argument, and its canonical "D" form is
    // already URL-path safe (lowercase hex + hyphens only).
    //
    // Returned to the Agent as a *relative* path — the absolute URL is composed against the
    // publishing Device's own RTSP base, which the Backend deliberately does not hardcode.
    public const string OutputPathPrefix = "cameras/";

    // FS-12 §2.1. Derived from the administrator-defined CameraKey rather than the CameraId, so the
    // public mount reads `cameras/front-entrance` instead of `cameras/2613b331-...`. Still derived
    // rather than stored: the key is already unique within the Branch and immutable, so persisting
    // the composed path would only create an opportunity for stored and derived values to drift.
    public static string DeriveOutputPath(string cameraKey) =>
        OutputPathPrefix + RequireCameraKey(cameraKey);

    // FS-12 §3 — the frozen pattern. Anchored, and applied to the whole string, so no newline trick
    // can smuggle a second line past it. Compiled once: this runs on every camera of every branch
    // response.
    private static readonly System.Text.RegularExpressions.Regex CameraKeyPattern =
        new(
            "^[a-z0-9](?:[a-z0-9-]{1,62}[a-z0-9])$",
            System.Text.RegularExpressions.RegexOptions.Compiled
                | System.Text.RegularExpressions.RegexOptions.CultureInvariant);

    // FS-12 §3. Reserved because each would collide with a real or reasonably-foreseeable route on
    // the Jetson's RTSP server or the Backend's API surface. `cameras` is reserved because it is the
    // mount prefix itself — `cameras/cameras` is a trap, not a valid camera.
    private static readonly HashSet<string> ReservedCameraKeys =
        new(StringComparer.Ordinal) { "ds-test", "api", "admin", "health", "metrics", "cameras" };

    // Validates without normalising. FS-12 §3 requires an uppercase key to be *rejected* rather than
    // silently lowercased: the administrator typed something specific, and quietly rewriting it would
    // mean the key they were shown is not the key they entered.
    public static string RequireCameraKey(string cameraKey)
    {
        if (string.IsNullOrWhiteSpace(cameraKey))
        {
            throw new ArgumentException("Camera key is required.", nameof(cameraKey));
        }

        // Trimmed only of surrounding whitespace — interior whitespace is a pattern violation, not
        // something to strip.
        var trimmed = cameraKey.Trim();

        if (trimmed.Length is < CameraKeyMinLength or > CameraKeyMaxLength)
        {
            throw new ArgumentException(
                $"Camera key must be between {CameraKeyMinLength} and {CameraKeyMaxLength} characters.",
                nameof(cameraKey));
        }

        if (!CameraKeyPattern.IsMatch(trimmed))
        {
            throw new ArgumentException(
                "Camera key must contain only lowercase letters, digits and hyphens, and must start "
                    + "and end with a letter or digit.",
                nameof(cameraKey));
        }

        if (ReservedCameraKeys.Contains(trimmed))
        {
            throw new ArgumentException(
                "Camera key is reserved and cannot be used.", nameof(cameraKey));
        }

        return trimmed;
    }

    // Non-throwing companion for callers that must classify a failure rather than catch one — the
    // Application layer maps each condition to its own named error code (FS-12 §3.1).
    public static bool IsReservedCameraKey(string cameraKey) =>
        ReservedCameraKeys.Contains(cameraKey);

    // Required by EF Core for materialization; never used by application code.
    private Camera()
    {
        Name = null!;
        RtspUrl = null!;
        CameraKey = null!;
    }

    // `enabled` defaults to true because the approved inbound camera contract carries only a name
    // and an RTSP URL (IP-01 §11, CameraConfigDto) — a camera the Admin has just configured is an
    // enabled one. No enable/disable mutator is added: no approved task needs one yet.
    // FS-12 §3: `cameraKey` is a required, positional argument — never defaulted and never derived
    // from `name`. Making it positional is deliberate: an optional key would let a caller create a
    // Camera with no public identity, and a key generated from the name would silently reintroduce
    // the mutable-label-as-identity bug FS-11 removed.
    public Camera(
        Guid branchId,
        string name,
        string rtspUrl,
        string cameraKey,
        bool enabled = true,
        int sourceOrder = 0)
    {
        if (branchId == Guid.Empty)
        {
            throw new ArgumentException("Branch id is required.", nameof(branchId));
        }

        if (sourceOrder < 0)
        {
            throw new ArgumentException("Camera source order must not be negative.", nameof(sourceOrder));
        }

        CameraId = Guid.NewGuid();
        BranchId = branchId;
        Name = RequireName(name);
        RtspUrl = RequireRtspUrl(rtspUrl);
        CameraKey = RequireCameraKey(cameraKey);
        SourceOrder = sourceOrder;
        Enabled = enabled;
    }

    // Edits an existing camera's configurable fields (FS-03 §5.2, AC-2). It reuses the same
    // presence/length validation the constructor applies, so an edit cannot move the camera into a
    // state the constructor would have rejected. CameraId, BranchId, SourceOrder, and Enabled are
    // deliberately never touched here: an edited camera keeps its identity (FS-03 §5.3 — existing
    // CameraId preserved), stays on its branch, and neither its pipeline order nor its enablement is
    // an edit input (as neither is a creation input via this path).
    //
    // FS-12 §2: CameraKey is likewise never touched here, and there is no mutator for it anywhere on
    // this entity. That is what makes "immutable after creation" an invariant of the type rather
    // than a rule the Application layer has to remember — a rename must never move a live RTSP mount.
    public void UpdateConfiguration(string name, string rtspUrl)
    {
        Name = RequireName(name);
        RtspUrl = RequireRtspUrl(rtspUrl);
    }

    // Presence and length only — the constructor and the editor share this so the two cannot drift.
    private static string RequireName(string name)
    {
        if (string.IsNullOrWhiteSpace(name))
        {
            throw new ArgumentException("Camera name is required.", nameof(name));
        }

        var trimmedName = name.Trim();

        if (trimmedName.Length > NameMaxLength)
        {
            throw new ArgumentException(
                $"Camera name must not exceed {NameMaxLength} characters.", nameof(name));
        }

        return trimmedName;
    }

    // Presence and length only — format (the rtsp:// scheme) remains an Application-layer rule
    // (IP-01 §11, FS-03 §6), not a Domain one. The value is never interpolated into the message: an
    // RTSP URL may embed credentials, and an exception must not become the vector that leaks them.
    private static string RequireRtspUrl(string rtspUrl)
    {
        if (string.IsNullOrWhiteSpace(rtspUrl))
        {
            throw new ArgumentException("Camera RTSP URL is required.", nameof(rtspUrl));
        }

        var trimmedRtspUrl = rtspUrl.Trim();

        if (trimmedRtspUrl.Length > RtspUrlMaxLength)
        {
            // Deliberately states only the limit, never the value — the value may contain
            // credentials.
            throw new ArgumentException(
                $"Camera RTSP URL must not exceed {RtspUrlMaxLength} characters.", nameof(rtspUrl));
        }

        return trimmedRtspUrl;
    }
}

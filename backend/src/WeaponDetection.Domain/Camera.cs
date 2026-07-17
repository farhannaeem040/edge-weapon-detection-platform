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

    public Guid CameraId { get; private set; }
    public Guid BranchId { get; private set; }
    public string Name { get; private set; }
    public string RtspUrl { get; private set; }
    public bool Enabled { get; private set; }

    // Required by EF Core for materialization; never used by application code.
    private Camera()
    {
        Name = null!;
        RtspUrl = null!;
    }

    // `enabled` defaults to true because the approved inbound camera contract carries only a name
    // and an RTSP URL (IP-01 §11, CameraConfigDto) — a camera the Admin has just configured is an
    // enabled one. No enable/disable mutator is added: no approved task needs one yet.
    public Camera(Guid branchId, string name, string rtspUrl, bool enabled = true)
    {
        if (branchId == Guid.Empty)
        {
            throw new ArgumentException("Branch id is required.", nameof(branchId));
        }

        CameraId = Guid.NewGuid();
        BranchId = branchId;
        Name = RequireName(name);
        RtspUrl = RequireRtspUrl(rtspUrl);
        Enabled = enabled;
    }

    // Edits an existing camera's configurable fields (FS-03 §5.2, AC-2). It reuses the same
    // presence/length validation the constructor applies, so an edit cannot move the camera into a
    // state the constructor would have rejected. CameraId, BranchId, and Enabled are deliberately
    // never touched: an edited camera keeps its identity (FS-03 §5.3 — existing CameraId preserved),
    // stays on its branch, and its enablement is not an edit input (as it is not a creation input).
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

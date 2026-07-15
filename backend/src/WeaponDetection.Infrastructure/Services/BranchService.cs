using Microsoft.EntityFrameworkCore;
using WeaponDetection.Application.Interfaces;
using WeaponDetection.Domain;
using WeaponDetection.Infrastructure.Persistence;

namespace WeaponDetection.Infrastructure.Services;

// Creates a Branch with its Cameras, the single reserved Device, and that Device's first Activation
// Key as one atomic unit (FS-02 §5.1, IP-01 T-15). Like AuthService, it depends on
// WeaponDetectionDbContext and therefore lives in Infrastructure/Services behind an Application
// interface — callers never see EF entities. Device/Activation-Key construction is delegated to
// IDeviceService so the two services stay independently testable (IP-01 §13).
//
// Validation happens before any database work: the request is fully validated and every entity is
// constructed in memory before the transaction is opened, so a rejected request (FS-02 §12/§13)
// never opens a connection, never begins a transaction, and cannot leave a partial row. Invalid
// input is signalled by an exception rather than a failure result — consistent with AuthService and
// with the Domain constructors — since the API layer (T-16) validates the inbound DTO first and any
// exception here means that gate was bypassed.
public class BranchService : IBranchService
{
    private readonly WeaponDetectionDbContext _dbContext;
    private readonly IDeviceService _deviceService;

    public BranchService(WeaponDetectionDbContext dbContext, IDeviceService deviceService)
    {
        _dbContext = dbContext;
        _deviceService = deviceService;
    }

    public async Task<BranchCreationResult> CreateBranchAsync(
        NewBranchRequest request,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(request);

        // At least one camera with a valid RTSP URL is required (FS-02 §12). The per-field presence
        // and length rules for the branch and each camera are enforced by the Domain constructors
        // below; this is the one branch-creation rule no single entity owns.
        if (request.Cameras is null || request.Cameras.Count == 0)
        {
            throw new ArgumentException(
                "A branch must be created with at least one camera.", nameof(request));
        }

        // Constructing the Branch first yields the BranchId the Cameras and Device hang off of.
        // Any blank/oversized branch field throws here, before any database work.
        var branch = new Branch(request.Name, request.Address, request.ContactDetails);

        var cameras = new List<Camera>(request.Cameras.Count);
        foreach (var cameraRequest in request.Cameras)
        {
            if (cameraRequest is null)
            {
                throw new ArgumentException(
                    "A camera configuration must not be null.", nameof(request));
            }

            // RTSP URL *format* is validated here, at the Application layer, not in the Domain
            // (IP-01 §11) — the Camera entity checks only presence and length. Presence itself is
            // left to the constructor so a single blank-value message is produced in one place.
            EnsureValidRtspUrl(cameraRequest.RtspUrl);

            cameras.Add(new Camera(branch.BranchId, cameraRequest.Name, cameraRequest.RtspUrl));
        }

        // Pure, database-free: builds the unactivated Device and its Unconsumed Activation Key, and
        // returns the plaintext key to disclose once. Nothing is persisted until the transaction
        // below.
        var provisioning = _deviceService.ProvisionForBranch(branch.BranchId);

        // One transaction spanning Branch + Cameras + Device + Activation Key, so a failure partway
        // through leaves no partial rows (FS-02 §5.1, AC-1). The atomicity is enforced by SQL
        // Server, not simulated — hence the behavioural coverage of this method is an integration
        // test against a real database (IP-01 §9), while the validation guards above are unit-tested.
        await using var transaction =
            await _dbContext.Database.BeginTransactionAsync(cancellationToken);
        try
        {
            _dbContext.Branches.Add(branch);
            _dbContext.Cameras.AddRange(cameras);
            _dbContext.Devices.Add(provisioning.Device);
            _dbContext.ActivationKeys.Add(provisioning.ActivationKey);

            await _dbContext.SaveChangesAsync(cancellationToken);
            await transaction.CommitAsync(cancellationToken);
        }
        catch (DbUpdateException ex)
        {
            await transaction.RollbackAsync(cancellationToken);

            // The plaintext key/secret is never interpolated into the message — an exception is a
            // log entry waiting to happen (FS-02 §11).
            throw new InvalidOperationException(
                "Branch creation failed while persisting the branch and its device.", ex);
        }

        var createdCameras = cameras
            .Select(c => new CreatedCamera(c.CameraId, c.Name, c.RtspUrl, c.Enabled))
            .ToList();

        var device = provisioning.Device;
        var createdBranch = new CreatedBranch(
            branch.BranchId,
            branch.Name,
            branch.Address,
            branch.ContactDetails,
            createdCameras,
            new CreatedDeviceSummary(device.DeviceId, device.ActivationStatus, device.LastKnownAddress));

        return new BranchCreationResult(createdBranch, provisioning.PlaintextActivationKey);
    }

    // A valid RTSP URL is an absolute URI using the rtsp scheme (FS-02 §12). The value is never
    // echoed into the exception message: an RTSP URL may embed credentials
    // (rtsp://user:pass@host/...), and an exception must not become the vector that leaks them
    // (mirrors the Camera entity's own rule).
    private static void EnsureValidRtspUrl(string rtspUrl)
    {
        if (string.IsNullOrWhiteSpace(rtspUrl))
        {
            // Presence is (re)checked by the Camera constructor too; guarding here keeps the format
            // check from having to reason about null/blank input.
            return;
        }

        var trimmed = rtspUrl.Trim();
        var isRtsp =
            Uri.TryCreate(trimmed, UriKind.Absolute, out var uri)
            && string.Equals(uri.Scheme, "rtsp", StringComparison.OrdinalIgnoreCase);

        if (!isRtsp)
        {
            throw new ArgumentException(
                "A camera must be configured with a valid rtsp:// URL.", nameof(rtspUrl));
        }
    }
}

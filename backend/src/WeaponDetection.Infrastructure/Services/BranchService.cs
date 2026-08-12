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

        // FS-12 §3: keys must be unique within the Branch. Checked here as well as by the database's
        // unique index so a duplicate inside a single request produces a precise named error rather
        // than an opaque DbUpdateException after a wasted round trip.
        var seenCameraKeys = new HashSet<string>(StringComparer.Ordinal);

        for (var sourceOrder = 0; sourceOrder < request.Cameras.Count; sourceOrder++)
        {
            var cameraRequest = request.Cameras[sourceOrder];
            if (cameraRequest is null)
            {
                throw new ArgumentException(
                    "A camera configuration must not be null.", nameof(request));
            }

            var cameraKey = ValidateCameraKey(cameraRequest.CameraKey);

            if (!seenCameraKeys.Add(cameraKey))
            {
                throw new ConfigurationValidationException(
                    ConfigurationErrorCodes.CameraKeyAlreadyExists,
                    "Each camera key must be unique within the branch.");
            }

            // RTSP URL *format* is validated here, at the Application layer, not in the Domain
            // (IP-01 §11) — the Camera entity checks only presence and length. Presence itself is
            // left to the constructor so a single blank-value message is produced in one place.
            EnsureValidRtspUrl(cameraRequest.RtspUrl);

            // FS-11 §2: SourceOrder is auto-assigned from the camera's position in the creation
            // request — there is no approved admin reordering workflow yet, so request order is the
            // one deterministic signal available at creation time.
            cameras.Add(new Camera(
                branch.BranchId,
                cameraRequest.Name,
                cameraRequest.RtspUrl,
                cameraKey,
                sourceOrder: sourceOrder));
        }

        // Pure, database-free: builds the unactivated Device and its Unconsumed Activation Key, and
        // returns the plaintext key to disclose once. Nothing is persisted until the transaction
        // below.
        var provisioning = _deviceService.ProvisionForBranch(branch.BranchId);

        // FS-12 §4: the reserved Device is the network host for every Camera output of this Branch,
        // so its address is configured as part of the same creation transaction. Validated *before*
        // the transaction opens, so a malformed host costs no database work and leaves no partial
        // rows (FS-12 §9 item 11).
        ApplyNetworkConfiguration(provisioning.Device, request.JetsonHost, request.RtspOutputPort);

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

        var device = provisioning.Device;
        var createdBranch = MapBranch(branch, cameras, device);

        return new BranchCreationResult(createdBranch, provisioning.PlaintextActivationKey);
    }

    // Reads every branch with its cameras and single Device summary (FS-02 §5.4, §10.3). Three
    // AsNoTracking reads rather than per-branch queries: the entities carry no navigation
    // properties (they hold plain FK Guids), and at this prototype's scale a set-based load then an
    // in-memory join is both simpler and cheaper than N round-trips. No secret or DeviceRecordId
    // leaves this method — BranchView deliberately cannot carry either (FS-02 §1.3, §7).
    public async Task<IReadOnlyList<BranchView>> ListBranchesAsync(
        CancellationToken cancellationToken = default)
    {
        var branches = await _dbContext.Branches.AsNoTracking().ToListAsync(cancellationToken);
        if (branches.Count == 0)
        {
            return [];
        }

        var cameras = await _dbContext.Cameras.AsNoTracking().ToListAsync(cancellationToken);
        var devices = await _dbContext.Devices.AsNoTracking().ToListAsync(cancellationToken);

        var camerasByBranch = cameras
            .GroupBy(c => c.BranchId)
            .ToDictionary(g => g.Key, g => (IReadOnlyList<Camera>)g.ToList());
        var deviceByBranch = devices.ToDictionary(d => d.BranchId);

        return branches
            .Select(branch => MapBranch(
                branch,
                camerasByBranch.GetValueOrDefault(branch.BranchId, []),
                deviceByBranch[branch.BranchId]))
            .ToList();
    }

    // Reads one branch by id, or null when none matches (mapped to 404 by the API layer). Each
    // branch has exactly one Device (BR-002/CON-007), created with the branch itself, so the Device
    // lookup is a Single once the branch is known.
    public async Task<BranchView?> GetBranchAsync(
        Guid branchId,
        CancellationToken cancellationToken = default)
    {
        var branch = await _dbContext.Branches
            .AsNoTracking()
            .SingleOrDefaultAsync(b => b.BranchId == branchId, cancellationToken);
        if (branch is null)
        {
            return null;
        }

        var cameras = await _dbContext.Cameras
            .AsNoTracking()
            .Where(c => c.BranchId == branchId)
            .ToListAsync(cancellationToken);

        var device = await _dbContext.Devices
            .AsNoTracking()
            .SingleAsync(d => d.BranchId == branchId, cancellationToken);

        return MapBranch(branch, cameras, device);
    }

    // Edits a branch and reconciles its cameras as one atomic transaction (FS-03 §5.1, §5.2, §11).
    // The Device and every Activation Key record are deliberately never written here: an edit
    // preserves the Device identity, activation status, protected shared secret, and all key records
    // unchanged (FS-03 §5.3, AC-7, AC-8). Expected non-success outcomes — an unknown branch, or a
    // business-invalid request — are returned as typed results rather than thrown, so the API layer
    // maps them to 404/400 without exceptions-as-flow.
    public async Task<BranchUpdateResult> UpdateBranchAsync(
        UpdateBranchRequest request,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(request);

        // Database-free validation first, mirroring CreateBranchAsync: nothing below opens a
        // connection or a transaction until the request is structurally sound.

        // At least one camera must remain — and the only way an edit could leave zero is to submit
        // zero, since every submitted camera is persisted (FS-03 §5.2, AC-5).
        if (request.Cameras is null || request.Cameras.Count == 0)
        {
            return BranchUpdateResult.Invalid();
        }

        // Two request cameras may not claim the same existing CameraId (FS-03 §5.2, AC-9).
        var requestedIds = request.Cameras
            .Where(c => c is not null && c.CameraId is not null)
            .Select(c => c.CameraId!.Value)
            .ToList();
        if (requestedIds.Count != requestedIds.Distinct().Count())
        {
            return BranchUpdateResult.Invalid();
        }

        // Each camera's RTSP URL must be a valid rtsp:// URL (FS-03 §6). Presence/length are enforced
        // by the Domain mutators/constructors below; format is the Application-layer rule.
        foreach (var camera in request.Cameras)
        {
            if (camera is null || !IsValidRtspUrl(camera.RtspUrl))
            {
                return BranchUpdateResult.Invalid();
            }
        }

        await using var transaction =
            await _dbContext.Database.BeginTransactionAsync(cancellationToken);
        try
        {
            // Tracked (not AsNoTracking): this path mutates the branch and its cameras.
            var branch = await _dbContext.Branches
                .SingleOrDefaultAsync(b => b.BranchId == request.BranchId, cancellationToken);
            if (branch is null)
            {
                await transaction.RollbackAsync(cancellationToken);
                return BranchUpdateResult.NotFound();
            }

            var storedCameras = await _dbContext.Cameras
                .Where(c => c.BranchId == request.BranchId)
                .ToListAsync(cancellationToken);
            var storedById = storedCameras.ToDictionary(c => c.CameraId);

            // Every existing CameraId in the request must belong to *this* branch (FS-03 §5.2, AC-9).
            // A foreign camera's id (one owned by another branch) is simply not in this set, so it is
            // rejected identically to an unknown id — without revealing which it was.
            if (requestedIds.Any(id => !storedById.ContainsKey(id)))
            {
                await transaction.RollbackAsync(cancellationToken);
                return BranchUpdateResult.Invalid();
            }

            try
            {
                branch.UpdateDetails(request.Name, request.Address, request.ContactDetails);

                // FS-11 §2: a newly-added camera's SourceOrder must not collide with any camera
                // already stored for this branch. Existing cameras keep the SourceOrder they were
                // created with (an edit never touches it); a new one gets the next value after the
                // highest currently stored, so it can never collide with the update loop's own
                // additions either.
                var nextSourceOrder = storedCameras.Count == 0
                    ? 0
                    : storedCameras.Max(c => c.SourceOrder) + 1;

                // FS-12 §3: every key that will exist after this update must stay unique within the
                // Branch. Seeded with the keys of cameras the request keeps, so a newly-added camera
                // cannot claim one that is already in use.
                var liveCameraKeys = new HashSet<string>(
                    storedCameras
                        .Where(c => requestedIds.Contains(c.CameraId))
                        .Select(c => c.CameraKey),
                    StringComparer.Ordinal);

                foreach (var mutation in request.Cameras)
                {
                    if (mutation.CameraId is null)
                    {
                        // Add: a brand-new camera identity (FS-03 §5.2). FS-12 §3 — a new Camera must
                        // carry an explicit administrator-entered key; one is never generated for it.
                        var newCameraKey = ValidateCameraKey(mutation.CameraKey);

                        if (!liveCameraKeys.Add(newCameraKey))
                        {
                            throw new ConfigurationValidationException(
                                ConfigurationErrorCodes.CameraKeyAlreadyExists,
                                "Each camera key must be unique within the branch.");
                        }

                        _dbContext.Cameras.Add(
                            new Camera(
                                branch.BranchId,
                                mutation.Name,
                                mutation.RtspUrl,
                                newCameraKey,
                                sourceOrder: nextSourceOrder));
                        nextSourceOrder++;
                    }
                    else
                    {
                        // Update in place: the existing CameraId is preserved (FS-03 §5.3).
                        var stored = storedById[mutation.CameraId.Value];

                        // FS-12 §2: the key is immutable after creation. Omitting it is fine (an
                        // edit-only client need not echo it back); sending a *different* one is a
                        // request to move a live RTSP mount, which is rejected outright rather than
                        // silently ignored — a caller that believes it renamed the key must be told
                        // it did not.
                        if (mutation.CameraKey is not null
                            && !string.Equals(
                                mutation.CameraKey.Trim(), stored.CameraKey, StringComparison.Ordinal))
                        {
                            throw new ConfigurationValidationException(
                                ConfigurationErrorCodes.CameraKeyImmutable,
                                "A camera key cannot be changed after the camera is created.");
                        }

                        stored.UpdateConfiguration(mutation.Name, mutation.RtspUrl);
                    }
                }
            }
            catch (ConfigurationValidationException)
            {
                // FS-12 §3.1: these carry a named error code the administrator needs to see, so they
                // are rethrown for the controller to map rather than being collapsed into the
                // generic Invalid outcome by the catch below (which they would otherwise match, as
                // ConfigurationValidationException derives from ArgumentException).
                await transaction.RollbackAsync(cancellationToken);
                throw;
            }
            catch (ArgumentException)
            {
                // Presence/length failures from the Domain mutators/constructors. The API layer's
                // DataAnnotations normally reject these first; this is the defensive backstop, and it
                // collapses to the same Invalid outcome with no value echoed.
                await transaction.RollbackAsync(cancellationToken);
                return BranchUpdateResult.Invalid();
            }

            // Remove every stored camera the request no longer includes (FS-03 §5.2). Deleting only
            // the omitted cameras; the kept and added ones remain, so the branch still has ≥1.
            var keptIds = requestedIds.ToHashSet();
            var removed = storedCameras.Where(c => !keptIds.Contains(c.CameraId)).ToList();
            _dbContext.Cameras.RemoveRange(removed);

            await _dbContext.SaveChangesAsync(cancellationToken);
            await transaction.CommitAsync(cancellationToken);
        }
        catch (DbUpdateException ex)
        {
            await transaction.RollbackAsync(cancellationToken);

            // No submitted value is interpolated into the message — an RTSP URL may embed
            // credentials (FS-03 §12, ARCH-001 §15.6).
            throw new InvalidOperationException(
                "Branch update failed while persisting the branch and its cameras.", ex);
        }

        // Re-project the committed end-state in the same safe shape the read paths use. The Device is
        // read back unchanged — it was never written by this operation (FS-03 §5.3).
        var updatedBranch = await _dbContext.Branches
            .AsNoTracking()
            .SingleAsync(b => b.BranchId == request.BranchId, cancellationToken);
        var updatedCameras = await _dbContext.Cameras
            .AsNoTracking()
            .Where(c => c.BranchId == request.BranchId)
            .ToListAsync(cancellationToken);
        var device = await _dbContext.Devices
            .AsNoTracking()
            .SingleAsync(d => d.BranchId == request.BranchId, cancellationToken);

        return BranchUpdateResult.Updated(MapBranch(updatedBranch, updatedCameras, device));
    }

    // Hard-deletes a branch and its dependent data as one atomic transaction (FS-03 §5.5, §5.6,
    // §11). The delete is explicit and ordered — Activation Keys, then Device, then Cameras, then the
    // Branch — rather than relying on the schema's cascade rules: those cascades are declared but were
    // untested before this feature (each config comment says as much), and an explicit order makes
    // the guarantee testable and independent of that configuration (IP-03 §1.1). A failure at any
    // point rolls the whole thing back.
    //
    // No remote Agent contact happens here (FS-03 §7.4): the Backend removes only its own records. A
    // previously activated Agent may still hold its local credentials, but once the Device row is
    // gone the Backend has nothing to authenticate them against (AC-12).
    public async Task<BranchDeletionOutcome> DeleteBranchAsync(
        Guid branchId,
        CancellationToken cancellationToken = default)
    {
        await using var transaction =
            await _dbContext.Database.BeginTransactionAsync(cancellationToken);
        try
        {
            var branch = await _dbContext.Branches
                .SingleOrDefaultAsync(b => b.BranchId == branchId, cancellationToken);
            if (branch is null)
            {
                await transaction.RollbackAsync(cancellationToken);
                return BranchDeletionOutcome.NotFound;
            }

            // Each branch has exactly one Device (BR-002/CON-007); its Activation Keys hang off the
            // internal DeviceRecordId. Load children before deleting, parents last.
            var device = await _dbContext.Devices
                .SingleOrDefaultAsync(d => d.BranchId == branchId, cancellationToken);
            var cameras = await _dbContext.Cameras
                .Where(c => c.BranchId == branchId)
                .ToListAsync(cancellationToken);

            if (device is not null)
            {
                var activationKeys = await _dbContext.ActivationKeys
                    .Where(k => k.DeviceRecordId == device.DeviceRecordId)
                    .ToListAsync(cancellationToken);
                _dbContext.ActivationKeys.RemoveRange(activationKeys);
            }

            if (device is not null)
            {
                _dbContext.Devices.Remove(device);
            }

            _dbContext.Cameras.RemoveRange(cameras);
            _dbContext.Branches.Remove(branch);

            await _dbContext.SaveChangesAsync(cancellationToken);
            await transaction.CommitAsync(cancellationToken);

            return BranchDeletionOutcome.Deleted;
        }
        catch (DbUpdateException ex)
        {
            await transaction.RollbackAsync(cancellationToken);

            // No identifier or secret is interpolated into the message (FS-03 §12, ARCH-001 §15.6).
            throw new InvalidOperationException(
                "Branch deletion failed while removing the branch and its dependent records.", ex);
        }
    }

    // The one place Branch/Camera/Device entities become a BranchView, shared by the create response
    // and both read paths so the projection — and its exclusion of DeviceRecordId and any secret —
    // is defined once.
    private static BranchView MapBranch(Branch branch, IReadOnlyList<Camera> cameras, Device device) =>
        new(
            branch.BranchId,
            branch.Name,
            branch.Address,
            branch.ContactDetails,
            cameras.Select(c => MapCamera(c, device)).ToList(),
            new DeviceSummaryView(
                device.DeviceId,
                device.ActivationStatus,
                device.LastKnownAddress,
                device.JetsonHost,
                device.RtspOutputPort,
                device.ComposeAnnotatedOutputBase()));

    // FS-11 §11: the annotated-output path is derived from the Camera's immutable id, and the full
    // client URL is composed against the owning Device's base — null when that base is unset, so a
    // client is told "not configured" rather than shown an address the Backend guessed.
    private static CameraView MapCamera(Camera camera, Device device)
    {
        // FS-12 §2.1: derived from the administrator-defined CameraKey rather than the CameraId, so
        // the public mount reads `cameras/front-entrance`. The Camera's own immutable CameraId is
        // still returned above — the key changed what the *stream* is called, not what the Camera is.
        var outputPath = Camera.DeriveOutputPath(camera.CameraKey);
        return new CameraView(
            camera.CameraId,
            camera.CameraKey,
            camera.Name,
            camera.RtspUrl,
            camera.Enabled,
            camera.SourceOrder,
            outputPath,
            device.ComposeAnnotatedOutputUrl(outputPath));
    }

    // FS-12 §3.1 — validates an administrator-supplied CameraKey and maps each distinct failure to
    // its own named error code. The Domain entity enforces the same rules (so no code path can
    // persist an invalid key), but it throws a single ArgumentException; this classifies the failure
    // so the administrator is told whether the key was blank, malformed, or reserved.
    private static string ValidateCameraKey(string? cameraKey)
    {
        if (string.IsNullOrWhiteSpace(cameraKey))
        {
            throw new ConfigurationValidationException(
                ConfigurationErrorCodes.CameraKeyRequired, "A camera key is required.");
        }

        var trimmed = cameraKey.Trim();

        if (Camera.IsReservedCameraKey(trimmed))
        {
            throw new ConfigurationValidationException(
                ConfigurationErrorCodes.CameraKeyReserved,
                "That camera key is reserved and cannot be used.");
        }

        try
        {
            return Camera.RequireCameraKey(trimmed);
        }
        catch (ArgumentException)
        {
            // The value is deliberately not echoed back into the message.
            throw new ConfigurationValidationException(
                ConfigurationErrorCodes.CameraKeyInvalid,
                "A camera key must be 3-64 characters of lowercase letters, digits and hyphens, "
                    + "starting and ending with a letter or digit.");
        }
    }

    // FS-12 §4 — applies the Jetson network configuration, classifying each failure. Like
    // ValidateCameraKey this does not duplicate the rules; the Device entity owns them, and this
    // only decides which named code the administrator sees.
    private static void ApplyNetworkConfiguration(Device device, string? jetsonHost, int? rtspOutputPort)
    {
        if (string.IsNullOrWhiteSpace(jetsonHost))
        {
            throw new ConfigurationValidationException(
                ConfigurationErrorCodes.JetsonHostRequired,
                "A Jetson host (IP address or hostname) is required.");
        }

        if (rtspOutputPort is < 1 or > 65535)
        {
            throw new ConfigurationValidationException(
                ConfigurationErrorCodes.RtspOutputPortInvalid,
                "The RTSP output port must be between 1 and 65535.");
        }

        try
        {
            device.SetNetworkConfiguration(jetsonHost, rtspOutputPort);
        }
        catch (ArgumentException)
        {
            throw new ConfigurationValidationException(
                ConfigurationErrorCodes.JetsonHostInvalid,
                "The Jetson host must be a bare IP address or hostname, without a scheme, port, "
                    + "path, query, fragment or credentials.");
        }
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

        if (!IsRtspUri(rtspUrl.Trim()))
        {
            throw new ArgumentException(
                "A camera must be configured with a valid rtsp:// URL.", nameof(rtspUrl));
        }
    }

    // The update path's boolean counterpart to EnsureValidRtspUrl (FS-03 §6). Unlike the create
    // helper — which skips a blank value so the Camera constructor produces the single presence
    // message — this treats blank as invalid too, so the whole update request collapses to one
    // Invalid result rather than relying on a Domain throw. Both share the same rtsp:// core.
    private static bool IsValidRtspUrl(string rtspUrl) =>
        !string.IsNullOrWhiteSpace(rtspUrl) && IsRtspUri(rtspUrl.Trim());

    // An absolute URI using the rtsp scheme. The value is never echoed by any caller's error path.
    private static bool IsRtspUri(string trimmed) =>
        Uri.TryCreate(trimmed, UriKind.Absolute, out var uri)
        && string.Equals(uri.Scheme, "rtsp", StringComparison.OrdinalIgnoreCase);
}

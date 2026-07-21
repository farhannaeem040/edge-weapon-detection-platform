using System.Buffers.Text;
using System.Security.Cryptography;
using Microsoft.Data.SqlClient;
using Microsoft.EntityFrameworkCore;
using WeaponDetection.Application.Exceptions;
using WeaponDetection.Application.Interfaces;
using WeaponDetection.Domain;
using WeaponDetection.Infrastructure.Persistence;
using WeaponDetection.Infrastructure.Security;

namespace WeaponDetection.Infrastructure.Services;

// Device operations behind an Application interface (ARCH-001 §10.1). Responsibilities today:
//
//  - ProvisionForBranch (T-15): a pure, database-free build of a branch's reserved Device and its
//    first Activation Key; BranchService persists the result inside the branch-creation transaction.
//  - GetDeviceByDeviceIdAsync (T-16): a read-only lookup of an activated Device by its external
//    DeviceId, for GET /api/v1/devices/{id} (FS-02 §10.3).
//  - RegenerateActivationKeyAsync (T-17): a transactional invalidate-old/insert-new replacement of a
//    branch's Activation Key (FS-02 §5.3).
//  - ActivateAsync (T-19): transactional validation and consumption of a presented Activation Key —
//    assigning/retaining the DeviceId, activating the Device, and issuing a protected shared secret
//    (FS-02 §5.5–§5.7, IP-01 §8 steps 3–6).
//
// No plaintext credential material — Activation Key, its secret half, or the device shared secret —
// is ever logged (FS-02 §11); each flows straight back to the caller for its single disclosure and
// is otherwise discarded. Only protected/hashed forms are persisted.
public class DeviceService : IDeviceService
{
    // 256 bits of entropy for the device shared secret, Base64Url-encoded so it is safe to carry
    // later in the X-Device-Secret header (FS-02 §5.5 step 10) without escaping.
    private const int SharedSecretSizeBytes = 32;

    private readonly IActivationKeyGenerator _activationKeyGenerator;
    private readonly IDeviceSecretProtector _deviceSecretProtector;
    private readonly WeaponDetectionDbContext _dbContext;

    public DeviceService(
        IActivationKeyGenerator activationKeyGenerator,
        IDeviceSecretProtector deviceSecretProtector,
        WeaponDetectionDbContext dbContext)
    {
        _activationKeyGenerator = activationKeyGenerator
            ?? throw new ArgumentNullException(nameof(activationKeyGenerator));
        _deviceSecretProtector = deviceSecretProtector
            ?? throw new ArgumentNullException(nameof(deviceSecretProtector));
        _dbContext = dbContext ?? throw new ArgumentNullException(nameof(dbContext));
    }

    public async Task<DeviceDetailView?> GetDeviceByDeviceIdAsync(
        Guid deviceId,
        CancellationToken cancellationToken = default)
    {
        // A Device is addressable here only by its external DeviceId, which is non-null only once
        // activated (FS-02 §1.3). An all-zero id is never a valid assigned DeviceId, so it is
        // treated as "no such device" without querying — this also stops it from matching an
        // unactivated row's NULL DeviceId under any provider that might coerce the comparison.
        if (deviceId == Guid.Empty)
        {
            return null;
        }

        var device = await _dbContext.Devices
            .AsNoTracking()
            .SingleOrDefaultAsync(d => d.DeviceId == deviceId, cancellationToken);
        if (device is null)
        {
            return null;
        }

        // DeviceId is guaranteed non-null on the matched row (it was the lookup key); the summary
        // type exposes only externally-safe fields — never DeviceRecordId or ProtectedSharedSecret.
        return new DeviceDetailView(
            device.DeviceId!.Value,
            device.BranchId,
            device.ActivationStatus,
            device.LastKnownAddress);
    }

    public DeviceProvisioning ProvisionForBranch(Guid branchId)
    {
        if (branchId == Guid.Empty)
        {
            throw new ArgumentException("Branch id is required.", nameof(branchId));
        }

        // The Device entity fixes the reserved, pre-activation state itself (DeviceId NULL,
        // ActivationStatus Unactivated) — this method does not get to choose otherwise.
        var device = new Device(branchId);

        // Only the keyId and the salted secret hash are persisted on the ActivationKey; the
        // complete plaintext key is carried back out for the single disclosure and never stored.
        var generated = _activationKeyGenerator.Generate();
        var activationKey = new ActivationKey(
            generated.KeyId, device.DeviceRecordId, generated.SecretHash);

        return new DeviceProvisioning(device, activationKey, generated.PlaintextKey);
    }

    public async Task<ActivationKeyRegenerationResult?> RegenerateActivationKeyAsync(
        Guid branchId,
        CancellationToken cancellationToken = default)
    {
        // An all-zero id is never a valid BranchId, so it is "no such branch" (404) without a query
        // — mirroring GetDeviceByDeviceIdAsync's treatment of Guid.Empty, and short-circuiting
        // before any transaction is opened.
        if (branchId == Guid.Empty)
        {
            return null;
        }

        // Regeneration is a single atomic unit (FS-02 §5.3, §12; IP-05 T-50): invalidating the old
        // Unconsumed key(s), the security-first credential reset for an activated device, and the
        // insertion of the replacement key either all commit or all roll back. A failure partway
        // through can never leave the Device with a revoked secret and no key, two Unconsumed keys, or
        // a changed DeviceId. SQL Server enforces the atomicity — hence the behavioural coverage is an
        // integration test against a real database (IP-01 §9). The existence read is kept inside the
        // transaction so it shares the same consistent snapshot as the writes.
        await using var transaction =
            await _dbContext.Database.BeginTransactionAsync(cancellationToken);

        var device = await _dbContext.Devices
            .SingleOrDefaultAsync(d => d.BranchId == branchId, cancellationToken);
        if (device is null)
        {
            // Nothing has been written; the disposing transaction rolls back cleanly. Null maps to
            // 404 at the API layer (FS-02 §10.2). This reveals nothing about whether credential
            // material existed.
            return null;
        }

        // Invalidate every currently Unconsumed key so at most one Unconsumed key survives per Device
        // (the filtered unique index of T-49). Historical Consumed/Invalidated keys are left exactly
        // as they are — a Consumed key is already unusable, and preserving it keeps an accurate record
        // of what was actually used. Loaded tracked (no AsNoTracking) so the transition is persisted.
        var unconsumedKeys = await _dbContext.ActivationKeys
            .Where(k => k.DeviceRecordId == device.DeviceRecordId
                && k.Status == ActivationKeyStatus.Unconsumed)
            .ToListAsync(cancellationToken);
        foreach (var unconsumedKey in unconsumedKeys)
        {
            unconsumedKey.Invalidate();
        }

        // The security-first credential reset (FS-02 §5.3 amended, ADR-015). An Activated or
        // already-ReactivationRequired device has its shared secret revoked and moves to
        // ReactivationRequired, retaining its permanent DeviceId (RequireReactivation is idempotent
        // from ReactivationRequired). An Unactivated device has no DeviceId and no secret to revoke,
        // so it stays Unactivated and RequireReactivation is deliberately not called on it.
        if (device.ActivationStatus != DeviceActivationStatus.Unactivated)
        {
            device.RequireReactivation();
        }

        // The replacement stores only the new keyId and the salted secret hash; the complete plaintext
        // key exists here only as a local (FS-02 §1.4, §11) and is placed in a caller-visible result
        // only after a successful commit, below. The new key points at the same internal
        // DeviceRecordId, never the (possibly NULL) external DeviceId (FS-02 §1.3).
        var generated = _activationKeyGenerator.Generate();
        var replacement = new ActivationKey(
            generated.KeyId, device.DeviceRecordId, generated.SecretHash);

        try
        {
            // A no-op in production; overridden only by a concurrency test to force two regenerations
            // to interleave deterministically after both have read the pre-state and before either
            // writes (so the loser genuinely hits the T-49 unique index). See
            // OnBeforeRegenerationWriteAsync.
            await OnBeforeRegenerationWriteAsync(cancellationToken);

            // Flush the invalidations and the status/secret change BEFORE inserting the new Unconsumed
            // key, so the filtered unique index (IX_ActivationKeys_DeviceRecordId_Unconsumed, T-49)
            // never transiently sees two Unconsumed rows for this device within one statement.
            await _dbContext.SaveChangesAsync(cancellationToken);

            _dbContext.ActivationKeys.Add(replacement);
            await _dbContext.SaveChangesAsync(cancellationToken);

            await transaction.CommitAsync(cancellationToken);
        }
        catch (DbUpdateException ex) when (IsUnconsumedKeyUniqueIndexViolation(ex))
        {
            // A concurrent regeneration already committed the single Unconsumed key for this device
            // (T-49's filtered unique index is the final authority). Roll back — the winner's key is
            // untouched — and clear the change tracker so this scoped context can never later persist
            // the failed transaction's staged state. No plaintext key is returned to the loser; there
            // is no automatic retry (IP-05 §3). T-52 maps this to 409/ACTIVATION_KEY_REGENERATION_CONFLICT.
            await transaction.RollbackAsync(cancellationToken);
            _dbContext.ChangeTracker.Clear();
            throw new ActivationKeyRegenerationConflictException();
        }
        catch (DbUpdateException ex)
        {
            // Any other persistence failure is unexpected — not a regeneration conflict. Roll back and
            // clear the tracker; the plaintext key/secret is never interpolated into the message.
            await transaction.RollbackAsync(cancellationToken);
            _dbContext.ChangeTracker.Clear();
            throw new InvalidOperationException(
                "Activation key regeneration failed while persisting the new key.", ex);
        }

        // Constructed only after the transaction has committed successfully, so a rollback or a losing
        // concurrent request can never return a plaintext key (IP-05 T-50 plaintext-return safety).
        return new ActivationKeyRegenerationResult(generated.PlaintextKey);
    }

    // A deliberately empty extension point invoked inside RegenerateActivationKeyAsync's transaction,
    // after the device/keys have been read and the in-memory changes staged but before any database
    // write. It exists solely so a concurrency test can synchronize two regenerations at exactly this
    // point (both having read the pre-state) and force a deterministic race on the T-49 unique index,
    // rather than relying on a flaky timing window. Production never overrides it, and it is protected
    // — invisible to the API surface and to IDeviceService.
    protected virtual Task OnBeforeRegenerationWriteAsync(CancellationToken cancellationToken) =>
        Task.CompletedTask;

    // True only for the specific unique-index violation on IX_ActivationKeys_DeviceRecordId_Unconsumed
    // (a concurrent regeneration conflict, T-49/T-50) — never for an unrelated DbUpdateException, which
    // must remain an unexpected persistence failure. SQL Server raises error 2601 (duplicate key in a
    // unique index) or 2627 (unique constraint); the index name pins it to this constraint.
    private static bool IsUnconsumedKeyUniqueIndexViolation(Exception exception)
    {
        const string indexName = "IX_ActivationKeys_DeviceRecordId_Unconsumed";
        for (var current = exception; current is not null; current = current.InnerException)
        {
            if (current is SqlException sqlException
                && sqlException.Number is 2601 or 2627
                && sqlException.Message.Contains(indexName, StringComparison.Ordinal))
            {
                return true;
            }
        }

        return false;
    }

    public async Task<DeviceActivationResult> ActivateAsync(
        string activationKey,
        CancellationToken cancellationToken = default)
    {
        // Parse before any lookup: a value that does not split into a non-empty keyId and a non-empty
        // secret is malformed and rejected outright (FS-02 §5.5 step 3, §12) — no transaction is
        // opened and no record is read.
        if (!TryParseActivationKey(activationKey, out var keyId, out var secret))
        {
            return DeviceActivationResult.Rejected(DeviceActivationFailureReason.Malformed);
        }

        // Lookup, verification, status check and consumption share one transaction so the consumption
        // is atomic (FS-02 §5.5 step 7, §12; IP-01 §8 step 6). The Activation Key row is read under an
        // update lock (UPDLOCK): two concurrent activations of the same key serialise on that lock, so
        // once the winner commits the other reads the key as Consumed and exactly one succeeds
        // (AC-16). SQL Server enforces this, so the behaviour is covered by integration tests against
        // a real database (IP-01 §9); the dedicated concurrent-request test is a later task (T-21).
        await using var transaction =
            await _dbContext.Database.BeginTransactionAsync(cancellationToken);

        // Indexed primary-key lookup by keyId, never a scan/hash-compare across every stored row
        // (AC-14). SELECT * returns exactly the mapped columns; the UPDLOCK/ROWLOCK hint holds the row
        // lock for the transaction. keyId is passed as a parameter, never interpolated into SQL text.
        var activationKeyRecord = await _dbContext.ActivationKeys
            .FromSqlInterpolated(
                $"SELECT * FROM ActivationKeys WITH (UPDLOCK, ROWLOCK) WHERE ActivationKeyId = {keyId}")
            .SingleOrDefaultAsync(cancellationToken);
        if (activationKeyRecord is null)
        {
            // Unknown keyId — same observable outcome as a malformed key or wrong secret (FS-02 §5.6).
            return DeviceActivationResult.Rejected(DeviceActivationFailureReason.UnknownKeyId);
        }

        // Verify the presented secret against the stored salted hash (FS-02 §5.5 step 5). A non-match
        // is rejected identically to an unknown keyId (§5.6, AC-15).
        if (!_activationKeyGenerator.VerifySecret(secret, activationKeyRecord.SecretHash))
        {
            return DeviceActivationResult.Rejected(DeviceActivationFailureReason.IncorrectSecret);
        }

        // The record must be Unconsumed to proceed; a Consumed or Invalidated key is rejected with no
        // side effects (FS-02 §5.7, §12). This is what makes an Activation Key one-time (BR-003) and
        // rejects replay of a consumed key (AC-4/AC-9). Unconsumed falls through to consumption below.
        switch (activationKeyRecord.Status)
        {
            case ActivationKeyStatus.Consumed:
                return DeviceActivationResult.Rejected(DeviceActivationFailureReason.Consumed);
            case ActivationKeyStatus.Invalidated:
                return DeviceActivationResult.Rejected(DeviceActivationFailureReason.Invalidated);
        }

        // The Device the key reserves, by internal DeviceRecordId (never the external DeviceId — which
        // is still NULL on a first activation, FS-02 §1.3). Tracked, so the activation transition is
        // persisted.
        var device = await _dbContext.Devices
            .SingleAsync(d => d.DeviceRecordId == activationKeyRecord.DeviceRecordId, cancellationToken);

        // A fresh, cryptographically secure shared secret is issued on every activation (first and
        // reactivation), rotating any previous one (NFR-SEC-002, ADR-015). Only its protected form is
        // stored; the plaintext exists transiently to be returned once and is never persisted or
        // logged (FS-02 §11, §12).
        var sharedSecret = GenerateSharedSecret();
        var protectedSharedSecret = _deviceSecretProtector.Protect(sharedSecret);

        // The atomic state transition (FS-02 §5.5 step 7). Consume() enforces one-time use as an
        // entity invariant; Activate() assigns DeviceId only on a first activation and retains it
        // thereafter (AC-7), and always rotates the protected secret.
        activationKeyRecord.Consume();
        device.Activate(protectedSharedSecret);

        try
        {
            await _dbContext.SaveChangesAsync(cancellationToken);
            await transaction.CommitAsync(cancellationToken);
        }
        catch (DbUpdateException ex)
        {
            await transaction.RollbackAsync(cancellationToken);

            // The shared secret is never interpolated into the message (FS-02 §11).
            throw new InvalidOperationException(
                "Device activation failed while persisting the activation state.", ex);
        }

        // DeviceId is guaranteed non-null here — Activate assigned it if it was not already set.
        return DeviceActivationResult.Activated(
            new DeviceActivationSuccess(device.DeviceId!.Value, sharedSecret, device.BranchId));
    }

    // Splits a presented Activation Key into its keyId and secret halves (FS-02 §1.4). The generator
    // builds `keyId.secret` from a Base64Url alphabet that never contains the delimiter, so a
    // well-formed key splits into exactly two non-empty parts (FS-02 §5.5 step 3); anything else —
    // blank input, no delimiter, more than one delimiter, or an empty half — is malformed. Neither
    // half is ever logged.
    private static bool TryParseActivationKey(string? activationKey, out string keyId, out string secret)
    {
        keyId = string.Empty;
        secret = string.Empty;

        if (string.IsNullOrWhiteSpace(activationKey))
        {
            return false;
        }

        var parts = activationKey.Trim().Split(ActivationKeyGenerator.Delimiter);
        if (parts.Length != 2
            || string.IsNullOrWhiteSpace(parts[0])
            || string.IsNullOrWhiteSpace(parts[1]))
        {
            return false;
        }

        keyId = parts[0];
        secret = parts[1];
        return true;
    }

    private static string GenerateSharedSecret()
    {
        var bytes = RandomNumberGenerator.GetBytes(SharedSecretSizeBytes);
        return Base64Url.EncodeToString(bytes);
    }
}

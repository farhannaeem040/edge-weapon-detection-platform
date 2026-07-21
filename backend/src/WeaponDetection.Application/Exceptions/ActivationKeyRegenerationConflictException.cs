namespace WeaponDetection.Application.Exceptions;

// The typed conflict outcome of Activation Key regeneration (IP-05 T-50): a concurrent regeneration
// for the same device already committed the single permitted Unconsumed key, so this request lost the
// race on the filtered unique index (IX_ActivationKeys_DeviceRecordId_Unconsumed, T-49) and rolled
// back without committing a key.
//
// It is deliberately an exception rather than a variant on the success/not-found return type: the
// existing regeneration contract returns ActivationKeyRegenerationResult? (null = not found), and the
// existing service already signals an exceptional persistence outcome by throwing — so this is the
// closest existing convention, and it keeps the API controller (which distinguishes only
// success/not-found today) unchanged in T-50. The API task (T-52) maps this to
// HTTP 409 / errorCode ACTIVATION_KEY_REGENERATION_CONFLICT.
//
// It carries no plaintext key, no shared secret, no device id, and no row content — a losing request
// returns nothing that was never committed, and the message must stay safe to log.
public sealed class ActivationKeyRegenerationConflictException : Exception
{
    public ActivationKeyRegenerationConflictException()
        : base("A concurrent Activation Key regeneration for the same device won the race; this request was rolled back.")
    {
    }
}

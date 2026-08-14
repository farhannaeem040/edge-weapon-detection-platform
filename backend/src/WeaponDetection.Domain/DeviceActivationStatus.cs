namespace WeaponDetection.Domain;

// The states FS-02 §9 defines for Device.ActivationStatus.
//
// ARCH-001 §16's longer device lifecycle (`Unprovisioned → Activation Pending → Activated →
// Online ⇄ Offline`) is not this enum: Online/Offline are *health* states, owned by a later
// feature's separate Device.Status field.
//
// `ReactivationRequired` (IP-05, FS-02 §5.3 amended) is entered when the Admin regenerates the
// Activation Key of an already-activated device: that regeneration immediately revokes the device's
// shared secret as a security-first credential reset. It is an *activation* state, deliberately
// distinct from the health state "Offline" (§14 of the IP-05 brief reserves Offline for future
// heartbeat-based connectivity). A device leaves `ReactivationRequired` for `Activated` on a
// successful reactivation (§5.8), retaining its permanent Device ID.
public enum DeviceActivationStatus
{
    Unactivated = 0,
    Activated = 1,
    ReactivationRequired = 2,
}

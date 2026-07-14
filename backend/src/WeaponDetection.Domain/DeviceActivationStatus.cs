namespace WeaponDetection.Domain;

// The two states FS-02 §9 defines for Device.ActivationStatus.
//
// ARCH-001 §16's longer device lifecycle (`Unprovisioned → Activation Pending → Activated →
// Online ⇄ Offline`) is not this enum: Online/Offline are *health* states, owned by a later
// feature's separate Device.Status field. ActivationStatus answers only "has this device ever
// completed activation", which is exactly the two values below.
public enum DeviceActivationStatus
{
    Unactivated = 0,
    Activated = 1,
}

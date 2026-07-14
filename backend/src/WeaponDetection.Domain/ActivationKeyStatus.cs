namespace WeaponDetection.Domain;

// The three states FS-02 §1.4/§9 defines for an Activation Key record.
//
// Consumed and Invalidated are distinct states, not one "unusable" state: they are reached by
// different events (a successful activation vs. an Admin regeneration, FS-02 §5.7) and the
// regeneration rule turns on the difference — Invalidate() applies "regardless of its prior
// consumption state" (§5.3 step 3). Both are rejected identically at the API boundary (§5.7),
// but that is a presentation rule, not a reason to collapse them in the model.
public enum ActivationKeyStatus
{
    Unconsumed = 0,
    Consumed = 1,
    Invalidated = 2,
}

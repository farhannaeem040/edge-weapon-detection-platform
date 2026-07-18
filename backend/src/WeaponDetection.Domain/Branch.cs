namespace WeaponDetection.Domain;

// The monitored physical location (ARCH-001 §13.1, FS-02 §9). Owns one or more Camera records.
//
// "Contact details" is modelled as a single free-text value: FS-02 §19 explicitly leaves the exact
// fields comprising it open, and ARCH-001 §13.1 lists it as one field — decomposing it into
// phone/email/contact-name columns would be inventing structure no approved document requires.
//
// The maximum lengths below are the single source of truth for both the Domain invariant and the
// EF column mapping (BranchConfiguration reads these constants), so the two cannot drift apart.
public class Branch
{
    public const int NameMaxLength = 200;
    public const int AddressMaxLength = 500;
    public const int ContactDetailsMaxLength = 500;

    public Guid BranchId { get; private set; }
    public string Name { get; private set; }
    public string Address { get; private set; }
    public string ContactDetails { get; private set; }

    // Required by EF Core for materialization; never used by application code.
    private Branch()
    {
        Name = null!;
        Address = null!;
        ContactDetails = null!;
    }

    public Branch(string name, string address, string contactDetails)
    {
        Name = Require(name, NameMaxLength, "Branch name", nameof(name));
        Address = Require(address, AddressMaxLength, "Branch address", nameof(address));
        ContactDetails = Require(
            contactDetails, ContactDetailsMaxLength, "Branch contact details", nameof(contactDetails));

        BranchId = Guid.NewGuid();
    }

    // Edits the branch's scalar fields (FS-03 §5.1, AC-1). It reuses the same Require validation the
    // constructor applies, so an edit can never move the entity into a state the constructor would
    // have rejected — the invariant lives in one place. BranchId is deliberately never touched: the
    // public branch identity is preserved across every edit (FS-03 §5.3, preservation rules). This
    // mutator concerns only the branch's own fields; its Device and Activation Key records are a
    // separate lifecycle this method has, and can have, no effect on.
    public void UpdateDetails(string name, string address, string contactDetails)
    {
        Name = Require(name, NameMaxLength, "Branch name", nameof(name));
        Address = Require(address, AddressMaxLength, "Branch address", nameof(address));
        ContactDetails = Require(
            contactDetails, ContactDetailsMaxLength, "Branch contact details", nameof(contactDetails));
    }

    // Trims first, then length-checks, so trailing whitespace can never push an otherwise valid
    // value over the limit. The offending value is never interpolated into the message — an
    // exception is a diagnostic, not a place to echo back operational data.
    private static string Require(string value, int maxLength, string label, string parameterName)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            throw new ArgumentException($"{label} is required.", parameterName);
        }

        var trimmed = value.Trim();

        if (trimmed.Length > maxLength)
        {
            throw new ArgumentException(
                $"{label} must not exceed {maxLength} characters.", parameterName);
        }

        return trimmed;
    }
}

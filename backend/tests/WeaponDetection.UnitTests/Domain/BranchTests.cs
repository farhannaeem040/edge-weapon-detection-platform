using System;
using WeaponDetection.Domain;
using Xunit;

namespace WeaponDetection.UnitTests.Domain;

// Branch invariants (FS-02 §9, ARCH-001 §13.1). Name, address, and contact details are all
// required; nothing else about a Branch is constrained by an approved document.
public class BranchTests
{
    private const string Name = "Central Branch";
    private const string Address = "10 Example Street, Example City";
    private const string ContactDetails = "branch-manager@example.invalid";

    [Fact]
    public void Constructor_ValidValues_SetsAllFields()
    {
        var branch = new Branch(Name, Address, ContactDetails);

        Assert.Equal(Name, branch.Name);
        Assert.Equal(Address, branch.Address);
        Assert.Equal(ContactDetails, branch.ContactDetails);
    }

    [Fact]
    public void Constructor_GeneratesANonEmptyBranchId()
    {
        var branch = new Branch(Name, Address, ContactDetails);

        Assert.NotEqual(Guid.Empty, branch.BranchId);
    }

    [Fact]
    public void Constructor_GeneratesAUniqueBranchIdPerInstance()
    {
        var first = new Branch(Name, Address, ContactDetails);
        var second = new Branch(Name, Address, ContactDetails);

        Assert.NotEqual(first.BranchId, second.BranchId);
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("   ")]
    public void Constructor_MissingOrBlankName_Throws(string? name)
    {
        Assert.Throws<ArgumentException>(() => new Branch(name!, Address, ContactDetails));
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("   ")]
    public void Constructor_MissingOrBlankAddress_Throws(string? address)
    {
        Assert.Throws<ArgumentException>(() => new Branch(Name, address!, ContactDetails));
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("   ")]
    public void Constructor_MissingOrBlankContactDetails_Throws(string? contactDetails)
    {
        Assert.Throws<ArgumentException>(() => new Branch(Name, Address, contactDetails!));
    }

    [Fact]
    public void Constructor_TrimsSurroundingWhitespaceFromEveryField()
    {
        var branch = new Branch($"  {Name}  ", $"\t{Address}\n", $" {ContactDetails} ");

        Assert.Equal(Name, branch.Name);
        Assert.Equal(Address, branch.Address);
        Assert.Equal(ContactDetails, branch.ContactDetails);
    }

    [Fact]
    public void Constructor_NameAtExactlyTheMaximumLength_IsAccepted()
    {
        var name = new string('n', Branch.NameMaxLength);

        var branch = new Branch(name, Address, ContactDetails);

        Assert.Equal(Branch.NameMaxLength, branch.Name.Length);
    }

    [Fact]
    public void Constructor_NameLongerThanTheMaximum_Throws()
    {
        var name = new string('n', Branch.NameMaxLength + 1);

        Assert.Throws<ArgumentException>(() => new Branch(name, Address, ContactDetails));
    }

    [Fact]
    public void Constructor_AddressLongerThanTheMaximum_Throws()
    {
        var address = new string('a', Branch.AddressMaxLength + 1);

        Assert.Throws<ArgumentException>(() => new Branch(Name, address, ContactDetails));
    }

    [Fact]
    public void Constructor_ContactDetailsLongerThanTheMaximum_Throws()
    {
        var contactDetails = new string('c', Branch.ContactDetailsMaxLength + 1);

        Assert.Throws<ArgumentException>(() => new Branch(Name, Address, contactDetails));
    }

    [Fact]
    public void Constructor_ValueIsTrimmedBeforeTheLengthCheck()
    {
        // Trailing whitespace must not be what pushes an otherwise-valid value over the limit.
        var paddedName = new string('n', Branch.NameMaxLength) + "     ";

        var branch = new Branch(paddedName, Address, ContactDetails);

        Assert.Equal(Branch.NameMaxLength, branch.Name.Length);
    }
}

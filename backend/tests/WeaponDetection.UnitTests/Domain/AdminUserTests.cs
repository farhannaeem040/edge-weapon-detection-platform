using WeaponDetection.Domain;
using Xunit;

namespace WeaponDetection.UnitTests.Domain;

public class AdminUserTests
{
    [Fact]
    public void Constructor_WithValidValues_CreatesUser()
    {
        var user = new AdminUser("admin", "hashed-password");

        Assert.NotEqual(Guid.Empty, user.UserId);
        Assert.Equal("admin", user.CredentialIdentifier);
        Assert.Equal("hashed-password", user.PasswordHash);
    }

    [Fact]
    public void Constructor_TrimsCredentialIdentifier()
    {
        var user = new AdminUser("  admin  ", "hashed-password");

        Assert.Equal("admin", user.CredentialIdentifier);
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("   ")]
    public void Constructor_WithBlankCredentialIdentifier_Throws(string? credentialIdentifier)
    {
        Assert.Throws<ArgumentException>(() => new AdminUser(credentialIdentifier!, "hashed-password"));
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("   ")]
    public void Constructor_WithBlankPasswordHash_Throws(string? passwordHash)
    {
        Assert.Throws<ArgumentException>(() => new AdminUser("admin", passwordHash!));
    }
}

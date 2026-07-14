using System.Linq;
using Microsoft.EntityFrameworkCore;
using WeaponDetection.Domain;
using WeaponDetection.Infrastructure.Persistence;
using Xunit;

namespace WeaponDetection.UnitTests.Persistence;

// Inspects the EF Core model metadata directly. Building the model requires a configured
// provider (UseSqlServer) but never opens a real connection, so a placeholder connection
// string is sufficient here — relational constraint *enforcement* is verified separately
// against a real SQL Server database (see IntegrationTests).
public class AdminSchemaModelTests
{
    private const string PlaceholderConnectionString =
        "Server=localhost;Database=WeaponDetectionModelTests;Trusted_Connection=True;TrustServerCertificate=True;";

    private static WeaponDetectionDbContext CreateContext()
    {
        var options = new DbContextOptionsBuilder<WeaponDetectionDbContext>()
            .UseSqlServer(PlaceholderConnectionString)
            .Options;

        return new WeaponDetectionDbContext(options);
    }

    [Fact]
    public void AdminUser_HasPrimaryKeyOnUserId()
    {
        using var context = CreateContext();
        var entityType = context.Model.FindEntityType(typeof(AdminUser))!;

        var primaryKey = entityType.FindPrimaryKey()!;

        Assert.Single(primaryKey.Properties);
        Assert.Equal(nameof(AdminUser.UserId), primaryKey.Properties[0].Name);
    }

    [Fact]
    public void AdminUser_CredentialIdentifier_HasUniqueIndex()
    {
        using var context = CreateContext();
        var entityType = context.Model.FindEntityType(typeof(AdminUser))!;

        var index = entityType.GetIndexes()
            .Single(i => i.Properties.Count == 1 && i.Properties[0].Name == nameof(AdminUser.CredentialIdentifier));

        Assert.True(index.IsUnique);
    }

    [Fact]
    public void AdminUser_PasswordHash_IsRequired()
    {
        using var context = CreateContext();
        var entityType = context.Model.FindEntityType(typeof(AdminUser))!;

        var property = entityType.FindProperty(nameof(AdminUser.PasswordHash))!;

        Assert.False(property.IsNullable);
    }

    [Fact]
    public void AdminSession_HasPrimaryKeyOnSessionId()
    {
        using var context = CreateContext();
        var entityType = context.Model.FindEntityType(typeof(AdminSession))!;

        var primaryKey = entityType.FindPrimaryKey()!;

        Assert.Single(primaryKey.Properties);
        Assert.Equal(nameof(AdminSession.SessionId), primaryKey.Properties[0].Name);
    }

    [Fact]
    public void AdminSession_UserId_IsRequiredForeignKeyToAdminUser()
    {
        using var context = CreateContext();
        var entityType = context.Model.FindEntityType(typeof(AdminSession))!;

        var foreignKey = entityType.GetForeignKeys().Single();

        Assert.Equal(typeof(AdminUser), foreignKey.PrincipalEntityType.ClrType);
        Assert.Equal(nameof(AdminSession.UserId), foreignKey.Properties.Single().Name);
        Assert.False(foreignKey.Properties.Single().IsNullable);
        Assert.True(foreignKey.IsRequired);
    }

    [Fact]
    public void AdminSession_ForeignKey_CascadesOnDelete()
    {
        using var context = CreateContext();
        var entityType = context.Model.FindEntityType(typeof(AdminSession))!;

        var foreignKey = entityType.GetForeignKeys().Single();

        Assert.Equal(DeleteBehavior.Cascade, foreignKey.DeleteBehavior);
    }

    [Fact]
    public void AdminSession_HasIndexOnUserId()
    {
        using var context = CreateContext();
        var entityType = context.Model.FindEntityType(typeof(AdminSession))!;

        var hasUserIdIndex = entityType.GetIndexes()
            .Any(i => i.Properties.Count == 1 && i.Properties[0].Name == nameof(AdminSession.UserId));

        Assert.True(hasUserIdIndex);
    }

    [Fact]
    public void AdminSession_IssuedAtExpiresAtRevoked_AreRequired()
    {
        using var context = CreateContext();
        var entityType = context.Model.FindEntityType(typeof(AdminSession))!;

        Assert.False(entityType.FindProperty(nameof(AdminSession.IssuedAt))!.IsNullable);
        Assert.False(entityType.FindProperty(nameof(AdminSession.ExpiresAt))!.IsNullable);
        Assert.False(entityType.FindProperty(nameof(AdminSession.Revoked))!.IsNullable);
    }
}

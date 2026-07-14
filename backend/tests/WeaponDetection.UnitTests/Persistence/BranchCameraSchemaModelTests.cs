using System.Linq;
using Microsoft.EntityFrameworkCore;
using WeaponDetection.Domain;
using WeaponDetection.Infrastructure.Persistence;
using Xunit;

namespace WeaponDetection.UnitTests.Persistence;

// Inspects the EF Core model metadata for the M2 (Branch/Camera) schema. Building the model
// requires a configured provider (UseSqlServer) but never opens a real connection — relational
// constraint *enforcement* is verified against a real SQL Server database in
// BranchCameraSchemaSqlServerTests (IP-01 §9).
public class BranchCameraSchemaModelTests
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
    public void Branch_HasPrimaryKeyOnBranchId()
    {
        using var context = CreateContext();
        var entityType = context.Model.FindEntityType(typeof(Branch))!;

        var primaryKey = entityType.FindPrimaryKey()!;

        Assert.Single(primaryKey.Properties);
        Assert.Equal(nameof(Branch.BranchId), primaryKey.Properties[0].Name);
    }

    [Theory]
    [InlineData(nameof(Branch.Name), Branch.NameMaxLength)]
    [InlineData(nameof(Branch.Address), Branch.AddressMaxLength)]
    [InlineData(nameof(Branch.ContactDetails), Branch.ContactDetailsMaxLength)]
    public void Branch_RequiredProperties_AreNonNullableWithTheDomainsMaxLength(
        string propertyName, int expectedMaxLength)
    {
        using var context = CreateContext();
        var entityType = context.Model.FindEntityType(typeof(Branch))!;

        var property = entityType.FindProperty(propertyName)!;

        Assert.False(property.IsNullable);
        Assert.Equal(expectedMaxLength, property.GetMaxLength());
    }

    [Fact]
    public void Branch_HasNoUniqueIndex()
    {
        // IP-01 §4 states no constraint for Branch, and no approved document forbids two branches
        // sharing a name. A unique index here would be an invented business rule.
        using var context = CreateContext();
        var entityType = context.Model.FindEntityType(typeof(Branch))!;

        Assert.DoesNotContain(entityType.GetIndexes(), i => i.IsUnique);
    }

    [Fact]
    public void Camera_HasPrimaryKeyOnCameraId()
    {
        using var context = CreateContext();
        var entityType = context.Model.FindEntityType(typeof(Camera))!;

        var primaryKey = entityType.FindPrimaryKey()!;

        Assert.Single(primaryKey.Properties);
        Assert.Equal(nameof(Camera.CameraId), primaryKey.Properties[0].Name);
    }

    [Fact]
    public void Camera_BranchId_IsRequiredForeignKeyToBranch()
    {
        using var context = CreateContext();
        var entityType = context.Model.FindEntityType(typeof(Camera))!;

        var foreignKey = entityType.GetForeignKeys().Single();

        Assert.Equal(typeof(Branch), foreignKey.PrincipalEntityType.ClrType);
        Assert.Equal(nameof(Camera.BranchId), foreignKey.Properties.Single().Name);
        Assert.False(foreignKey.Properties.Single().IsNullable);
        Assert.True(foreignKey.IsRequired);
    }

    [Fact]
    public void Camera_ForeignKey_CascadesOnDelete()
    {
        using var context = CreateContext();
        var entityType = context.Model.FindEntityType(typeof(Camera))!;

        var foreignKey = entityType.GetForeignKeys().Single();

        Assert.Equal(DeleteBehavior.Cascade, foreignKey.DeleteBehavior);
    }

    [Fact]
    public void Camera_BranchIdIndex_IsNotUnique_BecauseABranchOwnsOneOrMoreCameras()
    {
        // ARCH-001 §13.1 / FS-02 §9, §12: a Branch owns *one or more* Cameras. The index on the FK
        // serves branch-scoped lookups; it must not cap a branch at a single camera. (The
        // one-per-branch uniqueness in the approved specs belongs to Device — T-13, not here.)
        using var context = CreateContext();
        var entityType = context.Model.FindEntityType(typeof(Camera))!;

        var index = entityType.GetIndexes()
            .Single(i => i.Properties.Count == 1 && i.Properties[0].Name == nameof(Camera.BranchId));

        Assert.False(index.IsUnique);
    }

    [Theory]
    [InlineData(nameof(Camera.Name), Camera.NameMaxLength)]
    [InlineData(nameof(Camera.RtspUrl), Camera.RtspUrlMaxLength)]
    public void Camera_RequiredProperties_AreNonNullableWithTheDomainsMaxLength(
        string propertyName, int expectedMaxLength)
    {
        using var context = CreateContext();
        var entityType = context.Model.FindEntityType(typeof(Camera))!;

        var property = entityType.FindProperty(propertyName)!;

        Assert.False(property.IsNullable);
        Assert.Equal(expectedMaxLength, property.GetMaxLength());
    }

    [Fact]
    public void Camera_Enabled_IsRequired()
    {
        using var context = CreateContext();
        var entityType = context.Model.FindEntityType(typeof(Camera))!;

        Assert.False(entityType.FindProperty(nameof(Camera.Enabled))!.IsNullable);
    }

    [Fact]
    public void Model_ContainsNoDeviceOrActivationKeyEntity()
    {
        // T-13 work must not have leaked into T-12.
        using var context = CreateContext();

        var entityNames = context.Model.GetEntityTypes().Select(e => e.ClrType.Name).ToList();

        Assert.DoesNotContain("Device", entityNames);
        Assert.DoesNotContain("ActivationKey", entityNames);
    }
}

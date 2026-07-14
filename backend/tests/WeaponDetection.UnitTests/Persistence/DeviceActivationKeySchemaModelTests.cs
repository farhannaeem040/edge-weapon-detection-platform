using System.Linq;
using Microsoft.EntityFrameworkCore;
using WeaponDetection.Domain;
using WeaponDetection.Infrastructure.Persistence;
using Xunit;

namespace WeaponDetection.UnitTests.Persistence;

// Inspects the EF Core model metadata for the M3 (Device/ActivationKey) schema. Building the model
// requires a configured provider (UseSqlServer) but never opens a real connection — relational
// constraint *enforcement* is verified against a real SQL Server database in
// DeviceActivationKeySchemaSqlServerTests (IP-01 §9).
public class DeviceActivationKeySchemaModelTests
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
    public void Device_HasPrimaryKeyOnDeviceRecordId_NotOnDeviceId()
    {
        // FS-02 §1.3: the internal record id is the key; the external DeviceId is NULL until
        // activation and so could not be a primary key at all.
        using var context = CreateContext();
        var entityType = context.Model.FindEntityType(typeof(Device))!;

        var primaryKey = entityType.FindPrimaryKey()!;

        Assert.Single(primaryKey.Properties);
        Assert.Equal(nameof(Device.DeviceRecordId), primaryKey.Properties[0].Name);
    }

    [Fact]
    public void Device_DeviceId_IsNullable()
    {
        // NULL from branch creation until first activation (FS-02 §1.3).
        using var context = CreateContext();
        var entityType = context.Model.FindEntityType(typeof(Device))!;

        Assert.True(entityType.FindProperty(nameof(Device.DeviceId))!.IsNullable);
    }

    [Fact]
    public void Device_DeviceIdIndex_IsUniqueButFilteredToNonNullValues()
    {
        // The distinction that matters (IP-01 §4): a *plain* unique index would treat the many
        // pre-activation NULLs as duplicates of each other and reject the second unactivated
        // device. The filter confines uniqueness to devices that have actually activated.
        using var context = CreateContext();
        var entityType = context.Model.FindEntityType(typeof(Device))!;

        var index = entityType.GetIndexes()
            .Single(i => i.Properties.Count == 1 && i.Properties[0].Name == nameof(Device.DeviceId));

        Assert.True(index.IsUnique);
        Assert.Equal("[DeviceId] IS NOT NULL", index.GetFilter());
    }

    [Fact]
    public void Device_BranchIdIndex_IsUnique_BecauseABranchHasExactlyOneDevice()
    {
        // BR-002/CON-007. The deliberate contrast with Camera.BranchId, whose index is *not*
        // unique because a Branch owns one or more Cameras.
        using var context = CreateContext();
        var entityType = context.Model.FindEntityType(typeof(Device))!;

        var index = entityType.GetIndexes()
            .Single(i => i.Properties.Count == 1 && i.Properties[0].Name == nameof(Device.BranchId));

        Assert.True(index.IsUnique);
        Assert.Null(index.GetFilter());
    }

    [Fact]
    public void Device_BranchId_IsRequiredForeignKeyToBranch_CascadingOnDelete()
    {
        using var context = CreateContext();
        var entityType = context.Model.FindEntityType(typeof(Device))!;

        var foreignKey = entityType.GetForeignKeys().Single();

        Assert.Equal(typeof(Branch), foreignKey.PrincipalEntityType.ClrType);
        Assert.Equal(nameof(Device.BranchId), foreignKey.Properties.Single().Name);
        Assert.True(foreignKey.IsRequired);
        Assert.Equal(DeleteBehavior.Cascade, foreignKey.DeleteBehavior);
    }

    [Fact]
    public void Device_ActivationStatus_IsStoredAsItsName()
    {
        using var context = CreateContext();
        var entityType = context.Model.FindEntityType(typeof(Device))!;

        var property = entityType.FindProperty(nameof(Device.ActivationStatus))!;

        Assert.False(property.IsNullable);
        Assert.Equal(typeof(string), property.GetProviderClrType());
    }

    [Theory]
    [InlineData(nameof(Device.ProtectedSharedSecret), Device.ProtectedSharedSecretMaxLength)]
    [InlineData(nameof(Device.LastKnownAddress), Device.LastKnownAddressMaxLength)]
    public void Device_PostActivationProperties_AreNullableWithTheDomainsMaxLength(
        string propertyName, int expectedMaxLength)
    {
        // Both are NULL until the device activates / first makes operational contact (FS-02 §9).
        using var context = CreateContext();
        var entityType = context.Model.FindEntityType(typeof(Device))!;

        var property = entityType.FindProperty(propertyName)!;

        Assert.True(property.IsNullable);
        Assert.Equal(expectedMaxLength, property.GetMaxLength());
    }

    [Fact]
    public void ActivationKey_HasPrimaryKeyOnActivationKeyId()
    {
        // AC-14: the keyId *is* the primary key, so an activation resolves its record by a direct
        // indexed seek rather than by hashing the presented secret against every stored row.
        using var context = CreateContext();
        var entityType = context.Model.FindEntityType(typeof(ActivationKey))!;

        var primaryKey = entityType.FindPrimaryKey()!;

        Assert.Single(primaryKey.Properties);
        Assert.Equal(nameof(ActivationKey.ActivationKeyId), primaryKey.Properties[0].Name);
    }

    [Fact]
    public void ActivationKey_ActivationKeyId_IsBoundedNonUnicode()
    {
        using var context = CreateContext();
        var entityType = context.Model.FindEntityType(typeof(ActivationKey))!;

        var property = entityType.FindProperty(nameof(ActivationKey.ActivationKeyId))!;

        Assert.False(property.IsNullable);
        Assert.Equal(ActivationKey.ActivationKeyIdMaxLength, property.GetMaxLength());
        Assert.False(property.IsUnicode());
    }

    [Fact]
    public void ActivationKey_SecretHash_IsRequiredWithTheDomainsMaxLength()
    {
        using var context = CreateContext();
        var entityType = context.Model.FindEntityType(typeof(ActivationKey))!;

        var property = entityType.FindProperty(nameof(ActivationKey.SecretHash))!;

        Assert.False(property.IsNullable);
        Assert.Equal(ActivationKey.SecretHashMaxLength, property.GetMaxLength());
    }

    [Fact]
    public void ActivationKey_Status_IsStoredAsItsName()
    {
        using var context = CreateContext();
        var entityType = context.Model.FindEntityType(typeof(ActivationKey))!;

        var property = entityType.FindProperty(nameof(ActivationKey.Status))!;

        Assert.False(property.IsNullable);
        Assert.Equal(typeof(string), property.GetProviderClrType());
    }

    [Fact]
    public void ActivationKey_HasANonUniqueIndexOnDeviceRecordIdAndStatus()
    {
        // Serves regeneration's "find the current Unconsumed key for this Device" (IP-01 §4). Not
        // unique: a Device accumulates a history of keys as the Admin regenerates them.
        using var context = CreateContext();
        var entityType = context.Model.FindEntityType(typeof(ActivationKey))!;

        var index = entityType.GetIndexes().Single(i => i.Properties.Count == 2);

        Assert.Equal(nameof(ActivationKey.DeviceRecordId), index.Properties[0].Name);
        Assert.Equal(nameof(ActivationKey.Status), index.Properties[1].Name);
        Assert.False(index.IsUnique);
    }

    [Fact]
    public void ActivationKey_ForeignKey_IsToTheInternalDeviceRecordId_CascadingOnDelete()
    {
        // Never to the external DeviceId, which is NULL when a key is first issued (FS-02 §1.3).
        using var context = CreateContext();
        var entityType = context.Model.FindEntityType(typeof(ActivationKey))!;

        var foreignKey = entityType.GetForeignKeys().Single();

        Assert.Equal(typeof(Device), foreignKey.PrincipalEntityType.ClrType);
        Assert.Equal(nameof(ActivationKey.DeviceRecordId), foreignKey.Properties.Single().Name);
        Assert.Equal(
            nameof(Device.DeviceRecordId), foreignKey.PrincipalKey.Properties.Single().Name);
        Assert.True(foreignKey.IsRequired);
        Assert.Equal(DeleteBehavior.Cascade, foreignKey.DeleteBehavior);
    }
}

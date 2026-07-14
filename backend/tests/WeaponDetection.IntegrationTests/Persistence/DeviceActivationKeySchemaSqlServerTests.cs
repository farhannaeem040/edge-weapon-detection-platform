using System;
using System.Collections.Generic;
using System.Linq;
using Microsoft.Data.SqlClient;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Infrastructure;
using Microsoft.EntityFrameworkCore.Migrations;
using Microsoft.Extensions.DependencyInjection;
using WeaponDetection.Domain;
using WeaponDetection.Infrastructure.Persistence;
using Xunit;

namespace WeaponDetection.IntegrationTests.Persistence;

// Verifies the M3 (Device/ActivationKey) schema's relational behavior against a real SQL Server
// database. This is the class IP-01 §9 has in mind: a filtered unique index and a unique FK
// constraint are precisely the things EF Core InMemory/SQLite do not enforce faithfully, so
// verifying them against anything less than real SQL Server would prove nothing.
//
// Every secret- and hash-shaped value below is an obvious placeholder. No real key, secret, or
// hash appears in a committed test.
public class DeviceActivationKeySchemaSqlServerTests
    : IClassFixture<DeviceActivationKeySchemaSqlServerFixture>
{
    private const string ProtectedSecret = "protected-placeholder-secret-1";
    private const string RotatedProtectedSecret = "protected-placeholder-secret-2";
    private const string SecretHash = "placeholder-secret-hash";
    private const string BranchCameraSchemaMigration = "BranchCameraSchema";

    private static List<string> GetTableNames(WeaponDetectionDbContext context) =>
        context.Database
            .SqlQueryRaw<string>(
                "SELECT TABLE_NAME AS [Value] FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE = 'BASE TABLE'")
            .ToList();

    private static Branch AddBranch(WeaponDetectionDbContext context)
    {
        var branch = new Branch(
            $"Branch {Guid.NewGuid()}",
            "10 Example Street, Example City",
            "branch-manager@example.invalid");

        context.Branches.Add(branch);
        context.SaveChanges();

        return branch;
    }

    private static Device AddDevice(WeaponDetectionDbContext context, Branch branch)
    {
        var device = new Device(branch.BranchId);

        context.Devices.Add(device);
        context.SaveChanges();

        return device;
    }

    [Fact]
    public void Migration_CreatesDeviceAndActivationKeyTables()
    {
        using var context = DeviceActivationKeySchemaSqlServerFixture.CreateContext();

        var tableNames = GetTableNames(context);

        Assert.Contains("Devices", tableNames);
        Assert.Contains("ActivationKeys", tableNames);
    }

    [Fact]
    public void Migration_LeavesTheEarlierSchemasIntact()
    {
        using var context = DeviceActivationKeySchemaSqlServerFixture.CreateContext();

        var tableNames = GetTableNames(context);

        Assert.Contains("AdminUsers", tableNames);
        Assert.Contains("AdminSessions", tableNames);
        Assert.Contains("Branches", tableNames);
        Assert.Contains("Cameras", tableNames);
    }

    [Fact]
    public void Migration_SeedsNoDeviceOrActivationKeyData()
    {
        // Migrates a throwaway database of its own: emptiness is only meaningful on a database no
        // other test has written to, so the shared fixture cannot answer this question.
        var connectionString =
            $"Server=localhost\\SQLEXPRESS;Database=WeaponDetectionDeviceSeedCheck_{Guid.NewGuid():N};" +
            "Trusted_Connection=True;TrustServerCertificate=True;";

        var options = new DbContextOptionsBuilder<WeaponDetectionDbContext>()
            .UseSqlServer(connectionString)
            .Options;

        using var context = new WeaponDetectionDbContext(options);

        try
        {
            context.Database.Migrate();

            Assert.Empty(context.Devices);
            Assert.Empty(context.ActivationKeys);
        }
        finally
        {
            context.Database.EnsureDeleted();
        }
    }

    [Fact]
    public void Device_RoundTripsThroughTheDatabase_Unactivated()
    {
        using var context = DeviceActivationKeySchemaSqlServerFixture.CreateContext();
        var branch = AddBranch(context);
        var device = AddDevice(context, branch);

        using var verifyContext = DeviceActivationKeySchemaSqlServerFixture.CreateContext();
        var persisted = verifyContext.Devices.Single(d => d.DeviceRecordId == device.DeviceRecordId);

        Assert.Equal(branch.BranchId, persisted.BranchId);
        Assert.Null(persisted.DeviceId);
        Assert.Equal(DeviceActivationStatus.Unactivated, persisted.ActivationStatus);
        Assert.Null(persisted.ProtectedSharedSecret);
        Assert.Null(persisted.LastKnownAddress);
    }

    [Fact]
    public void UniqueConstraint_RejectsASecondDeviceForTheSameBranch()
    {
        // BR-002/CON-007 enforced by the database itself, so no service-layer bug can create a
        // second device for a branch.
        using var context = DeviceActivationKeySchemaSqlServerFixture.CreateContext();
        var branch = AddBranch(context);
        AddDevice(context, branch);

        context.Devices.Add(new Device(branch.BranchId));

        var exception = Assert.Throws<DbUpdateException>(() => context.SaveChanges());
        Assert.IsType<SqlException>(exception.InnerException);
    }

    [Fact]
    public void FilteredIndex_AllowsManyUnactivatedDevices_EachWithANullDeviceId()
    {
        // The test that proves the index is *filtered* and not naive: SQL Server treats NULLs as
        // equal for uniqueness, so a plain unique index on DeviceId would accept the first
        // unactivated device and reject every one after it — breaking branch creation outright.
        using var context = DeviceActivationKeySchemaSqlServerFixture.CreateContext();

        var firstDevice = AddDevice(context, AddBranch(context));
        var secondDevice = AddDevice(context, AddBranch(context));
        var thirdDevice = AddDevice(context, AddBranch(context));

        using var verifyContext = DeviceActivationKeySchemaSqlServerFixture.CreateContext();
        var deviceRecordIds = new[]
        {
            firstDevice.DeviceRecordId, secondDevice.DeviceRecordId, thirdDevice.DeviceRecordId,
        };

        var persisted = verifyContext.Devices
            .Where(d => deviceRecordIds.Contains(d.DeviceRecordId))
            .ToList();

        Assert.Equal(3, persisted.Count);
        Assert.All(persisted, d => Assert.Null(d.DeviceId));
    }

    [Fact]
    public void FilteredIndex_RejectsADuplicateDeviceId_AmongActivatedDevices()
    {
        // The other half of the same index: uniqueness *is* enforced once DeviceId is non-NULL.
        // Forced by raw SQL, because the Domain assigns each DeviceId itself and will not produce
        // a collision — the point here is that the database would refuse one even if it did.
        using var context = DeviceActivationKeySchemaSqlServerFixture.CreateContext();

        var firstDevice = AddDevice(context, AddBranch(context));
        var secondDevice = AddDevice(context, AddBranch(context));

        firstDevice.Activate(ProtectedSecret);
        secondDevice.Activate(ProtectedSecret);
        context.SaveChanges();

        var exception = Assert.Throws<SqlException>(() =>
            context.Database.ExecuteSqlRaw(
                "UPDATE Devices SET DeviceId = {0} WHERE DeviceRecordId = {1}",
                firstDevice.DeviceId!.Value,
                secondDevice.DeviceRecordId));

        Assert.Contains("IX_Devices_DeviceId", exception.Message);
    }

    [Fact]
    public void Device_Activation_PersistsTheAssignedDeviceIdAndProtectedSecret()
    {
        using var context = DeviceActivationKeySchemaSqlServerFixture.CreateContext();
        var device = AddDevice(context, AddBranch(context));

        device.Activate(ProtectedSecret);
        context.SaveChanges();

        using var verifyContext = DeviceActivationKeySchemaSqlServerFixture.CreateContext();
        var persisted = verifyContext.Devices.Single(d => d.DeviceRecordId == device.DeviceRecordId);

        Assert.Equal(device.DeviceId, persisted.DeviceId);
        Assert.Equal(DeviceActivationStatus.Activated, persisted.ActivationStatus);
        Assert.Equal(ProtectedSecret, persisted.ProtectedSharedSecret);
    }

    [Fact]
    public void Device_Reactivation_RetainsTheDeviceIdAndRotatesTheSecret_AcrossTheDatabase()
    {
        // AC-7 / FS-02 §5.8, verified through a real round-trip rather than only in memory.
        using var context = DeviceActivationKeySchemaSqlServerFixture.CreateContext();
        var device = AddDevice(context, AddBranch(context));

        device.Activate(ProtectedSecret);
        context.SaveChanges();
        var originalDeviceId = device.DeviceId;

        using var reactivateContext = DeviceActivationKeySchemaSqlServerFixture.CreateContext();
        var reloaded = reactivateContext.Devices
            .Single(d => d.DeviceRecordId == device.DeviceRecordId);

        reloaded.Activate(RotatedProtectedSecret);
        reactivateContext.SaveChanges();

        using var verifyContext = DeviceActivationKeySchemaSqlServerFixture.CreateContext();
        var persisted = verifyContext.Devices.Single(d => d.DeviceRecordId == device.DeviceRecordId);

        Assert.Equal(originalDeviceId, persisted.DeviceId);
        Assert.Equal(RotatedProtectedSecret, persisted.ProtectedSharedSecret);
    }

    [Fact]
    public void ForeignKeyConstraint_RejectsADeviceWithNoSuchBranch()
    {
        using var context = DeviceActivationKeySchemaSqlServerFixture.CreateContext();

        context.Devices.Add(new Device(Guid.NewGuid()));

        var exception = Assert.Throws<DbUpdateException>(() => context.SaveChanges());
        Assert.IsType<SqlException>(exception.InnerException);
    }

    [Fact]
    public void ActivationKey_RoundTripsThroughTheDatabase_Unconsumed()
    {
        using var context = DeviceActivationKeySchemaSqlServerFixture.CreateContext();
        var device = AddDevice(context, AddBranch(context));

        var key = new ActivationKey(Guid.NewGuid().ToString("N"), device.DeviceRecordId, SecretHash);
        context.ActivationKeys.Add(key);
        context.SaveChanges();

        using var verifyContext = DeviceActivationKeySchemaSqlServerFixture.CreateContext();
        var persisted = verifyContext.ActivationKeys
            .Single(k => k.ActivationKeyId == key.ActivationKeyId);

        Assert.Equal(device.DeviceRecordId, persisted.DeviceRecordId);
        Assert.Equal(SecretHash, persisted.SecretHash);
        Assert.Equal(ActivationKeyStatus.Unconsumed, persisted.Status);
    }

    [Fact]
    public void ActivationKey_ResolvesByItsKeyIdPrimaryKey()
    {
        // AC-14: the presented keyId resolves its record by a primary-key lookup — never by
        // scanning the table and comparing the presented secret against every stored hash.
        using var context = DeviceActivationKeySchemaSqlServerFixture.CreateContext();
        var device = AddDevice(context, AddBranch(context));

        var keyId = Guid.NewGuid().ToString("N");
        context.ActivationKeys.Add(new ActivationKey(keyId, device.DeviceRecordId, SecretHash));
        context.SaveChanges();

        using var verifyContext = DeviceActivationKeySchemaSqlServerFixture.CreateContext();
        var found = verifyContext.ActivationKeys.Find(keyId);

        Assert.NotNull(found);
        Assert.Equal(keyId, found!.ActivationKeyId);
    }

    [Fact]
    public void ActivationKey_PrimaryKey_RejectsADuplicateKeyId()
    {
        using var context = DeviceActivationKeySchemaSqlServerFixture.CreateContext();
        var device = AddDevice(context, AddBranch(context));

        var keyId = Guid.NewGuid().ToString("N");
        context.ActivationKeys.Add(new ActivationKey(keyId, device.DeviceRecordId, SecretHash));
        context.SaveChanges();

        using var duplicateContext = DeviceActivationKeySchemaSqlServerFixture.CreateContext();
        duplicateContext.ActivationKeys.Add(
            new ActivationKey(keyId, device.DeviceRecordId, SecretHash));

        var exception = Assert.Throws<DbUpdateException>(() => duplicateContext.SaveChanges());
        Assert.IsType<SqlException>(exception.InnerException);
    }

    [Fact]
    public void Device_CanAccumulateAHistoryOfActivationKeys()
    {
        // Regeneration (FS-02 §5.3) leaves the superseded records in place, so the FK index must
        // not be unique. Only the newest key is Unconsumed.
        using var context = DeviceActivationKeySchemaSqlServerFixture.CreateContext();
        var device = AddDevice(context, AddBranch(context));

        var consumedKey = new ActivationKey(
            Guid.NewGuid().ToString("N"), device.DeviceRecordId, SecretHash);
        consumedKey.Consume();

        var invalidatedKey = new ActivationKey(
            Guid.NewGuid().ToString("N"), device.DeviceRecordId, SecretHash);
        invalidatedKey.Invalidate();

        var currentKey = new ActivationKey(
            Guid.NewGuid().ToString("N"), device.DeviceRecordId, SecretHash);

        context.ActivationKeys.AddRange(consumedKey, invalidatedKey, currentKey);
        context.SaveChanges();

        using var verifyContext = DeviceActivationKeySchemaSqlServerFixture.CreateContext();
        var keys = verifyContext.ActivationKeys
            .Where(k => k.DeviceRecordId == device.DeviceRecordId)
            .ToList();

        Assert.Equal(3, keys.Count);
        Assert.Single(keys, k => k.Status == ActivationKeyStatus.Unconsumed);
        Assert.Single(keys, k => k.Status == ActivationKeyStatus.Consumed);
        Assert.Single(keys, k => k.Status == ActivationKeyStatus.Invalidated);
    }

    [Fact]
    public void ForeignKeyConstraint_RejectsAnActivationKeyWithNoSuchDevice()
    {
        using var context = DeviceActivationKeySchemaSqlServerFixture.CreateContext();

        context.ActivationKeys.Add(
            new ActivationKey(Guid.NewGuid().ToString("N"), Guid.NewGuid(), SecretHash));

        var exception = Assert.Throws<DbUpdateException>(() => context.SaveChanges());
        Assert.IsType<SqlException>(exception.InnerException);
    }

    [Fact]
    public void CascadeDelete_RemovesDeviceAndItsActivationKeys_WhenTheBranchIsDeleted()
    {
        using var context = DeviceActivationKeySchemaSqlServerFixture.CreateContext();
        var branch = AddBranch(context);
        var device = AddDevice(context, branch);

        var key = new ActivationKey(Guid.NewGuid().ToString("N"), device.DeviceRecordId, SecretHash);
        context.ActivationKeys.Add(key);
        context.SaveChanges();

        context.Branches.Remove(branch);
        context.SaveChanges();

        using var verifyContext = DeviceActivationKeySchemaSqlServerFixture.CreateContext();

        Assert.False(verifyContext.Devices.Any(d => d.DeviceRecordId == device.DeviceRecordId));
        Assert.False(verifyContext.ActivationKeys.Any(k => k.ActivationKeyId == key.ActivationKeyId));
    }

    [Fact]
    public void Migration_RollsBackToM2_AndReappliesCleanly()
    {
        using var context = DeviceActivationKeySchemaSqlServerFixture.CreateContext();
        var migrator = context.GetInfrastructure().GetRequiredService<IMigrator>();

        // Roll back M3 only — down to M2, not to an empty database.
        migrator.Migrate(BranchCameraSchemaMigration);

        var afterRollback = GetTableNames(context);
        Assert.DoesNotContain("Devices", afterRollback);
        Assert.DoesNotContain("ActivationKeys", afterRollback);

        // M3's Down must drop only its own schema; everything earlier survives untouched.
        Assert.Contains("Branches", afterRollback);
        Assert.Contains("Cameras", afterRollback);
        Assert.Contains("AdminUsers", afterRollback);
        Assert.Contains("AdminSessions", afterRollback);

        migrator.Migrate();

        var afterReapply = GetTableNames(context);
        Assert.Contains("Devices", afterReapply);
        Assert.Contains("ActivationKeys", afterReapply);
        Assert.Contains("Branches", afterReapply);
        Assert.Contains("Cameras", afterReapply);
    }
}

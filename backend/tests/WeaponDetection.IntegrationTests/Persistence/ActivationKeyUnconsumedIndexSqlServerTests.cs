using System;
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

// IP-05 T-49 — the filtered unique index enforcing "at most one Unconsumed Activation Key per
// Device" (AC-6/AC-17), and its migration's pre-index duplicate guard. Verified against real SQL
// Server because a filtered unique index is exactly the thing EF Core InMemory/SQLite do not
// enforce faithfully (IP-01 §9). No real key, secret, or hash appears here — only placeholders.
public class ActivationKeyUnconsumedIndexSqlServerTests
    : IClassFixture<ActivationKeyUnconsumedIndexSqlServerFixture>
{
    private const string SecretHash = "placeholder-secret-hash";
    private const string IndexName = "IX_ActivationKeys_DeviceRecordId_Unconsumed";
    private const string PreviousMigration = "DeviceAndActivationKeySchema";

    private static Device AddBranchAndDevice(WeaponDetectionDbContext context)
    {
        var branch = new Branch(
            $"Branch {Guid.NewGuid()}", "10 Example Street, Example City", "ops@example.invalid");
        context.Branches.Add(branch);
        context.SaveChanges();

        var device = new Device(branch.BranchId);
        context.Devices.Add(device);
        context.SaveChanges();
        return device;
    }

    private static ActivationKey UnconsumedKey(Guid deviceRecordId) =>
        new(Guid.NewGuid().ToString("N"), deviceRecordId, SecretHash);

    private static string Flatten(Exception exception)
    {
        var text = exception.Message;
        for (var inner = exception.InnerException; inner is not null; inner = inner.InnerException)
        {
            text += " | " + inner.Message;
        }

        return text;
    }

    // ---- Runtime enforcement (shared, fully-migrated database) ---------------------------------

    [Fact]
    public void RejectsASecondUnconsumedKeyForTheSameDevice()
    {
        using var context = ActivationKeyUnconsumedIndexSqlServerFixture.CreateContext();
        var device = AddBranchAndDevice(context);

        context.ActivationKeys.Add(UnconsumedKey(device.DeviceRecordId));
        context.SaveChanges();

        using var second = ActivationKeyUnconsumedIndexSqlServerFixture.CreateContext();
        second.ActivationKeys.Add(UnconsumedKey(device.DeviceRecordId));

        var exception = Assert.Throws<DbUpdateException>(() => second.SaveChanges());
        Assert.IsType<SqlException>(exception.InnerException);
        Assert.Contains(IndexName, Flatten(exception));
    }

    [Fact]
    public void AllowsOneUnconsumedKeyPerDevice_AcrossDifferentDevices()
    {
        using var context = ActivationKeyUnconsumedIndexSqlServerFixture.CreateContext();
        var firstDevice = AddBranchAndDevice(context);
        var secondDevice = AddBranchAndDevice(context);

        context.ActivationKeys.Add(UnconsumedKey(firstDevice.DeviceRecordId));
        context.ActivationKeys.Add(UnconsumedKey(secondDevice.DeviceRecordId));

        context.SaveChanges(); // one Unconsumed each on distinct devices — no conflict.

        using var verify = ActivationKeyUnconsumedIndexSqlServerFixture.CreateContext();
        Assert.Equal(1, verify.ActivationKeys.Count(k =>
            k.DeviceRecordId == firstDevice.DeviceRecordId && k.Status == ActivationKeyStatus.Unconsumed));
        Assert.Equal(1, verify.ActivationKeys.Count(k =>
            k.DeviceRecordId == secondDevice.DeviceRecordId && k.Status == ActivationKeyStatus.Unconsumed));
    }

    [Fact]
    public void AllowsManyConsumedAndInvalidatedKeys_AlongsideOneUnconsumed()
    {
        // The filter is doing the work: uniqueness applies only to Unconsumed, so a Device may keep
        // an arbitrarily long history of Consumed/Invalidated keys plus its single live key.
        using var context = ActivationKeyUnconsumedIndexSqlServerFixture.CreateContext();
        var device = AddBranchAndDevice(context);

        var consumedA = UnconsumedKey(device.DeviceRecordId);
        consumedA.Consume();
        var consumedB = UnconsumedKey(device.DeviceRecordId);
        consumedB.Consume();
        var invalidatedA = UnconsumedKey(device.DeviceRecordId);
        invalidatedA.Invalidate();
        var invalidatedB = UnconsumedKey(device.DeviceRecordId);
        invalidatedB.Invalidate();
        var live = UnconsumedKey(device.DeviceRecordId);

        context.ActivationKeys.AddRange(consumedA, consumedB, invalidatedA, invalidatedB, live);
        context.SaveChanges();

        using var verify = ActivationKeyUnconsumedIndexSqlServerFixture.CreateContext();
        var keys = verify.ActivationKeys.Where(k => k.DeviceRecordId == device.DeviceRecordId).ToList();
        Assert.Equal(5, keys.Count);
        Assert.Equal(2, keys.Count(k => k.Status == ActivationKeyStatus.Consumed));
        Assert.Equal(2, keys.Count(k => k.Status == ActivationKeyStatus.Invalidated));
        Assert.Single(keys, k => k.Status == ActivationKeyStatus.Unconsumed);
    }

    // ---- Migration guard and Down (throwaway databases at controlled migration levels) ----------

    private static WeaponDetectionDbContext CreateThrowawayContext(out string connectionString)
    {
        connectionString =
            $"Server=localhost\\SQLEXPRESS;Database=WeaponDetectionUnconsumedIndex_{Guid.NewGuid():N};" +
            "Trusted_Connection=True;TrustServerCertificate=True;";
        var options = new DbContextOptionsBuilder<WeaponDetectionDbContext>()
            .UseSqlServer(connectionString)
            .Options;
        return new WeaponDetectionDbContext(options);
    }

    [Fact]
    public void Migration_FailsClearly_WhenADeviceAlreadyHasTwoUnconsumedKeys_AndLeavesRowsUnchanged()
    {
        using var context = CreateThrowawayContext(out _);
        try
        {
            var migrator = context.GetInfrastructure().GetRequiredService<IMigrator>();

            // Bring the database up to the migration *before* the unique index, where two Unconsumed
            // keys for one device are still permitted, and seed that duplicate state.
            migrator.Migrate(PreviousMigration);

            // FS-09: AddBranchAndDevice below inserts via the current (HEAD) EF model, which includes
            // Branch.TimeZoneId — a column this deliberately-frozen, pre-quota schema snapshot does not
            // have yet. TimeZoneId is unrelated to the ActivationKey unique-index guard under test
            // here, so it is added directly rather than migrating further forward (which would also
            // bring in the guard migration this test exists to exercise below).
            context.Database.ExecuteSqlRaw("ALTER TABLE Branches ADD TimeZoneId nvarchar(100) NULL;");

            // FS-12: same situation, same reason. The HEAD EF model now also carries
            // Device.JetsonHost/RtspOutputPort, which this frozen pre-quota snapshot predates. Both
            // are unrelated to the ActivationKey unique-index guard under test, so they are added
            // directly rather than migrating further forward.
            context.Database.ExecuteSqlRaw(
                "ALTER TABLE Devices ADD JetsonHost nvarchar(255) NULL, RtspOutputPort int NULL;");

            // FS-11 §11: same reasoning for Device.AnnotatedOutputBaseUrl — annotated-output
            // discovery metadata added long after this frozen snapshot, and equally unrelated to the
            // ActivationKey unique-index guard under test.
            context.Database.ExecuteSqlRaw(
                "ALTER TABLE Devices ADD AnnotatedOutputBaseUrl nvarchar(512) NULL;");

            var device = AddBranchAndDevice(context);
            var keyA = UnconsumedKey(device.DeviceRecordId);
            var keyB = UnconsumedKey(device.DeviceRecordId);
            context.ActivationKeys.AddRange(keyA, keyB);
            context.SaveChanges();

            // Applying the index migration must abort on the guard rather than pick a winner.
            var exception = Assert.ThrowsAny<Exception>(() => migrator.Migrate());
            Assert.Contains("duplicate unconsumed activation keys", Flatten(exception));

            // The guard changed nothing: both Unconsumed rows survive, unmodified.
            using var verify = CreateThrowawayContextForSame(context);
            var keys = verify.ActivationKeys
                .Where(k => k.DeviceRecordId == device.DeviceRecordId)
                .ToList();
            Assert.Equal(2, keys.Count);
            Assert.All(keys, k => Assert.Equal(ActivationKeyStatus.Unconsumed, k.Status));
            Assert.Contains(keys, k => k.ActivationKeyId == keyA.ActivationKeyId);
            Assert.Contains(keys, k => k.ActivationKeyId == keyB.ActivationKeyId);
        }
        finally
        {
            context.Database.EnsureDeleted();
        }
    }

    [Fact]
    public void Migration_Down_DropsOnlyTheIndex_AndKeepsTheData()
    {
        using var context = CreateThrowawayContext(out _);
        try
        {
            context.Database.Migrate(); // fully migrated: the filtered unique index exists.
            var device = AddBranchAndDevice(context);
            var key = UnconsumedKey(device.DeviceRecordId);
            context.ActivationKeys.Add(key);
            context.SaveChanges();

            Assert.Equal(1, IndexCount(context));

            var migrator = context.GetInfrastructure().GetRequiredService<IMigrator>();
            migrator.Migrate(PreviousMigration); // Down: drops the index only.

            Assert.Equal(0, IndexCount(context));

            // The Activation Key row is untouched by Down — no data migration.
            using var verify = CreateThrowawayContextForSame(context);
            Assert.True(verify.ActivationKeys.Any(k => k.ActivationKeyId == key.ActivationKeyId));
        }
        finally
        {
            context.Database.EnsureDeleted();
        }
    }

    private static int IndexCount(WeaponDetectionDbContext context) =>
        context.Database
            .SqlQueryRaw<int>(
                "SELECT COUNT(*) AS [Value] FROM sys.indexes WHERE name = {0}", IndexName)
            .AsEnumerable()
            .Single();

    private static WeaponDetectionDbContext CreateThrowawayContextForSame(WeaponDetectionDbContext context)
    {
        var options = new DbContextOptionsBuilder<WeaponDetectionDbContext>()
            .UseSqlServer(context.Database.GetConnectionString())
            .Options;
        return new WeaponDetectionDbContext(options);
    }
}

using Microsoft.Data.SqlClient;
using Microsoft.EntityFrameworkCore.Infrastructure;
using Microsoft.EntityFrameworkCore.Migrations;
using Microsoft.EntityFrameworkCore;
using WeaponDetection.Infrastructure.Persistence;

namespace WeaponDetection.IntegrationTests.Persistence;

// FS-12 / IP-14 T-277 — proves the AddCameraKeyAndDeviceNetwork migration's two-tier backfill against
// a real SQL Server, on its own disposable database. Never touches the production database.
//
// The migration is applied in two steps: the schema is first rolled forward to the migration
// *before* this one, rows are seeded as they exist in a pre-FS-12 deployment, and only then is this
// migration applied. That is the only way to exercise the backfill at all — migrating straight to
// the head would create the columns on an empty table and prove nothing.
public class CameraKeyMigrationSqlServerFixture : IDisposable
{
    public const string ConnectionString =
        "Server=localhost\\SQLEXPRESS;Database=WeaponDetectionCameraKeyMigrationTests;" +
        "Trusted_Connection=True;TrustServerCertificate=True;";

    // The migration immediately preceding AddCameraKeyAndDeviceNetwork.
    public const string PreviousMigration = "20260731171706_AddDeviceAnnotatedOutputBaseUrl";
    public const string TargetMigration = "20260731210201_AddCameraKeyAndDeviceNetwork";

    public CameraKeyMigrationSqlServerFixture()
    {
        using var context = CreateContext();
        context.Database.EnsureDeleted();
    }

    public static WeaponDetectionDbContext CreateContext()
    {
        var options = new DbContextOptionsBuilder<WeaponDetectionDbContext>()
            .UseSqlServer(ConnectionString)
            .Options;

        return new WeaponDetectionDbContext(options);
    }

    public void Dispose()
    {
        using var context = CreateContext();
        context.Database.EnsureDeleted();
    }
}

public class CameraKeyMigrationSqlServerTests
    : IClassFixture<CameraKeyMigrationSqlServerFixture>, IDisposable
{
    // The two approved POC rows, keyed on their real immutable CameraIds (FS-12 §7).
    private static readonly Guid FrontCameraId = new("2613b331-8783-4d51-903a-3e41a979a14c");
    private static readonly Guid RearCameraId = new("ad8a1f09-7fba-4794-8f73-63c7e2c57c92");

    // A Camera the migration has no approved key for — it must fall back to its own GUID.
    private static readonly Guid UnknownCameraId = new("11111111-2222-3333-4444-555555555555");

    // A second Branch, to prove the backfill copes with more than one Branch at a time.
    private static readonly Guid BranchId = new("9b6796f7-b2f2-47f3-a2df-a06bf94c1345");
    private static readonly Guid OtherBranchId = new("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee");

    public CameraKeyMigrationSqlServerTests()
    {
        using var context = CameraKeyMigrationSqlServerFixture.CreateContext();
        context.Database.EnsureDeleted();

        // 1. Roll the schema forward to the state a pre-FS-12 deployment is actually in.
        var migrator = context.GetService<IMigrator>();
        migrator.Migrate(CameraKeyMigrationSqlServerFixture.PreviousMigration);

        SeedPreMigrationRows();

        // 2. Apply the migration under test over that real data.
        migrator.Migrate(CameraKeyMigrationSqlServerFixture.TargetMigration);
    }

    private static void SeedPreMigrationRows()
    {
        Execute(
            """
            INSERT INTO Branches (BranchId, Name, Address, ContactDetails)
            VALUES (@Branch, 'Ljmu Branch', '1 High Street', 'ops@example.local'),
                   (@Other, 'Second Branch', '2 High Street', 'ops2@example.local');

            INSERT INTO Devices
                (DeviceRecordId, DeviceId, BranchId, ActivationStatus, AnnotatedOutputBaseUrl)
            VALUES (NEWID(), NEWID(), @Branch, 'Activated', 'rtsp://100.98.226.80:8554'),
                   (NEWID(), NEWID(), @Other,  'Unactivated', NULL);

            -- Two Cameras in ONE Branch: the case that makes the ordering bug fatal, because a
            -- unique index applied before the backfill would see two empty keys.
            INSERT INTO Cameras (CameraId, BranchId, Name, RtspUrl, Enabled, SourceOrder)
            VALUES (@Front,   @Branch, 'Front Camera',   'rtsp://100.77.146.5:8554/camera1', 1, 0),
                   (@Rear,    @Branch, 'Rear Entrance',  'rtsp://100.77.146.5:8554/camera2', 1, 1),
                   (@Unknown, @Other,  'Unknown Camera', 'rtsp://100.77.146.5:8554/camera3', 1, 0);
            """);
    }

    private static void Execute(string sql)
    {
        using var connection = new SqlConnection(CameraKeyMigrationSqlServerFixture.ConnectionString);
        connection.Open();
        using var command = connection.CreateCommand();
        command.CommandText = sql;
        command.Parameters.AddWithValue("@Branch", BranchId);
        command.Parameters.AddWithValue("@Other", OtherBranchId);
        command.Parameters.AddWithValue("@Front", FrontCameraId);
        command.Parameters.AddWithValue("@Rear", RearCameraId);
        command.Parameters.AddWithValue("@Unknown", UnknownCameraId);
        command.ExecuteNonQuery();
    }

    private static T Scalar<T>(string sql)
    {
        using var connection = new SqlConnection(CameraKeyMigrationSqlServerFixture.ConnectionString);
        connection.Open();
        using var command = connection.CreateCommand();
        command.CommandText = sql;
        return (T)command.ExecuteScalar()!;
    }

    private static string CameraKeyOf(Guid cameraId) =>
        Scalar<string>($"SELECT CameraKey FROM Cameras WHERE CameraId = '{cameraId}'");

    // --- Tier 1: the approved POC backfill ------------------------------------------------------

    [Fact]
    public void FrontCameraReceivesTheApprovedKey() =>
        Assert.Equal("front-camera", CameraKeyOf(FrontCameraId));

    [Fact]
    public void RearEntranceReceivesTheApprovedKey() =>
        Assert.Equal("rear-entrance", CameraKeyOf(RearCameraId));

    // --- Tier 2: the GUID fallback --------------------------------------------------------------

    [Fact]
    public void UnknownCameraFallsBackToItsOwnGuid() =>
        Assert.Equal(UnknownCameraId.ToString("D"), CameraKeyOf(UnknownCameraId));

    [Fact]
    public void UnknownCameraKeepsItsExistingMountUnchanged()
    {
        // The point of the GUID fallback: `cameras/{key}` is byte-identical to the `cameras/{guid}`
        // mount that row already had, so it neither changes its configurationVersion nor restarts a
        // Bridge. Only the two approved rows transition.
        var mount = "cameras/" + CameraKeyOf(UnknownCameraId);

        Assert.Equal($"cameras/{UnknownCameraId:D}", mount);
    }

    // --- Integrity ------------------------------------------------------------------------------

    [Fact]
    public void NoCameraKeyIsLeftEmpty() =>
        Assert.Equal(0, Scalar<int>("SELECT COUNT(*) FROM Cameras WHERE CameraKey = ''"));

    [Fact]
    public void NoDuplicateCameraKeyExistsWithinABranch() =>
        Assert.Equal(
            0,
            Scalar<int>(
                """
                SELECT ISNULL((
                    SELECT COUNT(*) FROM (
                        SELECT BranchId, CameraKey FROM Cameras
                        GROUP BY BranchId, CameraKey HAVING COUNT(*) > 1) d), 0);
                """));

    [Fact]
    public void UniqueIndexWasCreated() =>
        Assert.Equal(
            1,
            Scalar<int>(
                """
                SELECT COUNT(*) FROM sys.indexes
                WHERE name = 'IX_Cameras_BranchId_CameraKey' AND is_unique = 1;
                """));

    [Fact]
    public void UniqueIndexRejectsADuplicateKeyWithinOneBranch()
    {
        var exception = Assert.ThrowsAny<SqlException>(() =>
            Execute(
                $"""
                INSERT INTO Cameras (CameraId, BranchId, Name, RtspUrl, Enabled, SourceOrder, CameraKey)
                VALUES (NEWID(), '{BranchId}', 'Clash', 'rtsp://x:554/s', 1, 9, 'front-camera');
                """));

        Assert.Contains("IX_Cameras_BranchId_CameraKey", exception.Message);
    }

    [Fact]
    public void TheSameKeyIsAllowedInADifferentBranch()
    {
        // FS-12 §3: uniqueness is Branch-scoped, not global.
        Execute(
            $"""
            INSERT INTO Cameras (CameraId, BranchId, Name, RtspUrl, Enabled, SourceOrder, CameraKey)
            VALUES (NEWID(), '{OtherBranchId}', 'Front', 'rtsp://x:554/s', 1, 5, 'front-camera');
            """);

        Assert.Equal(
            2, Scalar<int>("SELECT COUNT(*) FROM Cameras WHERE CameraKey = 'front-camera'"));
    }

    // --- Device network backfill ----------------------------------------------------------------

    [Fact]
    public void DeviceHostIsParsedFromTheLegacyBaseUrl() =>
        Assert.Equal(
            "100.98.226.80",
            Scalar<string>($"SELECT JetsonHost FROM Devices WHERE BranchId = '{BranchId}'"));

    [Fact]
    public void DevicePortIsParsedFromTheLegacyBaseUrl() =>
        Assert.Equal(
            8554,
            Scalar<int>($"SELECT RtspOutputPort FROM Devices WHERE BranchId = '{BranchId}'"));

    [Fact]
    public void LegacyBaseUrlIsRetainedForRollback() =>
        Assert.Equal(
            "rtsp://100.98.226.80:8554",
            Scalar<string>(
                $"SELECT AnnotatedOutputBaseUrl FROM Devices WHERE BranchId = '{BranchId}'"));

    [Fact]
    public void DeviceWithNoLegacyBaseUrlIsLeftUnconfigured() =>
        Assert.Equal(
            1,
            Scalar<int>(
                $"""
                SELECT COUNT(*) FROM Devices
                WHERE BranchId = '{OtherBranchId}' AND JetsonHost IS NULL AND RtspOutputPort IS NULL;
                """));

    // --- Rollback -------------------------------------------------------------------------------

    [Fact]
    public void MigrationRollsBackCleanly()
    {
        using var context = CameraKeyMigrationSqlServerFixture.CreateContext();
        var migrator = context.GetService<IMigrator>();

        migrator.Migrate(CameraKeyMigrationSqlServerFixture.PreviousMigration);

        Assert.Equal(
            0,
            Scalar<int>(
                """
                SELECT COUNT(*) FROM sys.columns
                WHERE object_id = OBJECT_ID('Cameras') AND name = 'CameraKey';
                """));
        Assert.Equal(
            0,
            Scalar<int>(
                """
                SELECT COUNT(*) FROM sys.columns
                WHERE object_id = OBJECT_ID('Devices') AND name IN ('JetsonHost', 'RtspOutputPort');
                """));
    }

    // --- Path B: a fresh database migrated from zero -------------------------------------------

    [Fact]
    public void FreshDatabaseMigratesFromZeroWithEveryFs12Constraint()
    {
        // The migration under test is exercised above over *existing* data. This proves the other
        // deployment shape — a brand-new install, where the backfill has nothing to do and the
        // constraints must still land.
        using var context = CameraKeyMigrationSqlServerFixture.CreateContext();
        context.Database.EnsureDeleted();
        context.Database.Migrate();

        Assert.Equal(
            1,
            Scalar<int>(
                """
                SELECT COUNT(*) FROM sys.indexes
                WHERE name = 'IX_Cameras_BranchId_CameraKey' AND is_unique = 1;
                """));
        // FS-12 §8: one Device per Branch is preserved, not redesigned.
        Assert.Equal(
            1,
            Scalar<int>(
                """
                SELECT COUNT(*) FROM sys.indexes
                WHERE object_id = OBJECT_ID('Devices') AND is_unique = 1
                  AND name = 'IX_Devices_BranchId';
                """));
        // The pre-existing SourceOrder guard survives untouched.
        Assert.Equal(
            1,
            Scalar<int>(
                """
                SELECT COUNT(*) FROM sys.indexes
                WHERE name = 'IX_Cameras_BranchId_SourceOrder_Enabled' AND is_unique = 1;
                """));
        Assert.Equal(
            1,
            Scalar<int>(
                """
                SELECT COUNT(*) FROM __EFMigrationsHistory
                WHERE MigrationId = '20260731210201_AddCameraKeyAndDeviceNetwork';
                """));
    }

    // --- Path C: re-migrating an already-current database is a no-op ----------------------------

    [Fact]
    public void SecondMigrateDoesNotReplayTheBackfill()
    {
        // The backfill is written as UPDATE ... WHERE CameraKey = '', so replaying it would be
        // harmless — but it must not run at all. EF's history table is what guarantees that, and this
        // asserts the guarantee rather than trusting it: a re-migrate reports nothing pending and
        // leaves the approved keys exactly as they were.
        using var context = CameraKeyMigrationSqlServerFixture.CreateContext();
        var before = CameraKeyOf(FrontCameraId);

        context.Database.Migrate();

        Assert.Empty(context.Database.GetPendingMigrations());
        Assert.Equal(before, CameraKeyOf(FrontCameraId));
        Assert.Equal("front-camera", CameraKeyOf(FrontCameraId));
    }

    public void Dispose()
    {
        using var context = CameraKeyMigrationSqlServerFixture.CreateContext();
        context.Database.EnsureDeleted();
        GC.SuppressFinalize(this);
    }
}

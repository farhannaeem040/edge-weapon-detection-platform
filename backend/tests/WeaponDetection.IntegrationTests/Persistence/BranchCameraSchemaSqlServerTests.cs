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

// Verifies the M2 (Branch/Camera) schema's relational behavior against a real SQL Server database.
// EF Core InMemory/SQLite would not faithfully enforce SQL Server's foreign-key and cascade-delete
// behavior, so those providers are deliberately not used here (IP-01 §9).
//
// Every RTSP value below is a non-routable placeholder (.invalid, RFC 2606). No real camera
// address or credential appears in a committed test.
public class BranchCameraSchemaSqlServerTests : IClassFixture<BranchCameraSchemaSqlServerFixture>
{
    private const string RtspUrl = "rtsp://camera.example.invalid:554/stream1";
    private const string InitialAdminSchemaMigration = "InitialAdminSchema";

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

    [Fact]
    public void Migration_CreatesBranchAndCameraTables()
    {
        using var context = BranchCameraSchemaSqlServerFixture.CreateContext();

        var tableNames = GetTableNames(context);

        Assert.Contains("Branches", tableNames);
        Assert.Contains("Cameras", tableNames);
    }

    [Fact]
    public void Migration_LeavesTheAdminSchemaIntact()
    {
        using var context = BranchCameraSchemaSqlServerFixture.CreateContext();

        var tableNames = GetTableNames(context);

        Assert.Contains("AdminUsers", tableNames);
        Assert.Contains("AdminSessions", tableNames);
    }

    [Fact]
    public void Migration_CreatesNoDeviceOrActivationKeyTable()
    {
        // Those tables belong to M3/T-13. Their absence here is what proves T-12 stayed in scope.
        using var context = BranchCameraSchemaSqlServerFixture.CreateContext();

        var tableNames = GetTableNames(context);

        Assert.DoesNotContain("Devices", tableNames);
        Assert.DoesNotContain("ActivationKeys", tableNames);
    }

    [Fact]
    public void Migration_SeedsNoBranchOrCameraData()
    {
        // Migrates a throwaway database of its own: emptiness is only meaningful on a database no
        // other test has written to, so the shared fixture cannot answer this question.
        var connectionString =
            $"Server=localhost\\SQLEXPRESS;Database=WeaponDetectionSeedCheck_{Guid.NewGuid():N};" +
            "Trusted_Connection=True;TrustServerCertificate=True;";

        var options = new DbContextOptionsBuilder<WeaponDetectionDbContext>()
            .UseSqlServer(connectionString)
            .Options;

        using var context = new WeaponDetectionDbContext(options);

        try
        {
            context.Database.Migrate();

            Assert.Empty(context.Branches);
            Assert.Empty(context.Cameras);
        }
        finally
        {
            context.Database.EnsureDeleted();
        }
    }

    [Fact]
    public void Branch_RoundTripsThroughTheDatabase()
    {
        using var context = BranchCameraSchemaSqlServerFixture.CreateContext();
        var branch = AddBranch(context);

        using var verifyContext = BranchCameraSchemaSqlServerFixture.CreateContext();
        var persisted = verifyContext.Branches.Single(b => b.BranchId == branch.BranchId);

        Assert.Equal(branch.Name, persisted.Name);
        Assert.Equal(branch.Address, persisted.Address);
        Assert.Equal(branch.ContactDetails, persisted.ContactDetails);
    }

    [Fact]
    public void Camera_RoundTripsThroughTheDatabase_AndDefaultsToEnabled()
    {
        using var context = BranchCameraSchemaSqlServerFixture.CreateContext();
        var branch = AddBranch(context);

        var camera = new Camera(branch.BranchId, "Entrance Camera", RtspUrl);
        context.Cameras.Add(camera);
        context.SaveChanges();

        using var verifyContext = BranchCameraSchemaSqlServerFixture.CreateContext();
        var persisted = verifyContext.Cameras.Single(c => c.CameraId == camera.CameraId);

        Assert.Equal(branch.BranchId, persisted.BranchId);
        Assert.Equal("Entrance Camera", persisted.Name);
        Assert.Equal(RtspUrl, persisted.RtspUrl);
        Assert.True(persisted.Enabled);
    }

    [Fact]
    public void ForeignKeyConstraint_RejectsACameraWithNoSuchBranch()
    {
        using var context = BranchCameraSchemaSqlServerFixture.CreateContext();

        // A BranchId that corresponds to no Branch row.
        var orphan = new Camera(Guid.NewGuid(), "Orphan Camera", RtspUrl);
        context.Cameras.Add(orphan);

        var exception = Assert.Throws<DbUpdateException>(() => context.SaveChanges());
        Assert.IsType<SqlException>(exception.InnerException);
    }

    [Fact]
    public void Branch_CanOwnMoreThanOneCamera()
    {
        // The cardinality test that matters: ARCH-001 §13.1 and FS-02 §9/§12 define a Branch as
        // owning *one or more* Cameras. A unique index on Camera.BranchId would make this throw.
        using var context = BranchCameraSchemaSqlServerFixture.CreateContext();
        var branch = AddBranch(context);

        context.Cameras.Add(new Camera(branch.BranchId, "Entrance Camera", RtspUrl));
        context.Cameras.Add(new Camera(branch.BranchId, "Till Camera", "rtsp://camera.example.invalid:554/stream2"));
        context.Cameras.Add(new Camera(branch.BranchId, "Stockroom Camera", "rtsp://camera.example.invalid:554/stream3"));

        context.SaveChanges();

        using var verifyContext = BranchCameraSchemaSqlServerFixture.CreateContext();
        var cameras = verifyContext.Cameras.Where(c => c.BranchId == branch.BranchId).ToList();

        Assert.Equal(3, cameras.Count);
    }

    [Fact]
    public void CascadeDelete_RemovesCameras_WhenTheirBranchIsDeleted()
    {
        using var context = BranchCameraSchemaSqlServerFixture.CreateContext();
        var branch = AddBranch(context);

        var camera = new Camera(branch.BranchId, "Entrance Camera", RtspUrl);
        context.Cameras.Add(camera);
        context.SaveChanges();

        context.Branches.Remove(branch);
        context.SaveChanges();

        using var verifyContext = BranchCameraSchemaSqlServerFixture.CreateContext();
        Assert.False(verifyContext.Cameras.Any(c => c.CameraId == camera.CameraId));
    }

    [Fact]
    public void Migration_RollsBackToM1_AndReappliesCleanly()
    {
        using var context = BranchCameraSchemaSqlServerFixture.CreateContext();
        var migrator = context.GetInfrastructure().GetRequiredService<IMigrator>();

        // Roll back M2 only — down to M1, not to an empty database.
        migrator.Migrate(InitialAdminSchemaMigration);

        var afterRollback = GetTableNames(context);
        Assert.DoesNotContain("Branches", afterRollback);
        Assert.DoesNotContain("Cameras", afterRollback);

        // M2's Down must drop only its own schema; the Admin tables from M1 survive untouched.
        Assert.Contains("AdminUsers", afterRollback);
        Assert.Contains("AdminSessions", afterRollback);

        migrator.Migrate();

        var afterReapply = GetTableNames(context);
        Assert.Contains("Branches", afterReapply);
        Assert.Contains("Cameras", afterReapply);
        Assert.Contains("AdminUsers", afterReapply);
        Assert.Contains("AdminSessions", afterReapply);
    }
}

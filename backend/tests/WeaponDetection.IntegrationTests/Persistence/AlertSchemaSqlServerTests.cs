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

// Verifies the AddAlertSchema migration's relational behavior against a real SQL Server database
// (FS-06 §5.1/§5.2, IP-08 T-95/T-102, OI-12) — the unique (DeviceId, EventId) index is the
// idempotency/concurrency authority, so its actual enforcement by SQL Server (not merely believed
// from the model) is exactly what this class exists to prove.
public class AlertSchemaSqlServerTests : IClassFixture<AlertSchemaSqlServerFixture>
{
    private const string PreviousMigration = "ActivationKeyUnconsumedUniqueIndex";
    private const string RtspUrl = "rtsp://camera.example.invalid:554/stream1";

    private static List<string> GetTableNames(WeaponDetectionDbContext context) =>
        context.Database
            .SqlQueryRaw<string>(
                "SELECT TABLE_NAME AS [Value] FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE = 'BASE TABLE'")
            .ToList();

    private static async Task<(Branch Branch, Camera Camera)> SeedBranchAndCameraAsync(WeaponDetectionDbContext context)
    {
        var branch = new Branch($"Branch {Guid.NewGuid()}", "10 Example Street", "ops@example.invalid");
        var camera = new Camera(branch.BranchId, "camera1", RtspUrl, $"cam-{Guid.NewGuid():N}");
        context.Branches.Add(branch);
        context.Cameras.Add(camera);
        await context.SaveChangesAsync();
        return (branch, camera);
    }

    private static Alert MakeAlert(Guid deviceId, Guid eventId, Guid cameraId) =>
        new(
            deviceId,
            eventId,
            cameraId,
            detectedAtUtc: new DateTime(2026, 7, 24, 18, 30, 0, DateTimeKind.Utc),
            receivedAtUtc: DateTime.UtcNow,
            classId: 0,
            className: "gun",
            confidence: 0.91,
            frameNumber: 12345,
            frameWidth: 1280,
            frameHeight: 720,
            bboxLeft: 420.0,
            bboxTop: 180.0,
            bboxWidth: 250.0,
            bboxHeight: 190.0);

    [Fact]
    public void Migration_CreatesTheAlertsTable()
    {
        using var context = AlertSchemaSqlServerFixture.CreateContext();

        Assert.Contains("Alerts", GetTableNames(context));
    }

    [Fact]
    public void Migration_LeavesExistingSchemaIntact()
    {
        using var context = AlertSchemaSqlServerFixture.CreateContext();

        var tableNames = GetTableNames(context);
        Assert.Contains("Branches", tableNames);
        Assert.Contains("Cameras", tableNames);
        Assert.Contains("Devices", tableNames);
        Assert.Contains("ActivationKeys", tableNames);
    }

    [Fact]
    public async Task Alert_RoundTripsThroughTheDatabase()
    {
        using var context = AlertSchemaSqlServerFixture.CreateContext();
        var (_, camera) = await SeedBranchAndCameraAsync(context);
        var deviceId = Guid.NewGuid();
        var eventId = Guid.NewGuid();
        var alert = MakeAlert(deviceId, eventId, camera.CameraId);

        context.Alerts.Add(alert);
        await context.SaveChangesAsync();

        using var verify = AlertSchemaSqlServerFixture.CreateContext();
        var persisted = await verify.Alerts.SingleAsync(a => a.AlertId == alert.AlertId);
        Assert.Equal(deviceId, persisted.DeviceId);
        Assert.Equal(eventId, persisted.EventId);
        Assert.Equal(camera.CameraId, persisted.CameraId);
        Assert.Equal(AlertStatus.New, persisted.Status);
        Assert.Null(persisted.SnapshotReference);
    }

    [Fact]
    public async Task UniqueIndex_RejectsASecondAlertWithTheSameDeviceIdAndEventId()
    {
        using var context = AlertSchemaSqlServerFixture.CreateContext();
        var (_, camera) = await SeedBranchAndCameraAsync(context);
        var deviceId = Guid.NewGuid();
        var eventId = Guid.NewGuid();

        context.Alerts.Add(MakeAlert(deviceId, eventId, camera.CameraId));
        await context.SaveChangesAsync();

        using var second = AlertSchemaSqlServerFixture.CreateContext();
        second.Alerts.Add(MakeAlert(deviceId, eventId, camera.CameraId));

        var exception = await Assert.ThrowsAsync<DbUpdateException>(() => second.SaveChangesAsync());
        Assert.IsType<SqlException>(exception.InnerException);
        Assert.True(exception.InnerException is SqlException { Number: 2601 or 2627 });
    }

    [Fact]
    public async Task UniqueIndex_AllowsTheSameEventIdForDifferentDevices()
    {
        // The uniqueness is on the (DeviceId, EventId) pair, not EventId alone — two different
        // devices could (in principle) mint colliding EventId values.
        using var context = AlertSchemaSqlServerFixture.CreateContext();
        var (_, camera) = await SeedBranchAndCameraAsync(context);
        var eventId = Guid.NewGuid();

        context.Alerts.Add(MakeAlert(Guid.NewGuid(), eventId, camera.CameraId));
        context.Alerts.Add(MakeAlert(Guid.NewGuid(), eventId, camera.CameraId));
        await context.SaveChangesAsync();

        var count = await context.Alerts.CountAsync(a => a.EventId == eventId);
        Assert.Equal(2, count);
    }

    [Fact]
    public async Task UniqueIndex_AllowsDifferentEventIdsForTheSameDevice()
    {
        using var context = AlertSchemaSqlServerFixture.CreateContext();
        var (_, camera) = await SeedBranchAndCameraAsync(context);
        var deviceId = Guid.NewGuid();

        context.Alerts.Add(MakeAlert(deviceId, Guid.NewGuid(), camera.CameraId));
        context.Alerts.Add(MakeAlert(deviceId, Guid.NewGuid(), camera.CameraId));
        await context.SaveChangesAsync();

        var count = await context.Alerts.CountAsync(a => a.DeviceId == deviceId);
        Assert.Equal(2, count);
    }

    [Fact]
    public void Migration_RollsBackToThePreviousMigration_AndReappliesCleanly()
    {
        using var context = AlertSchemaSqlServerFixture.CreateContext();
        var migrator = context.GetInfrastructure().GetRequiredService<IMigrator>();

        migrator.Migrate(PreviousMigration);

        var afterRollback = GetTableNames(context);
        Assert.DoesNotContain("Alerts", afterRollback);
        Assert.Contains("Devices", afterRollback);
        Assert.Contains("ActivationKeys", afterRollback);

        migrator.Migrate();

        var afterReapply = GetTableNames(context);
        Assert.Contains("Alerts", afterReapply);
    }
}

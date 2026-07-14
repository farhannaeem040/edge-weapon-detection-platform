using System;
using System.Linq;
using Microsoft.Data.SqlClient;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Infrastructure;
using Microsoft.EntityFrameworkCore.Migrations;
using Microsoft.Extensions.DependencyInjection;
using WeaponDetection.Domain;
using Xunit;

namespace WeaponDetection.IntegrationTests.Persistence;

// Verifies relational/transactional behavior against a real SQL Server database — EF Core
// InMemory/SQLite would not faithfully enforce SQL Server's unique-index and foreign-key
// behavior, so those providers are deliberately not used here (IP-01 §9).
public class AdminSchemaSqlServerTests : IClassFixture<AdminSchemaSqlServerFixture>
{
    [Fact]
    public void Migration_CreatesExpectedTables()
    {
        using var context = AdminSchemaSqlServerFixture.CreateContext();

        var tableNames = context.Database
            .SqlQueryRaw<string>("SELECT TABLE_NAME AS [Value] FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE = 'BASE TABLE'")
            .ToList();

        Assert.Contains("AdminUsers", tableNames);
        Assert.Contains("AdminSessions", tableNames);
    }

    [Fact]
    public void UniqueCredentialIdentifierConstraint_IsEnforced()
    {
        using var context = AdminSchemaSqlServerFixture.CreateContext();
        var credentialIdentifier = $"admin-{Guid.NewGuid()}";

        context.AdminUsers.Add(new AdminUser(credentialIdentifier, "hash-1"));
        context.SaveChanges();

        context.AdminUsers.Add(new AdminUser(credentialIdentifier, "hash-2"));

        var exception = Assert.Throws<DbUpdateException>(() => context.SaveChanges());
        Assert.IsType<SqlException>(exception.InnerException);
    }

    [Fact]
    public void ForeignKeyConstraint_IsEnforced_ForNonExistentUser()
    {
        using var context = AdminSchemaSqlServerFixture.CreateContext();

        var orphanSession = new AdminSession(
            Guid.NewGuid(),
            Guid.NewGuid(), // does not correspond to any AdminUser row
            DateTimeOffset.UtcNow,
            DateTimeOffset.UtcNow.AddHours(1));

        context.AdminSessions.Add(orphanSession);

        var exception = Assert.Throws<DbUpdateException>(() => context.SaveChanges());
        Assert.IsType<SqlException>(exception.InnerException);
    }

    [Fact]
    public void CascadeDelete_RemovesSessions_WhenUserIsDeleted()
    {
        using var context = AdminSchemaSqlServerFixture.CreateContext();
        var user = new AdminUser($"cascade-{Guid.NewGuid()}", "hash");
        context.AdminUsers.Add(user);
        context.SaveChanges();

        var session = new AdminSession(Guid.NewGuid(), user.UserId, DateTimeOffset.UtcNow, DateTimeOffset.UtcNow.AddHours(1));
        context.AdminSessions.Add(session);
        context.SaveChanges();

        context.AdminUsers.Remove(user);
        context.SaveChanges();

        using var verifyContext = AdminSchemaSqlServerFixture.CreateContext();
        Assert.False(verifyContext.AdminSessions.Any(s => s.SessionId == session.SessionId));
    }

    [Fact]
    public void Migration_CanBeRolledBackAndReappliedCleanly()
    {
        using var context = AdminSchemaSqlServerFixture.CreateContext();
        var migrator = context.GetInfrastructure().GetRequiredService<IMigrator>();

        migrator.Migrate("0");

        var tablesAfterRollback = context.Database
            .SqlQueryRaw<string>("SELECT TABLE_NAME AS [Value] FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE = 'BASE TABLE'")
            .ToList();
        Assert.DoesNotContain("AdminUsers", tablesAfterRollback);
        Assert.DoesNotContain("AdminSessions", tablesAfterRollback);

        migrator.Migrate();

        var tablesAfterReapply = context.Database
            .SqlQueryRaw<string>("SELECT TABLE_NAME AS [Value] FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE = 'BASE TABLE'")
            .ToList();
        Assert.Contains("AdminUsers", tablesAfterReapply);
        Assert.Contains("AdminSessions", tablesAfterReapply);
    }
}

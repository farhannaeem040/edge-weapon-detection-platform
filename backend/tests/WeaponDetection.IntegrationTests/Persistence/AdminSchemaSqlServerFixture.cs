using Microsoft.EntityFrameworkCore;
using WeaponDetection.Infrastructure.Persistence;

namespace WeaponDetection.IntegrationTests.Persistence;

// Applies the real InitialAdminSchema migration to a dedicated local SQL Server database,
// distinct from the developer's WeaponDetectionDev database, so these tests never touch
// data a developer is working with interactively. Requires the local SQLEXPRESS instance
// (or another reachable SQL Server) described in README.md.
public class AdminSchemaSqlServerFixture : IDisposable
{
    public const string ConnectionString =
        "Server=localhost\\SQLEXPRESS;Database=WeaponDetectionSchemaTests;Trusted_Connection=True;TrustServerCertificate=True;";

    public AdminSchemaSqlServerFixture()
    {
        using var context = CreateContext();
        context.Database.Migrate();
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

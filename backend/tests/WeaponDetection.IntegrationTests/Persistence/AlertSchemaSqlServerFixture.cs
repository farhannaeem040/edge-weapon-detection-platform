using Microsoft.EntityFrameworkCore;
using WeaponDetection.Infrastructure.Persistence;

namespace WeaponDetection.IntegrationTests.Persistence;

// Applies every migration through AddAlertSchema (IP-08 T-95) to a dedicated local SQL Server
// database, distinct from every other fixture's — this class's tests roll the schema back and
// forward again. Requires the local SQLEXPRESS instance described in README.md.
public class AlertSchemaSqlServerFixture : IDisposable
{
    public const string ConnectionString =
        "Server=localhost\\SQLEXPRESS;Database=WeaponDetectionAlertSchemaTests;" +
        "Trusted_Connection=True;TrustServerCertificate=True;";

    public AlertSchemaSqlServerFixture()
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

using Microsoft.EntityFrameworkCore;
using WeaponDetection.Infrastructure.Persistence;

namespace WeaponDetection.IntegrationTests.Persistence;

// A dedicated, fully-migrated SQL Server database for the IP-05 T-49 unique-index tests, on its own
// database name — distinct from every other fixture's. This matters because xUnit runs different
// test classes in parallel and this fixture (like DeviceActivationKeySchemaSqlServerFixture) deletes
// its database on Dispose; sharing a database name across classes would race and drop it mid-use.
// Requires the local SQLEXPRESS instance described in README.md.
public class ActivationKeyUnconsumedIndexSqlServerFixture : IDisposable
{
    public const string ConnectionString =
        "Server=localhost\\SQLEXPRESS;Database=WeaponDetectionActivationKeyUnconsumedIndexTests;" +
        "Trusted_Connection=True;TrustServerCertificate=True;";

    public ActivationKeyUnconsumedIndexSqlServerFixture()
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

using Microsoft.EntityFrameworkCore;
using WeaponDetection.Infrastructure.Persistence;

namespace WeaponDetection.IntegrationTests.Persistence;

// Applies the real migrations (M1 InitialAdminSchema, M2 BranchCameraSchema, M3
// DeviceAndActivationKeySchema) to a dedicated local SQL Server database, distinct from the
// developer's WeaponDetectionDev database and from every other fixture's — the tests in this class
// roll the schema back and forward again, which must never happen against a database another test
// class is using concurrently. Requires the local SQLEXPRESS instance described in README.md.
public class DeviceActivationKeySchemaSqlServerFixture : IDisposable
{
    public const string ConnectionString =
        "Server=localhost\\SQLEXPRESS;Database=WeaponDetectionDeviceActivationKeySchemaTests;" +
        "Trusted_Connection=True;TrustServerCertificate=True;";

    public DeviceActivationKeySchemaSqlServerFixture()
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

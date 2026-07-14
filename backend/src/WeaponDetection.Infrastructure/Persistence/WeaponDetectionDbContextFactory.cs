using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Design;

namespace WeaponDetection.Infrastructure.Persistence;

// Used only by EF Core CLI tooling (e.g. `dotnet ef migrations add`, `dotnet ef dbcontext info`)
// to construct the DbContext outside of a running application. Never used at runtime — the API
// registers the real DbContext via AddInfrastructure using ConnectionStrings:DefaultConnection.
//
// The connection string below is a design-time-only placeholder (Windows/integrated
// authentication, no password) and is never used to open a real connection during CLI discovery
// or migration scaffolding; it may be overridden locally via EFCORE_DESIGNTIME_CONNECTION.
public class WeaponDetectionDbContextFactory : IDesignTimeDbContextFactory<WeaponDetectionDbContext>
{
    private const string DesignTimeConnectionString =
        "Server=localhost;Database=WeaponDetectionDesignTime;Trusted_Connection=True;TrustServerCertificate=True;";

    public WeaponDetectionDbContext CreateDbContext(string[] args)
    {
        var connectionString =
            Environment.GetEnvironmentVariable("EFCORE_DESIGNTIME_CONNECTION")
            ?? DesignTimeConnectionString;

        var optionsBuilder = new DbContextOptionsBuilder<WeaponDetectionDbContext>();
        optionsBuilder.UseSqlServer(connectionString);

        return new WeaponDetectionDbContext(optionsBuilder.Options);
    }
}

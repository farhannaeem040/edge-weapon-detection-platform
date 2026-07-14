using Microsoft.EntityFrameworkCore;
using WeaponDetection.Domain;

namespace WeaponDetection.Infrastructure.Persistence;

// Device/ActivationKey (T-13) are added, together with migration M3, in a later task.
public class WeaponDetectionDbContext : DbContext
{
    public WeaponDetectionDbContext(DbContextOptions<WeaponDetectionDbContext> options)
        : base(options)
    {
    }

    public DbSet<AdminUser> AdminUsers => Set<AdminUser>();
    public DbSet<AdminSession> AdminSessions => Set<AdminSession>();
    public DbSet<Branch> Branches => Set<Branch>();
    public DbSet<Camera> Cameras => Set<Camera>();

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        modelBuilder.ApplyConfigurationsFromAssembly(typeof(WeaponDetectionDbContext).Assembly);
    }
}

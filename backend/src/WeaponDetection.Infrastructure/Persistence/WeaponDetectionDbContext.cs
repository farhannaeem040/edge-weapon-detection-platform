using Microsoft.EntityFrameworkCore;
using WeaponDetection.Domain;

namespace WeaponDetection.Infrastructure.Persistence;

// Branch/Camera (T-12) and Device/ActivationKey (T-13) are added, together with their own
// migrations, in later tasks.
public class WeaponDetectionDbContext : DbContext
{
    public WeaponDetectionDbContext(DbContextOptions<WeaponDetectionDbContext> options)
        : base(options)
    {
    }

    public DbSet<AdminUser> AdminUsers => Set<AdminUser>();
    public DbSet<AdminSession> AdminSessions => Set<AdminSession>();

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        modelBuilder.ApplyConfigurationsFromAssembly(typeof(WeaponDetectionDbContext).Assembly);
    }
}

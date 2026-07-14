using Microsoft.EntityFrameworkCore;
using WeaponDetection.Domain;

namespace WeaponDetection.Infrastructure.Persistence;

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
    public DbSet<Device> Devices => Set<Device>();
    public DbSet<ActivationKey> ActivationKeys => Set<ActivationKey>();

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        modelBuilder.ApplyConfigurationsFromAssembly(typeof(WeaponDetectionDbContext).Assembly);
    }
}

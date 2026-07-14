using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Metadata.Builders;
using WeaponDetection.Domain;

namespace WeaponDetection.Infrastructure.Persistence.Configurations;

public class AdminSessionConfiguration : IEntityTypeConfiguration<AdminSession>
{
    public void Configure(EntityTypeBuilder<AdminSession> builder)
    {
        builder.ToTable("AdminSessions");

        builder.HasKey(s => s.SessionId);

        builder.Property(s => s.UserId)
            .IsRequired();

        builder.Property(s => s.IssuedAt)
            .IsRequired();

        builder.Property(s => s.ExpiresAt)
            .IsRequired();

        builder.Property(s => s.Revoked)
            .IsRequired()
            .HasDefaultValue(false);

        // Explicit index on the FK column for session-by-user lookups. No additional
        // cleanup/pruning index (e.g. on ExpiresAt) is added: FS-01 §19 explicitly defers the
        // exact AdminSession cleanup strategy, and no current feature queries by expiry, so an
        // unused index would be speculative.
        builder.HasIndex(s => s.UserId);

        // Cascade delete: an AdminSession has no meaning once its AdminUser is gone, and this
        // prototype has no soft-delete concept for AdminUser (single account, BR-001).
        builder.HasOne<AdminUser>()
            .WithMany()
            .HasForeignKey(s => s.UserId)
            .IsRequired()
            .OnDelete(DeleteBehavior.Cascade);
    }
}

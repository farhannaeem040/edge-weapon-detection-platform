using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Metadata.Builders;
using WeaponDetection.Domain;

namespace WeaponDetection.Infrastructure.Persistence.Configurations;

public class AdminUserConfiguration : IEntityTypeConfiguration<AdminUser>
{
    public void Configure(EntityTypeBuilder<AdminUser> builder)
    {
        builder.ToTable("AdminUsers");

        builder.HasKey(u => u.UserId);

        builder.Property(u => u.CredentialIdentifier)
            .IsRequired()
            .HasMaxLength(256);

        // Uniqueness relies on SQL Server's default case-insensitive collation
        // (SQL_Latin1_General_CP1_CI_AS) rather than a separate normalized column — the
        // CredentialIdentifier value itself is only trimmed at construction (AdminUser
        // constructor). A dedicated normalized-value column was deliberately not introduced,
        // since FS-01/IP-01 do not require one and it would be unused speculative complexity
        // for a single-Admin prototype.
        builder.HasIndex(u => u.CredentialIdentifier)
            .IsUnique();

        builder.Property(u => u.PasswordHash)
            .IsRequired()
            .HasMaxLength(512);
    }
}

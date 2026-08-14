using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Metadata.Builders;
using WeaponDetection.Domain;

namespace WeaponDetection.Infrastructure.Persistence.Configurations;

public class BranchDailyAlertQuotaConfiguration : IEntityTypeConfiguration<BranchDailyAlertQuota>
{
    // The composite primary key's constraint name (explicit, not EF's default) — AlertSyncService
    // matches on this name to distinguish "another request just inserted this Branch/day's row" from
    // an unrelated persistence failure, the same way AlertConfiguration.DeviceIdEventIdUniqueIndexName
    // is used for Alert's own insert-if-absent race handling (FS-09 §7 step 4a).
    public const string BranchIdLocalDatePrimaryKeyName = "PK_BranchDailyAlertQuotas";

    public void Configure(EntityTypeBuilder<BranchDailyAlertQuota> builder)
    {
        builder.ToTable("BranchDailyAlertQuotas");

        // (BranchId, LocalDate) is both the unique key and the composite primary key — there is no
        // separate surrogate id, since the Branch/day pair is the entity's entire identity (FS-09
        // §6.1).
        builder.HasKey(q => new { q.BranchId, q.LocalDate })
            .HasName(BranchIdLocalDatePrimaryKeyName);

        builder.Property(q => q.LocalDate)
            .IsRequired()
            .HasMaxLength(BranchDailyAlertQuota.LocalDateLength);

        builder.Property(q => q.AcceptedAlertCount)
            .IsRequired();

        builder.Property(q => q.SuppressedDetectionCount)
            .IsRequired();

        builder.Property(q => q.GunSuppressedCount)
            .IsRequired();

        builder.Property(q => q.KnifeSuppressedCount)
            .IsRequired();

        builder.Property(q => q.FirstSuppressedAtUtc)
            .IsRequired(false);

        builder.Property(q => q.LastSuppressedAtUtc)
            .IsRequired(false);

        // No FK to Branch is declared, mirroring Alert's "no FK, index only" style (FS-06 §5.1) — a
        // quota row is deliberately independent of Branch mutation.
    }
}

using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Metadata.Builders;
using WeaponDetection.Domain;

namespace WeaponDetection.Infrastructure.Persistence.Configurations;

public class SuppressedDetectionEventConfiguration : IEntityTypeConfiguration<SuppressedDetectionEvent>
{
    // (DeviceId, EventId) is the idempotency authority for suppression retries (FS-09 §6.2/§7 step 3),
    // mirroring AlertConfiguration.DeviceIdEventIdUniqueIndexName — it is the composite primary key
    // here since a SuppressedDetectionEvent's entire identity is that pair, unlike Alert which has its
    // own surrogate AlertId.
    public const string DeviceIdEventIdPrimaryKeyName = "PK_SuppressedDetectionEvents";

    public void Configure(EntityTypeBuilder<SuppressedDetectionEvent> builder)
    {
        builder.ToTable("SuppressedDetectionEvents");

        builder.HasKey(s => new { s.DeviceId, s.EventId })
            .HasName(DeviceIdEventIdPrimaryKeyName);

        builder.Property(s => s.BranchId)
            .IsRequired();

        builder.Property(s => s.LocalDate)
            .IsRequired()
            .HasMaxLength(SuppressedDetectionEvent.LocalDateLength);

        builder.Property(s => s.ClassName)
            .IsRequired()
            .HasMaxLength(SuppressedDetectionEvent.ClassNameMaxLength);

        builder.Property(s => s.DetectedAtUtc)
            .IsRequired();

        builder.Property(s => s.Reason)
            .IsRequired()
            .HasMaxLength(SuppressedDetectionEvent.ReasonMaxLength);

        builder.Property(s => s.CreatedAtUtc)
            .IsRequired();

        // Serves "suppressions for this Branch/day" lookups (e.g. the periodic summary log, FS-09
        // §13) without a full table scan; not itself an idempotency guard (the primary key above is).
        builder.HasIndex(s => new { s.BranchId, s.LocalDate });

        // No FK to Device/Branch, mirroring Alert's "no FK, index only" style (FS-06 §5.1).
    }
}

using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Metadata.Builders;
using WeaponDetection.Domain;

namespace WeaponDetection.Infrastructure.Persistence.Configurations;

public class AlertConfiguration : IEntityTypeConfiguration<Alert>
{
    public const string DeviceIdEventIdUniqueIndexName = "IX_Alerts_DeviceId_EventId";

    public void Configure(EntityTypeBuilder<Alert> builder)
    {
        builder.ToTable("Alerts");

        builder.HasKey(a => a.AlertId);

        builder.Property(a => a.DeviceId)
            .IsRequired();

        builder.Property(a => a.EventId)
            .IsRequired();

        // The idempotency/concurrency authority (FS-06 §5.1/§5.2, ADR-012). AlertSyncService treats a
        // violation of this exact index as "this event was already delivered", never as an unrelated
        // persistence failure — mirroring how DeviceService pins its own conflict detection to a named
        // index rather than any DbUpdateException.
        builder.HasIndex(a => new { a.DeviceId, a.EventId })
            .IsUnique()
            .HasDatabaseName(DeviceIdEventIdUniqueIndexName);

        builder.Property(a => a.CameraId)
            .IsRequired();

        builder.Property(a => a.DetectedAtUtc)
            .IsRequired();

        builder.Property(a => a.ReceivedAtUtc)
            .IsRequired();

        builder.Property(a => a.ClassId)
            .IsRequired();

        builder.Property(a => a.ClassName)
            .IsRequired()
            .HasMaxLength(Alert.ClassNameMaxLength);

        builder.Property(a => a.Confidence)
            .IsRequired();

        builder.Property(a => a.FrameNumber)
            .IsRequired();

        builder.Property(a => a.FrameWidth)
            .IsRequired();

        builder.Property(a => a.FrameHeight)
            .IsRequired();

        builder.Property(a => a.BboxLeft)
            .IsRequired();

        builder.Property(a => a.BboxTop)
            .IsRequired();

        builder.Property(a => a.BboxWidth)
            .IsRequired();

        builder.Property(a => a.BboxHeight)
            .IsRequired();

        // Null until FS-08's upload endpoint calls Alert.AttachSnapshot; every existing row (594 in
        // production as of FS-08's start) keeps SnapshotReference=NULL, unaffected by this migration.
        builder.Property(a => a.SnapshotReference)
            .IsRequired(false)
            .HasMaxLength(Alert.SnapshotReferenceMaxLength);

        // FS-08 §10: additive, nullable-only fields set together by AttachSnapshot. No backfill —
        // every pre-existing Alert row has all four columns NULL.
        builder.Property(a => a.SnapshotSha256)
            .IsRequired(false)
            .HasMaxLength(Alert.SnapshotSha256Length);

        builder.Property(a => a.SnapshotContentType)
            .IsRequired(false)
            .HasMaxLength(Alert.SnapshotContentTypeMaxLength);

        builder.Property(a => a.SnapshotSizeBytes)
            .IsRequired(false);

        builder.Property(a => a.SnapshotReceivedAtUtc)
            .IsRequired(false);

        // Stored as its name, mirroring Device.ActivationStatus — readable in the database and
        // immune to a future reordering of the enum members.
        builder.Property(a => a.Status)
            .IsRequired()
            .HasConversion<string>()
            .HasMaxLength(32);

        // Serves "the alerts for this device" / camera-scoped lookups; not itself the idempotency
        // guard (the composite unique index above is).
        builder.HasIndex(a => a.DeviceId);
        builder.HasIndex(a => a.CameraId);

        // No FK to Device/Camera is declared: DeviceId here is the external Device.DeviceId (not a
        // primary-key reference to Devices, whose key is DeviceRecordId), and an Alert's Camera/Device
        // row may be edited or removed independently without the historical Alert becoming invalid
        // (there is no cascade requirement in FS-06 for this increment).
    }
}

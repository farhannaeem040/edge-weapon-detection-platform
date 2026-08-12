using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Metadata.Builders;
using WeaponDetection.Domain;

namespace WeaponDetection.Infrastructure.Persistence.Configurations;

public class DeviceConfiguration : IEntityTypeConfiguration<Device>
{
    public void Configure(EntityTypeBuilder<Device> builder)
    {
        builder.ToTable("Devices");

        // The internal key (FS-02 §1.3). Never exposed; the external identity is DeviceId, below.
        builder.HasKey(d => d.DeviceRecordId);

        builder.Property(d => d.DeviceId)
            .IsRequired(false);

        // The constraint that makes AC-7 ("assigned exactly once") enforceable at the database and
        // not merely in the entity — but *filtered*, and that word is doing real work here. Every
        // Device is created with DeviceId = NULL and stays that way until it activates, so a plain
        // unique index would let the first unactivated device exist and reject the second, because
        // SQL Server treats NULLs as equal for uniqueness. The filter excludes the NULLs from the
        // index entirely: uniqueness is enforced only among devices that have actually activated.
        builder.HasIndex(d => d.DeviceId)
            .IsUnique()
            .HasFilter("[DeviceId] IS NOT NULL");

        builder.Property(d => d.BranchId)
            .IsRequired();

        // Exactly one Device per Branch (BR-002, CON-007, IP-01 §4) — enforced by the schema, not
        // by a service-layer check, so no code path can create a second one. This is the deliberate
        // contrast with Camera.BranchId's non-unique index (a Branch owns one or more Cameras).
        builder.HasIndex(d => d.BranchId)
            .IsUnique();

        // Stored as its name, not its underlying int. The column stays readable in the database and
        // survives any future reordering of the enum members, which an int column would silently
        // reinterpret.
        builder.Property(d => d.ActivationStatus)
            .IsRequired()
            .HasConversion<string>()
            .HasMaxLength(32);

        // Protected (encrypted) form only — never the plaintext shared secret (FS-02 §11,
        // ARCH-001 §13.3). NULL until the device first activates.
        builder.Property(d => d.ProtectedSharedSecret)
            .IsRequired(false)
            .HasMaxLength(Device.ProtectedSharedSecretMaxLength);

        // NULL until first operational contact (FS-02 §9). Nothing in IP-01 writes it; the
        // mechanism and timing are deferred by FS-02 §19 to the later feature that realizes
        // ASM-008. The column exists now because M3 is where the Device table is created.
        builder.Property(d => d.LastKnownAddress)
            .IsRequired(false)
            .HasMaxLength(Device.LastKnownAddressMaxLength);

        // FS-11 §11 — the Device's advertised annotated-output RTSP base. Discovery metadata only,
        // NULL until an Admin configures it, and never a credential (Device.SetAnnotatedOutputBaseUrl
        // rejects any base carrying user info).
        // FS-12 §5 (Option A — transitional): retained as a read-fallback only. New writes go to
        // JetsonHost/RtspOutputPort below; this column stays populated so a rollback to the previous
        // build keeps composing working URLs. A later feature drops it.
        builder.Property(d => d.AnnotatedOutputBaseUrl)
            .IsRequired(false)
            .HasMaxLength(Device.AnnotatedOutputBaseUrlMaxLength);

        // FS-12 §4 — the structured, authoritative network configuration. Nullable because a Device
        // is created reserved, before its Jetson's reachable address is known. Never named for
        // Tailscale: the POC's Tailscale IP is just one of the values this column legitimately holds.
        builder.Property(d => d.JetsonHost)
            .IsRequired(false)
            .HasMaxLength(Device.JetsonHostMaxLength);

        builder.Property(d => d.RtspOutputPort)
            .IsRequired(false);

        // Cascade, mirroring Camera: a Device is reserved *for* a Branch and has no meaning
        // without it. Configured from the dependent side, as elsewhere in this model.
        builder.HasOne<Branch>()
            .WithMany()
            .HasForeignKey(d => d.BranchId)
            .IsRequired()
            .OnDelete(DeleteBehavior.Cascade);
    }
}

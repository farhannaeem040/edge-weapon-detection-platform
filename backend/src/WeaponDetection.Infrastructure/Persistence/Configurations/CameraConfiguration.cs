using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Metadata.Builders;
using WeaponDetection.Domain;

namespace WeaponDetection.Infrastructure.Persistence.Configurations;

public class CameraConfiguration : IEntityTypeConfiguration<Camera>
{
    public void Configure(EntityTypeBuilder<Camera> builder)
    {
        builder.ToTable("Cameras");

        builder.HasKey(c => c.CameraId);

        builder.Property(c => c.BranchId)
            .IsRequired();

        builder.Property(c => c.Name)
            .IsRequired()
            .HasMaxLength(Camera.NameMaxLength);

        // Stored as-is. FS-02/IP-01 require protected storage for the *device shared secret*
        // (IDeviceSecretProtector) and one-way hashing for the Activation Key secret and the Admin
        // password — the camera RTSP URL is in neither category, and no approved document asks for
        // it to be encrypted. It is not protected here on that basis, not by oversight.
        builder.Property(c => c.RtspUrl)
            .IsRequired()
            .HasMaxLength(Camera.RtspUrlMaxLength);

        // FS-12 §2/§3 — the administrator-entered public mount identifier. Required: a Camera with
        // no key has no annotated output, which is not a state the system supports.
        builder.Property(c => c.CameraKey)
            .IsRequired()
            .HasMaxLength(Camera.CameraKeyMaxLength);

        builder.Property(c => c.Enabled)
            .IsRequired()
            .HasDefaultValue(true);

        // FS-11 §2: the DeepStream source index this Camera occupies within its Device's pipeline
        // when enabled. Never derived from Name/RtspUrl/row order.
        builder.Property(c => c.SourceOrder)
            .IsRequired()
            .HasDefaultValue(0);

        // NOT unique. A Branch owns one or more Cameras (ARCH-001 §13.1, FS-02 §9/§12), so this
        // index exists to serve "the cameras of this branch" lookups, not to cap the count at one.
        // The one-per-branch uniqueness in the approved specs belongs to Device (BR-002/CON-007)
        // and arrives with T-13.
        builder.HasIndex(c => c.BranchId);

        // FS-11 §2: SourceOrder must be unique only among the *enabled* cameras of a branch — a
        // disabled camera never occupies a pipeline slot, so its SourceOrder cannot collide with
        // anything. Filtered/partial unique index, same pattern as
        // IX_ActivationKeys_DeviceRecordId_Unconsumed.
        builder.HasIndex(c => new { c.BranchId, c.SourceOrder })
            .IsUnique()
            .HasFilter("[Enabled] = 1")
            .HasDatabaseName("IX_Cameras_BranchId_SourceOrder_Enabled");

        // FS-12 §3: a CameraKey is unique within its Branch. Unlike the SourceOrder index this is
        // deliberately NOT filtered on Enabled — a disabled Camera keeps its key reserved, because
        // the key is a permanent public identity that re-enabling must restore unchanged. Allowing a
        // second Camera to take a disabled one's key would silently repoint an operator's URL.
        //
        // Enforced at the database as well as the service layer so no concurrent creation can
        // interleave past the service-level check.
        builder.HasIndex(c => new { c.BranchId, c.CameraKey })
            .IsUnique()
            .HasDatabaseName("IX_Cameras_BranchId_CameraKey");

        // Cascade: a Camera is meaningless without the Branch it is installed at, and the Branch
        // owns it (ARCH-001 §13.1). Mirrors the AdminUser → AdminSession precedent. No delete
        // endpoint exists yet; this declares the schema-level behavior for when one does.
        builder.HasOne<Branch>()
            .WithMany()
            .HasForeignKey(c => c.BranchId)
            .IsRequired()
            .OnDelete(DeleteBehavior.Cascade);
    }
}

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

        builder.Property(c => c.Enabled)
            .IsRequired()
            .HasDefaultValue(true);

        // NOT unique. A Branch owns one or more Cameras (ARCH-001 §13.1, FS-02 §9/§12), so this
        // index exists to serve "the cameras of this branch" lookups, not to cap the count at one.
        // The one-per-branch uniqueness in the approved specs belongs to Device (BR-002/CON-007)
        // and arrives with T-13.
        builder.HasIndex(c => c.BranchId);

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

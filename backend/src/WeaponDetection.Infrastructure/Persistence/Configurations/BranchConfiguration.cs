using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Metadata.Builders;
using WeaponDetection.Domain;

namespace WeaponDetection.Infrastructure.Persistence.Configurations;

public class BranchConfiguration : IEntityTypeConfiguration<Branch>
{
    public void Configure(EntityTypeBuilder<Branch> builder)
    {
        builder.ToTable("Branches");

        builder.HasKey(b => b.BranchId);

        // Column lengths come from the Domain entity's own constants, so the database constraint
        // and the constructor invariant are the same number by construction.
        builder.Property(b => b.Name)
            .IsRequired()
            .HasMaxLength(Branch.NameMaxLength);

        builder.Property(b => b.Address)
            .IsRequired()
            .HasMaxLength(Branch.AddressMaxLength);

        builder.Property(b => b.ContactDetails)
            .IsRequired()
            .HasMaxLength(Branch.ContactDetailsMaxLength);

        // No unique index on Name (or any other Branch column): IP-01 §4 lists no constraint for
        // Branch, and neither FS-02 nor ARCH-001 forbids two branches sharing a name. Adding one
        // would invent a business rule no approved document states.
        //
        // The Branch–Camera relationship is configured once, from the dependent side
        // (CameraConfiguration), matching the existing AdminSession/AdminUser precedent.
    }
}

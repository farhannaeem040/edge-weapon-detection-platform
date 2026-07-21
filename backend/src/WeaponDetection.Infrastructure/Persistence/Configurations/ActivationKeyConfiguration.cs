using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Metadata.Builders;
using WeaponDetection.Domain;

namespace WeaponDetection.Infrastructure.Persistence.Configurations;

public class ActivationKeyConfiguration : IEntityTypeConfiguration<ActivationKey>
{
    public void Configure(EntityTypeBuilder<ActivationKey> builder)
    {
        builder.ToTable("ActivationKeys");

        // The keyId itself is the primary key (IP-01 §4, FS-02 §1.4). That is what satisfies AC-14:
        // an activation resolves its record by a direct primary-key seek on the presented keyId, so
        // the Backend never scans the table hashing the presented secret against every stored row.
        // No secondary index is needed for the activation path — this *is* the index.
        builder.HasKey(k => k.ActivationKeyId);

        // Non-Unicode and fixed-bounded: a keyId is generated from an ASCII alphabet (T-14 picks the
        // exact length and character set, within this bound — FS-02 §19 leaves it open). Declaring
        // it varchar rather than nvarchar halves the width of the clustered key every ActivationKey
        // lookup traverses.
        builder.Property(k => k.ActivationKeyId)
            .IsRequired()
            .HasMaxLength(ActivationKey.ActivationKeyIdMaxLength)
            .IsUnicode(false);

        builder.Property(k => k.DeviceRecordId)
            .IsRequired();

        // A salted adaptive hash of the secret half only — never the plaintext secret, and never
        // the complete plaintext key (FS-02 §1.4, §11; ARCH-001 §15.5). Sized to match
        // AdminUser.PasswordHash, since IP-01 §8 has both produced by the same hashing utility.
        builder.Property(k => k.SecretHash)
            .IsRequired()
            .HasMaxLength(ActivationKey.SecretHashMaxLength);

        builder.Property(k => k.Status)
            .IsRequired()
            .HasConversion<string>()
            .HasMaxLength(32);

        // Serves the one query regeneration needs: "the current Unconsumed key for this Device"
        // (IP-01 §4). Not unique — a Device accumulates a history of keys, of which the older ones
        // are Consumed or Invalidated (FS-02 §5.3).
        builder.HasIndex(k => new { k.DeviceRecordId, k.Status });

        // IP-05 T-49: at most one Unconsumed Activation Key per Device, enforced by the schema rather
        // than by application code (AC-6/AC-17). Filtered so the many historical Consumed/Invalidated
        // keys never collide — uniqueness applies only to the single live (Unconsumed) key. Status is
        // stored as its enum name (above), so the filter matches the literal 'Unconsumed'. This is
        // what makes two concurrent regenerations unable to both commit a live key: the losing INSERT
        // of a second Unconsumed row violates this index and its transaction rolls back.
        builder.HasIndex(k => k.DeviceRecordId)
            .IsUnique()
            .HasFilter("[Status] = 'Unconsumed'")
            .HasDatabaseName("IX_ActivationKeys_DeviceRecordId_Unconsumed");

        // The FK is to the *internal* DeviceRecordId, never to the external DeviceId — which is
        // NULL at the moment a key is first issued and so could not be a foreign key at all
        // (FS-02 §1.3, §9). Cascade: a key is meaningless without the Device it activates.
        builder.HasOne<Device>()
            .WithMany()
            .HasForeignKey(k => k.DeviceRecordId)
            .IsRequired()
            .OnDelete(DeleteBehavior.Cascade);
    }
}

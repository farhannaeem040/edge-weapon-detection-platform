using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace WeaponDetection.Infrastructure.Persistence.Migrations
{
    /// <inheritdoc />
    public partial class ActivationKeyUnconsumedUniqueIndex : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            // IP-05 T-49 — pre-index duplicate guard. The filtered unique index below requires that no
            // device already has more than one Unconsumed Activation Key. Rather than silently
            // consume, invalidate, delete, or arbitrarily pick a "winner", the migration aborts
            // clearly if any duplicate exists, leaving all data unchanged (the migration runs in a
            // transaction, so the THROW rolls the whole step back). The message names no device id,
            // key, hash, secret, or row content. In this prototype the application already maintains
            // the single-Unconsumed-key invariant, so this is expected to be a no-op.
            migrationBuilder.Sql(@"
IF EXISTS (
    SELECT 1
    FROM [ActivationKeys]
    WHERE [Status] = 'Unconsumed'
    GROUP BY [DeviceRecordId]
    HAVING COUNT(*) > 1
)
BEGIN
    THROW 50001, 'Cannot create the unique activation-key index because duplicate unconsumed activation keys exist for at least one device. Resolve the duplicates before applying this migration.', 1;
END;
");

            migrationBuilder.CreateIndex(
                name: "IX_ActivationKeys_DeviceRecordId_Unconsumed",
                table: "ActivationKeys",
                column: "DeviceRecordId",
                unique: true,
                filter: "[Status] = 'Unconsumed'");
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropIndex(
                name: "IX_ActivationKeys_DeviceRecordId_Unconsumed",
                table: "ActivationKeys");
        }
    }
}

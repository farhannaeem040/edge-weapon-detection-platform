using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace WeaponDetection.Infrastructure.Persistence.Migrations
{
    /// <summary>
    /// FS-12 / IP-14 T-265. Additive: adds <c>Devices.JetsonHost</c>, <c>Devices.RtspOutputPort</c> and
    /// <c>Cameras.CameraKey</c>, backfills all three from existing data, and only then applies the
    /// Branch-scoped unique index on the key.
    /// </summary>
    /// <remarks>
    /// The ordering matters. The scaffolded version created the unique index immediately after adding
    /// <c>CameraKey NOT NULL DEFAULT ''</c>, which fails the moment a Branch owns two Cameras — both
    /// would hold the same empty key. The backfill therefore runs between the two steps.
    ///
    /// <para>The CameraKey backfill has two tiers, and the fallback tier is the important one:</para>
    /// <list type="bullet">
    /// <item>Rows explicitly approved for the POC get their human-readable key.</item>
    /// <item>Every other row falls back to its own <c>CameraId</c> rendered as a GUID string. That is
    /// not an arbitrary derivation from a mutable label — the brief forbids that because collisions
    /// are possible. A GUID is unique by construction, and it also happens to satisfy the FS-12 §3
    /// pattern exactly (lowercase hex and hyphens, starting and ending alphanumeric). The consequence
    /// is the useful part: an un-approved row keeps the byte-identical <c>cameras/{guid}</c> mount it
    /// already had, so it neither changes its <c>configurationVersion</c> nor restarts anyone's
    /// Bridge.</item>
    /// </list>
    ///
    /// <para><c>AnnotatedOutputBaseUrl</c> is deliberately left in place and still populated
    /// (FS-12 §5, Option A) so that rolling the Backend back to the previous build keeps composing
    /// working URLs. A later feature drops it.</para>
    ///
    /// <para>The host/port parser handles the <c>rtsp://host:port</c> and <c>rtsp://host</c> forms. A
    /// bracketed IPv6 literal is deliberately skipped: no deployment holds one, and inventing SQL to
    /// split it would be untested code on a one-time path. Such a row keeps a NULL <c>JetsonHost</c>
    /// and continues to compose from the retained legacy column.</para>
    /// </remarks>
    public partial class AddCameraKeyAndDeviceNetwork : Migration
    {
        // FS-12 §7 — the approved POC backfill values, keyed on the immutable CameraId rather than on
        // Camera.Name. Matching on the name would reintroduce exactly the mutable-label coupling this
        // feature exists to remove, and would silently do nothing if the Camera had been renamed.
        private const string FrontCameraId = "2613B331-8783-4D51-903A-3E41A979A14C";
        private const string RearCameraId = "AD8A1F09-7FBA-4794-8F73-63C7E2C57C92";

        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<string>(
                name: "JetsonHost",
                table: "Devices",
                type: "nvarchar(255)",
                maxLength: 255,
                nullable: true);

            migrationBuilder.AddColumn<int>(
                name: "RtspOutputPort",
                table: "Devices",
                type: "int",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "CameraKey",
                table: "Cameras",
                type: "nvarchar(64)",
                maxLength: 64,
                nullable: false,
                defaultValue: "");

            // --- Device backfill: split the retained base URL into its two structured parts. ---
            migrationBuilder.Sql(
                """
                WITH Parsed AS (
                    SELECT
                        DeviceRecordId,
                        REPLACE(REPLACE(AnnotatedOutputBaseUrl, 'rtsps://', ''), 'rtsp://', '') AS Remainder
                    FROM Devices
                    WHERE AnnotatedOutputBaseUrl IS NOT NULL
                      AND JetsonHost IS NULL
                      AND AnnotatedOutputBaseUrl NOT LIKE '%[[]%'
                )
                UPDATE d
                SET d.JetsonHost = CASE
                        WHEN CHARINDEX(':', p.Remainder) > 0
                            THEN LEFT(p.Remainder, CHARINDEX(':', p.Remainder) - 1)
                        ELSE p.Remainder
                    END,
                    d.RtspOutputPort = CASE
                        WHEN CHARINDEX(':', p.Remainder) > 0
                            THEN TRY_CAST(
                                SUBSTRING(p.Remainder, CHARINDEX(':', p.Remainder) + 1, 10) AS int)
                        ELSE 8554
                    END
                FROM Devices d
                INNER JOIN Parsed p ON p.DeviceRecordId = d.DeviceRecordId;
                """);

            // A malformed stored port would leave NULL; fall back to the documented default rather
            // than leaving a host that composes an incomplete URL.
            migrationBuilder.Sql(
                """
                UPDATE Devices
                SET RtspOutputPort = 8554
                WHERE JetsonHost IS NOT NULL AND RtspOutputPort IS NULL;
                """);

            // --- Camera backfill, tier 1: the approved POC keys. ---
            migrationBuilder.Sql(
                $"""
                UPDATE Cameras SET CameraKey = 'front-camera'
                WHERE CameraId = '{FrontCameraId}' AND CameraKey = '';

                UPDATE Cameras SET CameraKey = 'rear-entrance'
                WHERE CameraId = '{RearCameraId}' AND CameraKey = '';
                """);

            // --- Camera backfill, tier 2: every remaining row keeps its existing GUID mount. ---
            migrationBuilder.Sql(
                """
                UPDATE Cameras
                SET CameraKey = LOWER(CONVERT(varchar(36), CameraId))
                WHERE CameraKey = '';
                """);

            migrationBuilder.CreateIndex(
                name: "IX_Cameras_BranchId_CameraKey",
                table: "Cameras",
                columns: new[] { "BranchId", "CameraKey" },
                unique: true);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropIndex(
                name: "IX_Cameras_BranchId_CameraKey",
                table: "Cameras");

            migrationBuilder.DropColumn(
                name: "JetsonHost",
                table: "Devices");

            migrationBuilder.DropColumn(
                name: "RtspOutputPort",
                table: "Devices");

            migrationBuilder.DropColumn(
                name: "CameraKey",
                table: "Cameras");
        }
    }
}

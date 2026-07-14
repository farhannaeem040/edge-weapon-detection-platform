using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace WeaponDetection.Infrastructure.Persistence.Migrations
{
    /// <inheritdoc />
    public partial class DeviceAndActivationKeySchema : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.CreateTable(
                name: "Devices",
                columns: table => new
                {
                    DeviceRecordId = table.Column<Guid>(type: "uniqueidentifier", nullable: false),
                    DeviceId = table.Column<Guid>(type: "uniqueidentifier", nullable: true),
                    BranchId = table.Column<Guid>(type: "uniqueidentifier", nullable: false),
                    ActivationStatus = table.Column<string>(type: "nvarchar(32)", maxLength: 32, nullable: false),
                    ProtectedSharedSecret = table.Column<string>(type: "nvarchar(1024)", maxLength: 1024, nullable: true),
                    LastKnownAddress = table.Column<string>(type: "nvarchar(256)", maxLength: 256, nullable: true)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_Devices", x => x.DeviceRecordId);
                    table.ForeignKey(
                        name: "FK_Devices_Branches_BranchId",
                        column: x => x.BranchId,
                        principalTable: "Branches",
                        principalColumn: "BranchId",
                        onDelete: ReferentialAction.Cascade);
                });

            migrationBuilder.CreateTable(
                name: "ActivationKeys",
                columns: table => new
                {
                    ActivationKeyId = table.Column<string>(type: "varchar(64)", unicode: false, maxLength: 64, nullable: false),
                    DeviceRecordId = table.Column<Guid>(type: "uniqueidentifier", nullable: false),
                    SecretHash = table.Column<string>(type: "nvarchar(512)", maxLength: 512, nullable: false),
                    Status = table.Column<string>(type: "nvarchar(32)", maxLength: 32, nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_ActivationKeys", x => x.ActivationKeyId);
                    table.ForeignKey(
                        name: "FK_ActivationKeys_Devices_DeviceRecordId",
                        column: x => x.DeviceRecordId,
                        principalTable: "Devices",
                        principalColumn: "DeviceRecordId",
                        onDelete: ReferentialAction.Cascade);
                });

            migrationBuilder.CreateIndex(
                name: "IX_ActivationKeys_DeviceRecordId_Status",
                table: "ActivationKeys",
                columns: new[] { "DeviceRecordId", "Status" });

            migrationBuilder.CreateIndex(
                name: "IX_Devices_BranchId",
                table: "Devices",
                column: "BranchId",
                unique: true);

            migrationBuilder.CreateIndex(
                name: "IX_Devices_DeviceId",
                table: "Devices",
                column: "DeviceId",
                unique: true,
                filter: "[DeviceId] IS NOT NULL");
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropTable(
                name: "ActivationKeys");

            migrationBuilder.DropTable(
                name: "Devices");
        }
    }
}

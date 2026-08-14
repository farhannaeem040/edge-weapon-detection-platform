using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace WeaponDetection.Infrastructure.Persistence.Migrations
{
    /// <inheritdoc />
    public partial class AddBranchDailyAlertQuota : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<string>(
                name: "TimeZoneId",
                table: "Branches",
                type: "nvarchar(100)",
                maxLength: 100,
                nullable: true);

            migrationBuilder.CreateTable(
                name: "BranchDailyAlertQuotas",
                columns: table => new
                {
                    BranchId = table.Column<Guid>(type: "uniqueidentifier", nullable: false),
                    LocalDate = table.Column<string>(type: "nvarchar(10)", maxLength: 10, nullable: false),
                    AcceptedAlertCount = table.Column<int>(type: "int", nullable: false),
                    SuppressedDetectionCount = table.Column<int>(type: "int", nullable: false),
                    GunSuppressedCount = table.Column<int>(type: "int", nullable: false),
                    KnifeSuppressedCount = table.Column<int>(type: "int", nullable: false),
                    FirstSuppressedAtUtc = table.Column<DateTime>(type: "datetime2", nullable: true),
                    LastSuppressedAtUtc = table.Column<DateTime>(type: "datetime2", nullable: true)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_BranchDailyAlertQuotas", x => new { x.BranchId, x.LocalDate });
                });

            migrationBuilder.CreateTable(
                name: "SuppressedDetectionEvents",
                columns: table => new
                {
                    DeviceId = table.Column<Guid>(type: "uniqueidentifier", nullable: false),
                    EventId = table.Column<Guid>(type: "uniqueidentifier", nullable: false),
                    BranchId = table.Column<Guid>(type: "uniqueidentifier", nullable: false),
                    LocalDate = table.Column<string>(type: "nvarchar(10)", maxLength: 10, nullable: false),
                    ClassName = table.Column<string>(type: "nvarchar(200)", maxLength: 200, nullable: false),
                    DetectedAtUtc = table.Column<DateTime>(type: "datetime2", nullable: false),
                    Reason = table.Column<string>(type: "nvarchar(100)", maxLength: 100, nullable: false),
                    CreatedAtUtc = table.Column<DateTime>(type: "datetime2", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_SuppressedDetectionEvents", x => new { x.DeviceId, x.EventId });
                });

            migrationBuilder.CreateIndex(
                name: "IX_SuppressedDetectionEvents_BranchId_LocalDate",
                table: "SuppressedDetectionEvents",
                columns: new[] { "BranchId", "LocalDate" });
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropTable(
                name: "BranchDailyAlertQuotas");

            migrationBuilder.DropTable(
                name: "SuppressedDetectionEvents");

            migrationBuilder.DropColumn(
                name: "TimeZoneId",
                table: "Branches");
        }
    }
}

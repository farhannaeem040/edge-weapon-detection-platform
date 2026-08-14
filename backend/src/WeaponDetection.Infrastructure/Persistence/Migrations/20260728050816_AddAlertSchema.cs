using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace WeaponDetection.Infrastructure.Persistence.Migrations
{
    /// <inheritdoc />
    public partial class AddAlertSchema : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.CreateTable(
                name: "Alerts",
                columns: table => new
                {
                    AlertId = table.Column<Guid>(type: "uniqueidentifier", nullable: false),
                    DeviceId = table.Column<Guid>(type: "uniqueidentifier", nullable: false),
                    EventId = table.Column<Guid>(type: "uniqueidentifier", nullable: false),
                    CameraId = table.Column<Guid>(type: "uniqueidentifier", nullable: false),
                    DetectedAtUtc = table.Column<DateTime>(type: "datetime2", nullable: false),
                    ReceivedAtUtc = table.Column<DateTime>(type: "datetime2", nullable: false),
                    ClassId = table.Column<int>(type: "int", nullable: false),
                    ClassName = table.Column<string>(type: "nvarchar(200)", maxLength: 200, nullable: false),
                    Confidence = table.Column<double>(type: "float", nullable: false),
                    FrameNumber = table.Column<long>(type: "bigint", nullable: false),
                    FrameWidth = table.Column<int>(type: "int", nullable: false),
                    FrameHeight = table.Column<int>(type: "int", nullable: false),
                    BboxLeft = table.Column<double>(type: "float", nullable: false),
                    BboxTop = table.Column<double>(type: "float", nullable: false),
                    BboxWidth = table.Column<double>(type: "float", nullable: false),
                    BboxHeight = table.Column<double>(type: "float", nullable: false),
                    SnapshotReference = table.Column<string>(type: "nvarchar(2048)", maxLength: 2048, nullable: true),
                    Status = table.Column<string>(type: "nvarchar(32)", maxLength: 32, nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_Alerts", x => x.AlertId);
                });

            migrationBuilder.CreateIndex(
                name: "IX_Alerts_CameraId",
                table: "Alerts",
                column: "CameraId");

            migrationBuilder.CreateIndex(
                name: "IX_Alerts_DeviceId",
                table: "Alerts",
                column: "DeviceId");

            migrationBuilder.CreateIndex(
                name: "IX_Alerts_DeviceId_EventId",
                table: "Alerts",
                columns: new[] { "DeviceId", "EventId" },
                unique: true);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropTable(
                name: "Alerts");
        }
    }
}

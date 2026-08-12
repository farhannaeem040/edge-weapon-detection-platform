using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace WeaponDetection.Infrastructure.Persistence.Migrations
{
    /// <inheritdoc />
    public partial class AddAlertSnapshotFields : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<string>(
                name: "SnapshotContentType",
                table: "Alerts",
                type: "nvarchar(100)",
                maxLength: 100,
                nullable: true);

            migrationBuilder.AddColumn<DateTime>(
                name: "SnapshotReceivedAtUtc",
                table: "Alerts",
                type: "datetime2",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "SnapshotSha256",
                table: "Alerts",
                type: "nvarchar(64)",
                maxLength: 64,
                nullable: true);

            migrationBuilder.AddColumn<long>(
                name: "SnapshotSizeBytes",
                table: "Alerts",
                type: "bigint",
                nullable: true);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropColumn(
                name: "SnapshotContentType",
                table: "Alerts");

            migrationBuilder.DropColumn(
                name: "SnapshotReceivedAtUtc",
                table: "Alerts");

            migrationBuilder.DropColumn(
                name: "SnapshotSha256",
                table: "Alerts");

            migrationBuilder.DropColumn(
                name: "SnapshotSizeBytes",
                table: "Alerts");
        }
    }
}

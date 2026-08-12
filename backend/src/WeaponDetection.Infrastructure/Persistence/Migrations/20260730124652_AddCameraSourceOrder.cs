using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace WeaponDetection.Infrastructure.Persistence.Migrations
{
    /// <inheritdoc />
    public partial class AddCameraSourceOrder : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<int>(
                name: "SourceOrder",
                table: "Cameras",
                type: "int",
                nullable: false,
                defaultValue: 0);

            migrationBuilder.CreateIndex(
                name: "IX_Cameras_BranchId_SourceOrder_Enabled",
                table: "Cameras",
                columns: new[] { "BranchId", "SourceOrder" },
                unique: true,
                filter: "[Enabled] = 1");
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropIndex(
                name: "IX_Cameras_BranchId_SourceOrder_Enabled",
                table: "Cameras");

            migrationBuilder.DropColumn(
                name: "SourceOrder",
                table: "Cameras");
        }
    }
}

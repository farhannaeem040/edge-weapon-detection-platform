using System;
using System.Collections.Generic;
using System.Linq;
using WeaponDetection.Api.Contracts;
using WeaponDetection.Application.Interfaces;
using Xunit;

namespace WeaponDetection.UnitTests.Contracts;

// FS-15 §6.2, IP-17 T-4/T-8. The CSV escaping/columns contract, tested directly because the writer is
// pure. These rules are the difference between an export that a spreadsheet reads correctly and one
// that either corrupts a row or executes a formula the Admin never typed.
public class AnalyticsCsvWriterTests
{
    private static AnalyticsExportRow Row(
        string branch = "Ljmu Branch",
        string camera = "Front Camera",
        double? latencyMs = 2440,
        string detectionType = "gun",
        double confidence = 0.91,
        bool snapshotAvailable = true) =>
        new(
            new DateTime(2026, 8, 17, 5, 7, 9, DateTimeKind.Utc),
            new DateTime(2026, 8, 17, 5, 7, 11, DateTimeKind.Utc),
            latencyMs,
            branch,
            camera,
            detectionType,
            confidence,
            "New",
            snapshotAvailable);

    private static string[] Lines(string csv) =>
        csv.Split("\r\n", StringSplitOptions.RemoveEmptyEntries);

    [Fact]
    public void Write_EmitsTheDocumentedHeaderRowInOrder()
    {
        var csv = AnalyticsCsvWriter.Write([]);

        Assert.Equal(
            "Detected At (UTC),Received At (UTC),Delivery Latency (ms),Branch,Camera," +
            "Detection Type,Confidence,Status,Snapshot Available",
            Lines(csv)[0]);
    }

    [Fact]
    public void Write_WithNoRows_StillEmitsTheHeaderOnly()
    {
        Assert.Single(Lines(AnalyticsCsvWriter.Write([])));
    }

    [Fact]
    public void Write_UsesCrLfLineEndingsPerRfc4180()
    {
        var csv = AnalyticsCsvWriter.Write([Row()]);

        Assert.EndsWith("\r\n", csv);
        Assert.Equal(2, csv.Split("\r\n", StringSplitOptions.RemoveEmptyEntries).Length);
    }

    [Fact]
    public void Write_FormatsTimestampsAsInvariantUtc()
    {
        var line = Lines(AnalyticsCsvWriter.Write([Row()]))[1];

        Assert.StartsWith("2026-08-17 05:07:09,2026-08-17 05:07:11,", line);
    }

    [Fact]
    public void Write_LeavesLatencyBlankForAnIneligibleSampleRatherThanWritingZero()
    {
        // A blank cell is skipped by a spreadsheet average; a 0 would drag it toward zero and misstate
        // delivery performance (FS-15 §5.4/§6.2).
        var line = Lines(AnalyticsCsvWriter.Write([Row(latencyMs: null)]))[1];

        Assert.Contains(",,", line);
        Assert.DoesNotContain(",0,", line);
    }

    [Fact]
    public void Write_QuotesAValueContainingAComma()
    {
        var line = Lines(AnalyticsCsvWriter.Write([Row(branch: "Liverpool, Mount Pleasant")]))[1];

        Assert.Contains("\"Liverpool, Mount Pleasant\"", line);
    }

    [Fact]
    public void Write_DoublesEmbeddedQuotes()
    {
        var line = Lines(AnalyticsCsvWriter.Write([Row(camera: "The \"Main\" Door")]))[1];

        Assert.Contains("\"The \"\"Main\"\" Door\"", line);
    }

    [Fact]
    public void Write_QuotesAValueContainingANewlineSoItCannotBreakOutOfItsRow()
    {
        var csv = AnalyticsCsvWriter.Write([Row(branch: "Line one\nLine two")]);

        Assert.Contains("\"Line one\nLine two\"", csv);
    }

    [Theory]
    [InlineData("=1+1")]
    [InlineData("+44 151 231 2121")]
    [InlineData("-lookup")]
    [InlineData("@import")]
    public void Write_DefusesSpreadsheetFormulaInjectionInAdministratorEnteredText(string dangerous)
    {
        // Branch and Camera names are administrator-entered free text and can legitimately start with
        // one of these characters; the cell must never be evaluated as a formula.
        var line = Lines(AnalyticsCsvWriter.Write([Row(branch: dangerous)]))[1];

        Assert.Contains("'" + dangerous.Split(',')[0], line);
    }

    [Fact]
    public void Write_LeavesOrdinaryTextUnquoted()
    {
        var line = Lines(AnalyticsCsvWriter.Write([Row()]))[1];

        Assert.Contains("Ljmu Branch,Front Camera,gun,", line);
        Assert.DoesNotContain("\"Ljmu Branch\"", line);
    }

    [Fact]
    public void Write_RendersSnapshotAvailabilityAsYesOrNo()
    {
        Assert.EndsWith(",Yes", Lines(AnalyticsCsvWriter.Write([Row()]))[1]);
        Assert.EndsWith(",No", Lines(AnalyticsCsvWriter.Write([Row(snapshotAvailable: false)]))[1]);
    }

    [Fact]
    public void Write_FormatsConfidenceInvariantlySoALocaleCannotTurnItIntoASecondColumn()
    {
        // A comma decimal separator would silently add a column on a European-locale host.
        var line = Lines(AnalyticsCsvWriter.Write([Row(confidence: 0.91)]))[1];

        Assert.Contains(",0.91,", line);
    }

    [Fact]
    public void Write_ProducesOneRowPerExportRow()
    {
        var rows = Enumerable.Range(0, 25).Select(_ => Row()).ToList();

        Assert.Equal(26, Lines(AnalyticsCsvWriter.Write(rows)).Length);
    }

    [Fact]
    public void Columns_ContainNoSensitiveField()
    {
        // The export must never carry an RTSP URL (which can embed credentials), a CameraKey, a
        // snapshot reference, a filesystem path, or any internal identifier (FS-15 §6.2).
        foreach (var forbidden in new[]
                 { "Rtsp", "Url", "Secret", "Key", "Path", "Reference", "Sha", "DeviceRecordId", "Token" })
        {
            Assert.DoesNotContain(
                AnalyticsCsvWriter.Columns,
                column => column.Contains(forbidden, StringComparison.OrdinalIgnoreCase));
        }
    }

    [Fact]
    public void ExportRow_ExposesNoSensitiveProperty()
    {
        // Guards the projection itself, not just the column list: a future edit that adds RtspUrl to
        // AnalyticsExportRow would fail here before it could ever reach a downloaded file.
        var properties = typeof(AnalyticsExportRow).GetProperties().Select(p => p.Name).ToList();

        Assert.DoesNotContain("RtspUrl", properties);
        Assert.DoesNotContain("CameraKey", properties);
        Assert.DoesNotContain("SnapshotReference", properties);
        Assert.DoesNotContain("DeviceRecordId", properties);
        Assert.DoesNotContain("ProtectedSharedSecret", properties);
        Assert.DoesNotContain("ActivationKey", properties);
    }

    [Fact]
    public void FileName_IsDateStampedInUtc()
    {
        Assert.Equal(
            "operational-analytics-2026-08-17.csv",
            AnalyticsCsvWriter.FileName(new DateTime(2026, 8, 17, 23, 59, 59, DateTimeKind.Utc)));
    }

    [Fact]
    public void Write_RejectsANullRowCollection()
    {
        Assert.Throws<ArgumentNullException>(() =>
            AnalyticsCsvWriter.Write(null!));
    }

    [Fact]
    public void Write_HandlesEveryColumnBeingHostile()
    {
        var rows = new List<AnalyticsExportRow>
        {
            Row(branch: "=cmd|'/c calc'!A1", camera: "a,b\"c\nd", detectionType: "gun"),
        };

        var csv = AnalyticsCsvWriter.Write(rows);

        // Defused but not quoted — it carries no comma, quote, or newline, so quoting it would only
        // add noise a reader would have to strip.
        Assert.Contains("'=cmd|'/c calc'!A1", csv);
        Assert.Contains("\"a,b\"\"c\nd\"", csv);
    }
}

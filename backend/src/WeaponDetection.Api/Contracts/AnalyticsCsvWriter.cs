using System.Globalization;
using System.Text;
using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.Api.Contracts;

// Renders the Operational Analytics export rows as RFC 4180 CSV (FS-15 §6.2, IP-17 T-4).
//
// Lives in Api/Contracts alongside RtspUrlSanitizer, for the same reason: CSV is a *wire
// representation*, not a persistence or aggregation concern. The Infrastructure service produces safe
// AnalyticsExportRow values; this type decides only how they are spelled on the wire.
//
// Pure and static: no DbContext, no clock, no I/O — so every escaping rule below is directly
// unit-testable without a host, and the endpoint's only job is to call this and set the headers.
//
// The column set is fixed here rather than assembled by the controller, so the "nothing sensitive is
// ever exported" rule has exactly one place to be enforced and reviewed. AnalyticsExportRow itself
// carries no RtspUrl, CameraKey, SnapshotReference, filesystem path, DeviceRecordId, activation key,
// or Device secret — the projection is the boundary, and this writer cannot widen it.
public static class AnalyticsCsvWriter
{
    // FS-15 §6.2 — exactly these columns, in this order. Timestamps are labelled UTC because they are
    // written as UTC; a reader must never have to guess the zone.
    public static readonly IReadOnlyList<string> Columns =
    [
        "Detected At (UTC)",
        "Received At (UTC)",
        "Delivery Latency (ms)",
        "Branch",
        "Camera",
        "Detection Type",
        "Confidence",
        "Status",
        "Snapshot Available",
    ];

    // CRLF, per RFC 4180 §2 — and the line ending Excel expects regardless of host platform, so the
    // file is not produced differently on a Linux container than on a Windows developer machine.
    private const string LineSeparator = "\r\n";

    public static string Write(IReadOnlyList<AnalyticsExportRow> rows)
    {
        ArgumentNullException.ThrowIfNull(rows);

        var builder = new StringBuilder();

        builder.Append(string.Join(',', Columns.Select(Escape)));
        builder.Append(LineSeparator);

        foreach (var row in rows)
        {
            builder.Append(string.Join(',',
            [
                Escape(FormatTimestamp(row.DetectedAtUtc)),
                Escape(FormatTimestamp(row.ReceivedAtUtc)),
                // Blank — never a zero or a placeholder — for a row excluded by the §5.4 eligibility
                // rule, so a spreadsheet average over this column silently skips it rather than being
                // dragged toward zero.
                Escape(row.DeliveryLatencyMs?.ToString("0.###", CultureInfo.InvariantCulture) ?? string.Empty),
                Escape(row.BranchName),
                Escape(row.CameraName),
                Escape(row.DetectionType),
                Escape(row.Confidence.ToString("0.####", CultureInfo.InvariantCulture)),
                Escape(row.Status),
                Escape(row.SnapshotAvailable ? "Yes" : "No"),
            ]));
            builder.Append(LineSeparator);
        }

        return builder.ToString();
    }

    // The download filename (FS-15 §6.2). Date-stamped in UTC so two exports taken either side of
    // local midnight cannot claim the same day.
    public static string FileName(DateTime generatedAtUtc) =>
        $"operational-analytics-{generatedAtUtc:yyyy-MM-dd}.csv";

    // Round-trippable, sortable, unambiguous. Not a culture-sensitive format: the Backend runs in a
    // container whose locale is not the reader's.
    private static string FormatTimestamp(DateTime value) =>
        value.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture);

    // Two separate concerns, deliberately applied in this order:
    //
    //  1. Formula-injection defusing. A cell beginning with '=', '+', '-' or '@' is interpreted as a
    //     formula by Excel/Sheets/LibreOffice; a Branch or Camera name is administrator-entered free
    //     text and could begin with one. Prefixing a single quote makes the cell inert text. This runs
    //     first so the prefix itself is then subject to the quoting rule below.
    //  2. RFC 4180 quoting. A value containing a quote, comma, CR or LF is wrapped in double quotes
    //     with embedded quotes doubled, so no field value can break out of its cell or its row.
    private static string Escape(string? value)
    {
        var text = value ?? string.Empty;

        if (text.Length > 0 && text[0] is '=' or '+' or '-' or '@')
        {
            text = "'" + text;
        }

        if (text.IndexOfAny(['"', ',', '\r', '\n']) < 0)
        {
            return text;
        }

        return '"' + text.Replace("\"", "\"\"") + '"';
    }
}

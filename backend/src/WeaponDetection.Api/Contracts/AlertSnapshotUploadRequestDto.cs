using Microsoft.AspNetCore.Mvc;

namespace WeaponDetection.Api.Contracts;

// The multipart/form-data body for POST /api/v1/alerts/{alertId}/snapshot (FS-08 §9): `file`,
// `eventId`, `sha256`. [FromForm] binds this from the request's multipart form fields — ADR-009's
// binary/multipart exception, the same exception this endpoint's route was frozen for.
public sealed class AlertSnapshotUploadRequestDto
{
    [FromForm(Name = "file")]
    public IFormFile? File { get; set; }

    [FromForm(Name = "eventId")]
    public string? EventId { get; set; }

    // Optional: an early-reject sanity check only (FS-08 §9) — never trusted for the actual
    // duplicate/conflict decision, which the server's own recomputed SHA-256 makes.
    [FromForm(Name = "sha256")]
    public string? Sha256 { get; set; }
}

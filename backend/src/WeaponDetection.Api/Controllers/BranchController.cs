using Microsoft.AspNetCore.Mvc;
using WeaponDetection.Api.Contracts;
using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.Api.Controllers;

// FS-02 §10.1/§10.3, IP-01 §10/T-16. Branch creation and branch read endpoints. Every orchestration
// (transactional creation, projection) stays in IBranchService; this controller only translates
// between HTTP and the Application layer.
//
// The controller carries no [AllowAnonymous], so the application's fallback policy (T-10) subjects
// every action here to Admin JWT validation *and* the active-session check before the action body
// runs (FS-02 §10.1–§10.3 "Valid Admin JWT + active session"). No secret or internal DeviceRecordId
// can leave through these endpoints: the response DTOs cannot carry them, and the plaintext
// Activation Key is placed on the wire only by the create response, exactly once (FS-02 §1.3, §7).
[ApiController]
[Route("api/v1/branches")]
public class BranchController : ControllerBase
{
    private readonly IBranchService _branchService;

    public BranchController(IBranchService branchService)
    {
        _branchService = branchService;
    }

    // FS-02 §10.1: creates the Branch, its Cameras, the reserved unactivated Device, and the first
    // Activation Key in one transaction, and returns 201 with the created branch and the complete
    // plaintext Activation Key — the only response that ever carries it.
    [HttpPost]
    public async Task<IActionResult> Create(
        [FromBody] CreateBranchRequestDto request,
        CancellationToken cancellationToken)
    {
        // Presence/length are already enforced by DataAnnotations (a blank/oversized field is a 400
        // before this runs). RTSP-URL *format* and the "at least one non-null camera" rule are
        // Application-layer checks that throw ArgumentException; those surface here for the one class
        // of request that is DTO-valid but still invalid, and become a 400 like any other bad input.
        var appRequest = new NewBranchRequest(
            request.Name,
            request.Address,
            request.ContactDetails,
            request.Cameras
                // A null element passes model validation; preserve it so the service's own guard
                // rejects it as an ArgumentException rather than this mapping throwing an NRE.
                .Select(c => c is null ? null! : new NewCameraRequest(c.Name, c.RtspUrl))
                .ToList());

        BranchCreationResult result;
        try
        {
            result = await _branchService.CreateBranchAsync(appRequest, cancellationToken);
        }
        catch (ArgumentException)
        {
            // The message is intentionally generic and never echoes the submitted values — an RTSP
            // URL may embed credentials, and an error response must not become the leak (FS-02 §11).
            return BadRequest(ApiResponse.Fail("VALIDATION_ERROR", "The request is invalid."));
        }

        var dto = BranchResponseDto.ForCreate(result.Branch, result.PlaintextActivationKey);

        // 201 with a Location pointing at the branch's detail resource. The envelope filter wraps the
        // body in the standard {success, message, data} envelope automatically (IP-01 §11).
        return Created($"/api/v1/branches/{result.Branch.BranchId}", dto);
    }

    // FS-02 §10.3: all branches with their cameras and device summary. Never carries the plaintext
    // Activation Key (ForRead leaves it null) or the internal DeviceRecordId.
    [HttpGet]
    public async Task<IActionResult> List(CancellationToken cancellationToken)
    {
        var branches = await _branchService.ListBranchesAsync(cancellationToken);
        var dtos = branches.Select(BranchResponseDto.ForRead).ToList();

        return Ok(dtos);
    }

    // FS-02 §10.3: a single branch by id, or 404 when none matches.
    [HttpGet("{id:guid}")]
    public async Task<IActionResult> GetById(Guid id, CancellationToken cancellationToken)
    {
        var branch = await _branchService.GetBranchAsync(id, cancellationToken);
        if (branch is null)
        {
            return NotFound(ApiResponse.Fail("NOT_FOUND", "Branch not found."));
        }

        return Ok(BranchResponseDto.ForRead(branch));
    }

    // FS-03 §10.1 (IP-03 T-43): edits a branch and reconciles its cameras in one transaction. Like
    // every action here it is protected by the fallback Admin JWT + active-session policy (this
    // controller carries no [AllowAnonymous]). All orchestration and the transactional guarantee live
    // in IBranchService.UpdateBranchAsync; this action only translates the typed result to HTTP.
    //
    // The response is the same safe read shape as GET — BranchResponseDto.ForRead — so RTSP
    // redaction and the no-key/no-secret/no-DeviceRecordId guarantees come for free (FS-03 §7.1,
    // §12). The Device and Activation Key records are never touched by the update path.
    [HttpPut("{branchId:guid}")]
    public async Task<IActionResult> Update(
        Guid branchId,
        [FromBody] UpdateBranchRequestDto request,
        CancellationToken cancellationToken)
    {
        // Presence/length and the "at least one camera" rule are already enforced by DataAnnotations
        // (a blank/oversized field or empty collection is a 400 before this runs). RTSP-URL format
        // and the camera-identity rules (unknown/foreign/duplicate CameraId) are Application-layer
        // checks that surface as an Invalid result below.
        var appRequest = new UpdateBranchRequest(
            branchId,
            request.Name,
            request.Address,
            request.ContactDetails,
            request.Cameras
                // A null element passes model validation; preserve it so the service's own guard
                // rejects it rather than this mapping throwing an NRE.
                .Select(c => c is null ? null! : new CameraMutation(c.CameraId, c.Name, c.RtspUrl))
                .ToList());

        var result = await _branchService.UpdateBranchAsync(appRequest, cancellationToken);

        return result.Status switch
        {
            BranchUpdateStatus.Updated => Ok(BranchResponseDto.ForRead(result.Branch!)),
            BranchUpdateStatus.NotFound =>
                NotFound(ApiResponse.Fail("NOT_FOUND", "Branch not found.")),
            // Every business-validation failure — invalid RTSP, unknown/foreign/duplicate CameraId —
            // collapses to one generic 400 that never echoes a submitted value (FS-03 §6, §12). The
            // message is fixed and identical to the create path's.
            _ => BadRequest(ApiResponse.Fail("VALIDATION_ERROR", "The request is invalid.")),
        };
    }
}

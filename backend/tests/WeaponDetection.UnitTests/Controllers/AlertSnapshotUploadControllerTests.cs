using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using WeaponDetection.Api.Contracts;
using WeaponDetection.Api.Controllers;
using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.UnitTests.Controllers;

// Controller-level unit tests for POST /api/v1/alerts/{alertId}/snapshot (FS-08 §9, IP-10 T-154).
// Mirrors SyncEventsControllerTests' stub-interface style exactly: the controller is exercised
// directly, with a stub IDeviceCredentialValidator/IAlertSnapshotUploadService standing in for the
// real, database/filesystem-backed implementations (those are covered by the SQL Server integration
// tests). This isolates the controller's own responsibilities — header reading, the uniform 401,
// eventId parsing, and outcome-to-HTTP-status mapping — from AlertSnapshotUploadService's logic.
public class AlertSnapshotUploadControllerTests
{
    private static readonly Guid ValidDeviceId = Guid.NewGuid();
    private const string ValidSecret = "device-shared-secret-placeholder-AAAAAAAA";

    private sealed class StubDeviceCredentialValidator : IDeviceCredentialValidator
    {
        public Guid? ExpectedDeviceId { get; init; }
        public string? ExpectedSecret { get; init; }
        public Guid BranchId { get; init; } = Guid.NewGuid();
        public Guid DeviceRecordId { get; init; } = Guid.NewGuid();
        public DeviceCredentialValidationOutcome? ForcedOutcome { get; init; }

        public Task<DeviceCredentialValidationResult> ValidateAsync(
            Guid deviceId, string? presentedSecret, CancellationToken cancellationToken = default)
        {
            if (ForcedOutcome is { } forcedOutcome)
            {
                return Task.FromResult(DeviceCredentialValidationResult.Invalid(forcedOutcome));
            }

            var isValid = deviceId == ExpectedDeviceId && presentedSecret == ExpectedSecret;
            return Task.FromResult(isValid
                ? DeviceCredentialValidationResult.Valid(BranchId, DeviceRecordId)
                : DeviceCredentialValidationResult.Invalid(
                    DeviceCredentialValidationOutcome.SecretMismatch));
        }
    }

    private sealed class StubAlertSnapshotUploadService : IAlertSnapshotUploadService
    {
        public Func<SnapshotUploadRequest, SnapshotUploadOutcome>? Handler { get; init; }
        public SnapshotUploadRequest? ReceivedRequest { get; private set; }

        public Task<SnapshotUploadOutcome> UploadAsync(
            SnapshotUploadRequest request, CancellationToken cancellationToken = default)
        {
            ReceivedRequest = request;
            var outcome = Handler?.Invoke(request) ?? SnapshotUploadOutcome.Accepted("ref.jpg");
            return Task.FromResult(outcome);
        }
    }

    private static IFormFile MakeFile(byte[]? content = null, string contentType = "image/jpeg")
    {
        content ??= [0xFF, 0xD8, 0xFF, 0xD9];
        var stream = new MemoryStream(content);
        return new FormFile(stream, 0, content.Length, "file", "snapshot.jpg") { Headers = new HeaderDictionary(), ContentType = contentType };
    }

    private static AlertSnapshotUploadController CreateController(
        StubDeviceCredentialValidator validator,
        StubAlertSnapshotUploadService uploadService,
        Guid? deviceId,
        string? secret)
    {
        var controller = new AlertSnapshotUploadController(validator, uploadService)
        {
            ControllerContext = new ControllerContext { HttpContext = new DefaultHttpContext() },
        };

        if (deviceId is not null)
        {
            controller.Request.Headers["X-Device-Id"] = deviceId.Value.ToString();
        }

        if (secret is not null)
        {
            controller.Request.Headers["X-Device-Secret"] = secret;
        }

        return controller;
    }

    private static AlertSnapshotUploadRequestDto MakeRequest(Guid? eventId = null, IFormFile? file = null) => new()
    {
        File = file ?? MakeFile(),
        EventId = (eventId ?? Guid.NewGuid()).ToString(),
        Sha256 = null,
    };

    // --- Authentication (byte-identical to SyncEventsController's own uniform 401) ---

    [Fact]
    public async Task UploadSnapshot_MissingDeviceIdHeader_Returns401UniformFailure()
    {
        var validator = new StubDeviceCredentialValidator
        {
            ExpectedDeviceId = ValidDeviceId, ExpectedSecret = ValidSecret,
        };
        var controller = CreateController(
            validator, new StubAlertSnapshotUploadService(), deviceId: null, secret: ValidSecret);

        var result = await controller.UploadSnapshot(Guid.NewGuid(), MakeRequest(), CancellationToken.None);

        var unauthorized = Assert.IsType<UnauthorizedObjectResult>(result);
        var envelope = Assert.IsType<ApiResponse>(unauthorized.Value);
        Assert.False(envelope.Success);
        Assert.Equal("INVALID_DEVICE_CREDENTIALS", envelope.ErrorCode);
    }

    [Fact]
    public async Task UploadSnapshot_WrongSecret_Returns401UniformFailure()
    {
        var validator = new StubDeviceCredentialValidator
        {
            ExpectedDeviceId = ValidDeviceId, ExpectedSecret = ValidSecret,
        };
        var controller = CreateController(
            validator, new StubAlertSnapshotUploadService(), ValidDeviceId, "wrong-secret");

        var result = await controller.UploadSnapshot(Guid.NewGuid(), MakeRequest(), CancellationToken.None);

        Assert.IsType<UnauthorizedObjectResult>(result);
    }

    [Fact]
    public async Task UploadSnapshot_CredentialStorageUnavailable_Returns503_NotTheUniform401()
    {
        var validator = new StubDeviceCredentialValidator
        {
            ForcedOutcome = DeviceCredentialValidationOutcome.CredentialStorageUnavailable,
        };
        var controller = CreateController(
            validator, new StubAlertSnapshotUploadService(), ValidDeviceId, ValidSecret);

        var result = await controller.UploadSnapshot(Guid.NewGuid(), MakeRequest(), CancellationToken.None);

        var objectResult = Assert.IsType<ObjectResult>(result);
        Assert.Equal(StatusCodes.Status503ServiceUnavailable, objectResult.StatusCode);
        var envelope = Assert.IsType<ApiResponse>(objectResult.Value);
        Assert.Equal("DEVICE_AUTHENTICATION_UNAVAILABLE", envelope.ErrorCode);
    }

    // --- eventId parsing ---

    [Fact]
    public async Task UploadSnapshot_MalformedEventId_Returns400()
    {
        var validator = new StubDeviceCredentialValidator
        {
            ExpectedDeviceId = ValidDeviceId, ExpectedSecret = ValidSecret,
        };
        var controller = CreateController(validator, new StubAlertSnapshotUploadService(), ValidDeviceId, ValidSecret);
        var request = new AlertSnapshotUploadRequestDto { File = MakeFile(), EventId = "not-a-guid" };

        var result = await controller.UploadSnapshot(Guid.NewGuid(), request, CancellationToken.None);

        var badRequest = Assert.IsType<BadRequestObjectResult>(result);
        var envelope = Assert.IsType<ApiResponse>(badRequest.Value);
        Assert.False(envelope.Success);
    }

    // --- Outcome-to-HTTP-status mapping (FS-08 §9) ---

    [Fact]
    public async Task UploadSnapshot_Accepted_Returns201_WithSnapshotReference()
    {
        var validator = new StubDeviceCredentialValidator
        {
            ExpectedDeviceId = ValidDeviceId, ExpectedSecret = ValidSecret,
        };
        var uploadService = new StubAlertSnapshotUploadService
        {
            Handler = _ => SnapshotUploadOutcome.Accepted("11111111-1111-1111-1111-111111111111.jpg"),
        };
        var controller = CreateController(validator, uploadService, ValidDeviceId, ValidSecret);

        var result = await controller.UploadSnapshot(Guid.NewGuid(), MakeRequest(), CancellationToken.None);

        var objectResult = Assert.IsType<ObjectResult>(result);
        Assert.Equal(StatusCodes.Status201Created, objectResult.StatusCode);
        var envelope = Assert.IsType<ApiResponse>(objectResult.Value);
        var data = Assert.IsType<AlertSnapshotUploadResponseDto>(envelope.Data);
        Assert.Equal("accepted", data.Outcome);
        Assert.Equal("11111111-1111-1111-1111-111111111111.jpg", data.SnapshotReference);
    }

    [Fact]
    public async Task UploadSnapshot_Duplicate_Returns200()
    {
        var validator = new StubDeviceCredentialValidator
        {
            ExpectedDeviceId = ValidDeviceId, ExpectedSecret = ValidSecret,
        };
        var uploadService = new StubAlertSnapshotUploadService
        {
            Handler = _ => SnapshotUploadOutcome.Duplicate("ref.jpg"),
        };
        var controller = CreateController(validator, uploadService, ValidDeviceId, ValidSecret);

        var result = await controller.UploadSnapshot(Guid.NewGuid(), MakeRequest(), CancellationToken.None);

        var ok = Assert.IsType<OkObjectResult>(result);
        var envelope = Assert.IsType<ApiResponse>(ok.Value);
        var data = Assert.IsType<AlertSnapshotUploadResponseDto>(envelope.Data);
        Assert.Equal("duplicate", data.Outcome);
    }

    [Theory]
    [InlineData(SnapshotUploadOutcomeKind.AlertNotOwnedOrFound, 404)]
    [InlineData(SnapshotUploadOutcomeKind.EventIdMismatch, 400)]
    [InlineData(SnapshotUploadOutcomeKind.MissingFile, 400)]
    [InlineData(SnapshotUploadOutcomeKind.MalformedImage, 400)]
    [InlineData(SnapshotUploadOutcomeKind.Oversized, 413)]
    [InlineData(SnapshotUploadOutcomeKind.UnsupportedMediaType, 415)]
    [InlineData(SnapshotUploadOutcomeKind.Conflict, 409)]
    public async Task UploadSnapshot_RejectionKinds_MapToTheApprovedHttpStatus(
        SnapshotUploadOutcomeKind kind, int expectedStatus)
    {
        var validator = new StubDeviceCredentialValidator
        {
            ExpectedDeviceId = ValidDeviceId, ExpectedSecret = ValidSecret,
        };
        var uploadService = new StubAlertSnapshotUploadService
        {
            Handler = _ => SnapshotUploadOutcome.Rejected(kind, "SOME_CODE", "Some message."),
        };
        var controller = CreateController(validator, uploadService, ValidDeviceId, ValidSecret);

        var result = await controller.UploadSnapshot(Guid.NewGuid(), MakeRequest(), CancellationToken.None);

        var statusCode = result switch
        {
            NotFoundObjectResult => StatusCodes.Status404NotFound,
            BadRequestObjectResult => StatusCodes.Status400BadRequest,
            ConflictObjectResult => StatusCodes.Status409Conflict,
            ObjectResult objectResult => objectResult.StatusCode,
            _ => null,
        };

        Assert.Equal(expectedStatus, statusCode);
    }

    [Fact]
    public async Task UploadSnapshot_ValidRequest_DelegatesTheAuthenticatedDevicesBranchIdToTheService()
    {
        var branchId = Guid.NewGuid();
        var validator = new StubDeviceCredentialValidator
        {
            ExpectedDeviceId = ValidDeviceId, ExpectedSecret = ValidSecret, BranchId = branchId,
        };
        var uploadService = new StubAlertSnapshotUploadService();
        var controller = CreateController(validator, uploadService, ValidDeviceId, ValidSecret);
        var alertId = Guid.NewGuid();

        await controller.UploadSnapshot(alertId, MakeRequest(), CancellationToken.None);

        Assert.Equal(alertId, uploadService.ReceivedRequest!.AlertId);
        Assert.Equal(branchId, uploadService.ReceivedRequest.BranchId);
    }
}

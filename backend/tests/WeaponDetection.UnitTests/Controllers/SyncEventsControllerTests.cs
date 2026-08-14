using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using WeaponDetection.Api.Contracts;
using WeaponDetection.Api.Controllers;
using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.UnitTests.Controllers;

// Controller-level unit tests for POST /api/v1/sync/events (FS-06 §4, IP-08 T-99/T-101). Mirrors
// DeviceControllerTests's stub-interface style: the controller is exercised directly, with a stub
// IDeviceCredentialValidator/IAlertSyncService standing in for the real, database-backed
// implementations (those are covered by the SQL Server integration tests). This isolates the
// controller's own responsibilities — header reading, the uniform 401, batch-size bounding, and
// outcome-to-DTO mapping — from AlertSyncService's persistence logic.
public class SyncEventsControllerTests
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

    private sealed class StubAlertSyncService : IAlertSyncService
    {
        public Func<Guid, Guid, IReadOnlyList<DetectionEventSyncItem>, IReadOnlyList<SyncEventOutcome>>?
            Handler
        { get; init; }

        public Guid? ReceivedDeviceId { get; private set; }
        public Guid? ReceivedBranchId { get; private set; }
        public IReadOnlyList<DetectionEventSyncItem>? ReceivedEvents { get; private set; }

        public Task<IReadOnlyList<SyncEventOutcome>> SyncEventsAsync(
            Guid deviceId,
            Guid branchId,
            IReadOnlyList<DetectionEventSyncItem> events,
            CancellationToken cancellationToken = default)
        {
            ReceivedDeviceId = deviceId;
            ReceivedBranchId = branchId;
            ReceivedEvents = events;

            var outcomes = Handler?.Invoke(deviceId, branchId, events)
                ?? events.Select(e => SyncEventOutcome.Accepted(e.EventId, Guid.NewGuid())).ToList();
            return Task.FromResult(outcomes);
        }
    }

    private static DetectionEventDto MakeEvent(Guid? eventId = null) => new(
        eventId ?? Guid.NewGuid(),
        "camera1",
        DetectedAtUtc: new DateTime(2026, 7, 24, 18, 30, 0, DateTimeKind.Utc),
        CreatedAtUtc: new DateTime(2026, 7, 24, 18, 30, 0, 150, DateTimeKind.Utc),
        ClassId: 0,
        ClassName: "gun",
        Confidence: 0.91,
        SourceId: 0,
        FrameNumber: 12345,
        FrameWidth: 1280,
        FrameHeight: 720,
        BoundingBox: new BoundingBoxDto(420.0, 180.0, 250.0, 190.0));

    private static SyncEventsController CreateController(
        StubDeviceCredentialValidator validator, StubAlertSyncService alertSyncService, Guid? deviceId, string? secret)
    {
        var controller = new SyncEventsController(validator, alertSyncService)
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

    // --- Authentication (FS-06 §6.1: byte-identical to the existing validation endpoint's 401) ---

    [Fact]
    public async Task SyncEvents_MissingDeviceIdHeader_Returns401UniformFailure()
    {
        var validator = new StubDeviceCredentialValidator
        {
            ExpectedDeviceId = ValidDeviceId, ExpectedSecret = ValidSecret,
        };
        var controller = CreateController(
            validator, new StubAlertSyncService(), deviceId: null, secret: ValidSecret);

        var result = await controller.SyncEvents(
            new SyncEventsRequest([MakeEvent()]), CancellationToken.None);

        var unauthorized = Assert.IsType<UnauthorizedObjectResult>(result);
        var envelope = Assert.IsType<ApiResponse>(unauthorized.Value);
        Assert.False(envelope.Success);
        Assert.Equal("INVALID_DEVICE_CREDENTIALS", envelope.ErrorCode);
        Assert.Equal("The device credentials are invalid.", envelope.Message);
    }

    [Fact]
    public async Task SyncEvents_WrongSecret_Returns401UniformFailure_AndNeverEchoesTheSecret()
    {
        var validator = new StubDeviceCredentialValidator
        {
            ExpectedDeviceId = ValidDeviceId, ExpectedSecret = ValidSecret,
        };
        const string wrongSecret = "device-shared-secret-placeholder-BBBBBBBB";
        var controller = CreateController(
            validator, new StubAlertSyncService(), deviceId: ValidDeviceId, secret: wrongSecret);

        var result = await controller.SyncEvents(
            new SyncEventsRequest([MakeEvent()]), CancellationToken.None);

        var unauthorized = Assert.IsType<UnauthorizedObjectResult>(result);
        var envelope = Assert.IsType<ApiResponse>(unauthorized.Value);
        Assert.Equal("INVALID_DEVICE_CREDENTIALS", envelope.ErrorCode);
        Assert.DoesNotContain(wrongSecret, envelope.Message ?? string.Empty);
        Assert.Null(envelope.Data);
    }

    [Fact]
    public async Task SyncEvents_MalformedDeviceIdHeader_TreatedAsGuidEmpty_Returns401()
    {
        var validator = new StubDeviceCredentialValidator
        {
            ExpectedDeviceId = ValidDeviceId, ExpectedSecret = ValidSecret,
        };
        var controller = CreateController(
            validator, new StubAlertSyncService(), deviceId: null, secret: ValidSecret);
        controller.Request.Headers["X-Device-Id"] = "not-a-guid";

        var result = await controller.SyncEvents(
            new SyncEventsRequest([MakeEvent()]), CancellationToken.None);

        Assert.IsType<UnauthorizedObjectResult>(result);
    }

    // --- Credential storage unavailable (FS-07 §3.4: 503, never the confirmed-revocation 401) ---

    [Fact]
    public async Task SyncEvents_CredentialStorageUnavailable_Returns503_NotTheUniform401()
    {
        var validator = new StubDeviceCredentialValidator
        {
            ForcedOutcome = DeviceCredentialValidationOutcome.CredentialStorageUnavailable,
        };
        var controller = CreateController(
            validator, new StubAlertSyncService(), ValidDeviceId, ValidSecret);

        var result = await controller.SyncEvents(
            new SyncEventsRequest([MakeEvent()]), CancellationToken.None);

        var objectResult = Assert.IsType<ObjectResult>(result);
        Assert.Equal(StatusCodes.Status503ServiceUnavailable, objectResult.StatusCode);
        var envelope = Assert.IsType<ApiResponse>(objectResult.Value);
        Assert.False(envelope.Success);
        Assert.Equal("DEVICE_AUTHENTICATION_UNAVAILABLE", envelope.ErrorCode);
    }

    // --- Batch bounding (FS-06 §4.2: 400 for structurally invalid, 413 for over-cap) ---

    [Fact]
    public async Task SyncEvents_EmptyEventsList_Returns400()
    {
        var validator = new StubDeviceCredentialValidator
        {
            ExpectedDeviceId = ValidDeviceId, ExpectedSecret = ValidSecret,
        };
        var controller = CreateController(
            validator, new StubAlertSyncService(), ValidDeviceId, ValidSecret);

        var result = await controller.SyncEvents(
            new SyncEventsRequest([]), CancellationToken.None);

        var badRequest = Assert.IsType<BadRequestObjectResult>(result);
        var envelope = Assert.IsType<ApiResponse>(badRequest.Value);
        Assert.False(envelope.Success);
    }

    [Fact]
    public async Task SyncEvents_NullEventsList_Returns400()
    {
        var validator = new StubDeviceCredentialValidator
        {
            ExpectedDeviceId = ValidDeviceId, ExpectedSecret = ValidSecret,
        };
        var controller = CreateController(
            validator, new StubAlertSyncService(), ValidDeviceId, ValidSecret);

        var result = await controller.SyncEvents(
            new SyncEventsRequest(null), CancellationToken.None);

        Assert.IsType<BadRequestObjectResult>(result);
    }

    [Fact]
    public async Task SyncEvents_BatchExceedsMaxSize_Returns413()
    {
        var validator = new StubDeviceCredentialValidator
        {
            ExpectedDeviceId = ValidDeviceId, ExpectedSecret = ValidSecret,
        };
        var controller = CreateController(
            validator, new StubAlertSyncService(), ValidDeviceId, ValidSecret);
        var events = Enumerable.Range(0, SyncEventsController.MaxBatchSize + 1)
            .Select(_ => MakeEvent()).ToList();

        var result = await controller.SyncEvents(new SyncEventsRequest(events), CancellationToken.None);

        var objectResult = Assert.IsType<ObjectResult>(result);
        Assert.Equal(StatusCodes.Status413PayloadTooLarge, objectResult.StatusCode);
    }

    [Fact]
    public async Task SyncEvents_BatchAtExactlyMaxSize_IsAccepted()
    {
        var validator = new StubDeviceCredentialValidator
        {
            ExpectedDeviceId = ValidDeviceId, ExpectedSecret = ValidSecret,
        };
        var alertSyncService = new StubAlertSyncService();
        var controller = CreateController(validator, alertSyncService, ValidDeviceId, ValidSecret);
        var events = Enumerable.Range(0, SyncEventsController.MaxBatchSize)
            .Select(_ => MakeEvent()).ToList();

        var result = await controller.SyncEvents(new SyncEventsRequest(events), CancellationToken.None);

        Assert.IsType<OkObjectResult>(result);
    }

    // --- Delegation and mapping (FS-06 §4.2, §6.2) ---

    [Fact]
    public async Task SyncEvents_ValidRequest_DelegatesAuthenticatedDeviceAndBranchToTheService()
    {
        var branchId = Guid.NewGuid();
        var validator = new StubDeviceCredentialValidator
        {
            ExpectedDeviceId = ValidDeviceId, ExpectedSecret = ValidSecret, BranchId = branchId,
        };
        var alertSyncService = new StubAlertSyncService();
        var controller = CreateController(validator, alertSyncService, ValidDeviceId, ValidSecret);

        await controller.SyncEvents(new SyncEventsRequest([MakeEvent()]), CancellationToken.None);

        Assert.Equal(ValidDeviceId, alertSyncService.ReceivedDeviceId);
        Assert.Equal(branchId, alertSyncService.ReceivedBranchId);
        Assert.Single(alertSyncService.ReceivedEvents!);
    }

    [Fact]
    public async Task SyncEvents_MixedOutcomes_MapToTheApprovedWireStrings()
    {
        var acceptedId = Guid.NewGuid();
        var duplicateId = Guid.NewGuid();
        var rejectedId = Guid.NewGuid();
        var acceptedAlertId = Guid.NewGuid();
        var duplicateAlertId = Guid.NewGuid();

        var validator = new StubDeviceCredentialValidator
        {
            ExpectedDeviceId = ValidDeviceId, ExpectedSecret = ValidSecret,
        };
        var alertSyncService = new StubAlertSyncService
        {
            Handler = (_, _, _) =>
            [
                SyncEventOutcome.Accepted(acceptedId, acceptedAlertId),
                SyncEventOutcome.Duplicate(duplicateId, duplicateAlertId),
                SyncEventOutcome.Rejected(rejectedId, SyncEventErrorCodes.UnknownCamera),
            ],
        };
        var controller = CreateController(validator, alertSyncService, ValidDeviceId, ValidSecret);
        var events = new SyncEventsRequest([
            MakeEvent(acceptedId), MakeEvent(duplicateId), MakeEvent(rejectedId),
        ]);

        var result = await controller.SyncEvents(events, CancellationToken.None);

        var ok = Assert.IsType<OkObjectResult>(result);
        var response = Assert.IsType<SyncEventsResponse>(ok.Value);
        Assert.Equal(3, response.Results.Count);

        var accepted = response.Results.Single(r => r.EventId == acceptedId);
        Assert.Equal("accepted", accepted.Outcome);
        Assert.Equal(acceptedAlertId, accepted.AlertId);
        Assert.Null(accepted.ErrorCode);

        var duplicate = response.Results.Single(r => r.EventId == duplicateId);
        Assert.Equal("duplicate", duplicate.Outcome);
        Assert.Equal(duplicateAlertId, duplicate.AlertId);
        Assert.Null(duplicate.ErrorCode);

        var rejected = response.Results.Single(r => r.EventId == rejectedId);
        Assert.Equal("rejected", rejected.Outcome);
        Assert.Null(rejected.AlertId);
        Assert.Equal("UNKNOWN_CAMERA", rejected.ErrorCode);
    }
}

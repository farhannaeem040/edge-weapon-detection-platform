using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using WeaponDetection.Api.Contracts;
using WeaponDetection.Api.Controllers;
using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.UnitTests.Controllers;

// FS-10 §9.2/§9.3, IP-12 T-198. Controller-layer tests against a stub IAlertQueryService — no HTTP
// pipeline, no database. Mirrors DeviceControllerTests' style.
public class AlertControllerTests
{
    private sealed class StubAlertQueryService : IAlertQueryService
    {
        public Func<AlertListQuery, CancellationToken, Task<AlertPageResult>>? ListHandler { get; init; }
        public Func<Guid, CancellationToken, Task<AlertDetailView?>>? GetHandler { get; init; }

        public Task<AlertPageResult> ListAlertsAsync(
            AlertListQuery query, CancellationToken cancellationToken = default) =>
            ListHandler is null
                ? throw new NotSupportedException()
                : ListHandler(query, cancellationToken);

        public Task<AlertDetailView?> GetAlertAsync(
            Guid alertId, CancellationToken cancellationToken = default) =>
            GetHandler is null
                ? throw new NotSupportedException()
                : GetHandler(alertId, cancellationToken);
    }

    private sealed class StubAlertSnapshotRetrievalService : IAlertSnapshotRetrievalService
    {
        public Func<Guid, SnapshotRetrievalOutcome>? Handler { get; init; }

        public Task<SnapshotRetrievalOutcome> GetSnapshotAsync(
            Guid alertId, CancellationToken cancellationToken = default) =>
            Task.FromResult(Handler?.Invoke(alertId) ?? SnapshotRetrievalOutcome.NotFound());
    }

    private static AlertController CreateController(
        IAlertQueryService? queryService = null, IAlertSnapshotRetrievalService? snapshotService = null) =>
        new(queryService ?? new StubAlertQueryService(), snapshotService ?? new StubAlertSnapshotRetrievalService());

    private static AlertDetailView MakeDetail(Guid alertId) =>
        new(
            alertId,
            Guid.NewGuid(),
            DateTime.UtcNow,
            DateTime.UtcNow,
            0,
            "gun",
            0.9,
            Guid.NewGuid(),
            "LJMU Branch",
            Guid.NewGuid(),
            "camera1",
            Guid.NewGuid(),
            "New",
            false);

    [Fact]
    public async Task GetById_WhenUnknown_Returns404WithNotFoundContract()
    {
        var service = new StubAlertQueryService
        {
            GetHandler = (_, _) => Task.FromResult<AlertDetailView?>(null),
        };
        var controller = CreateController(queryService: service);

        var result = await controller.GetById(Guid.NewGuid(), CancellationToken.None);

        var notFound = Assert.IsType<NotFoundObjectResult>(result);
        var envelope = Assert.IsType<ApiResponse>(notFound.Value);
        Assert.False(envelope.Success);
        Assert.Equal("NOT_FOUND", envelope.ErrorCode);
    }

    [Fact]
    public async Task GetById_WhenFound_ReturnsOkWithDetailDto()
    {
        var alertId = Guid.NewGuid();
        var service = new StubAlertQueryService
        {
            GetHandler = (_, _) => Task.FromResult<AlertDetailView?>(MakeDetail(alertId)),
        };
        var controller = CreateController(queryService: service);

        var result = await controller.GetById(alertId, CancellationToken.None);

        var ok = Assert.IsType<OkObjectResult>(result);
        var dto = Assert.IsType<AlertDetailDto>(ok.Value);
        Assert.Equal(alertId, dto.AlertId);
    }

    [Fact]
    public async Task GetById_DetailDto_NeverExposesDeviceRecordIdOrInternalFields()
    {
        // AlertDetailDto's own property list is the contract; this asserts by construction that no
        // extra reflection-visible member carries anything beyond the documented safe fields.
        var properties = typeof(AlertDetailDto).GetProperties().Select(p => p.Name).ToHashSet();
        Assert.DoesNotContain("DeviceRecordId", properties);
        Assert.DoesNotContain("SnapshotReference", properties);
        Assert.DoesNotContain("ProtectedSharedSecret", properties);
    }

    [Fact]
    public async Task List_WhenPageSizeExceedsMaximum_Returns400ValidationError()
    {
        var service = new StubAlertQueryService();
        var controller = CreateController(queryService: service);
        var request = new AlertListRequestDto(
            1, AlertListQuery.MaxPageSize + 1, null, null, null, null, null, null, null, null, null);

        var result = await controller.List(request, CancellationToken.None);

        var badRequest = Assert.IsType<BadRequestObjectResult>(result);
        var envelope = Assert.IsType<ApiResponse>(badRequest.Value);
        Assert.Equal("VALIDATION_ERROR", envelope.ErrorCode);
    }

    [Fact]
    public async Task List_WhenSortByIsInvalid_Returns400ValidationError()
    {
        var service = new StubAlertQueryService();
        var controller = CreateController(queryService: service);
        var request = new AlertListRequestDto(
            null, null, null, null, null, null, null, null, null, "totallyNotAField", null);

        var result = await controller.List(request, CancellationToken.None);

        var badRequest = Assert.IsType<BadRequestObjectResult>(result);
        var envelope = Assert.IsType<ApiResponse>(badRequest.Value);
        Assert.Equal("VALIDATION_ERROR", envelope.ErrorCode);
    }

    [Fact]
    public async Task List_WhenClassNameIsUnrecognized_Returns400ValidationError()
    {
        var service = new StubAlertQueryService();
        var controller = CreateController(queryService: service);
        var request = new AlertListRequestDto(
            null, null, null, null, "bazooka", null, null, null, null, null, null);

        var result = await controller.List(request, CancellationToken.None);

        var badRequest = Assert.IsType<BadRequestObjectResult>(result);
        var envelope = Assert.IsType<ApiResponse>(badRequest.Value);
        Assert.Equal("VALIDATION_ERROR", envelope.ErrorCode);
    }

    [Fact]
    public async Task List_WhenStatusIsUnrecognized_Returns400ValidationError()
    {
        var service = new StubAlertQueryService();
        var controller = CreateController(queryService: service);
        var request = new AlertListRequestDto(
            null, null, null, null, null, null, null, "Resolved", null, null, null);

        var result = await controller.List(request, CancellationToken.None);

        var badRequest = Assert.IsType<BadRequestObjectResult>(result);
        var envelope = Assert.IsType<ApiResponse>(badRequest.Value);
        Assert.Equal("VALIDATION_ERROR", envelope.ErrorCode);
    }

    [Fact]
    public async Task List_DefaultsToPageOneAndDetectedAtUtcDescending()
    {
        AlertListQuery? captured = null;
        var service = new StubAlertQueryService
        {
            ListHandler = (query, _) =>
            {
                captured = query;
                return Task.FromResult(new AlertPageResult([], 0, 1, AlertListQuery.DefaultPageSize));
            },
        };
        var controller = CreateController(queryService: service);
        var request = new AlertListRequestDto(
            null, null, null, null, null, null, null, null, null, null, null);

        await controller.List(request, CancellationToken.None);

        Assert.NotNull(captured);
        Assert.Equal(1, captured!.Page);
        Assert.Equal(AlertSortField.DetectedAtUtc, captured.SortBy);
        Assert.True(captured.SortDescending);
    }

    [Fact]
    public async Task List_ReturnsPagedResponseShape()
    {
        var alertId = Guid.NewGuid();
        var item = new AlertListItemView(
            alertId, DateTime.UtcNow, DateTime.UtcNow, "gun", 0.9,
            Guid.NewGuid(), "LJMU Branch", Guid.NewGuid(), "camera1", Guid.NewGuid(), "New", false);
        var service = new StubAlertQueryService
        {
            ListHandler = (_, _) => Task.FromResult(new AlertPageResult([item], 1, 1, 25)),
        };
        var controller = CreateController(queryService: service);
        var request = new AlertListRequestDto(
            null, null, null, null, null, null, null, null, null, null, null);

        var result = await controller.List(request, CancellationToken.None);

        var ok = Assert.IsType<OkObjectResult>(result);
        var dto = Assert.IsType<AlertListResponseDto>(ok.Value);
        Assert.Single(dto.Items);
        Assert.Equal(1, dto.TotalCount);
        Assert.Equal(1, dto.TotalPages);
    }

    // FS-08 §12, IP-10 T-161. Controller-layer tests for GET /api/v1/alerts/{id}/snapshot against a
    // stub IAlertSnapshotRetrievalService — HTTP-pipeline auth wiring is covered separately by
    // AlertSnapshotRetrievalApiTests (SQL Server integration tests).

    [Fact]
    public async Task GetSnapshot_WhenNotFound_Returns404WithNotFoundContract()
    {
        var snapshotService = new StubAlertSnapshotRetrievalService
        {
            Handler = _ => SnapshotRetrievalOutcome.NotFound(),
        };
        var controller = CreateController(snapshotService: snapshotService);

        var result = await controller.GetSnapshot(Guid.NewGuid(), CancellationToken.None);

        var notFound = Assert.IsType<NotFoundObjectResult>(result);
        var envelope = Assert.IsType<ApiResponse>(notFound.Value);
        Assert.False(envelope.Success);
        Assert.Equal("NOT_FOUND", envelope.ErrorCode);
    }

    [Fact]
    public async Task GetSnapshot_WhenFound_ReturnsFileWithContentType()
    {
        var bytes = new byte[] { 0xFF, 0xD8, 0xFF, 0xD9 };
        var snapshotService = new StubAlertSnapshotRetrievalService
        {
            Handler = _ => SnapshotRetrievalOutcome.Found(bytes, "image/jpeg"),
        };
        var controller = CreateController(snapshotService: snapshotService);
        controller.ControllerContext = new ControllerContext { HttpContext = new DefaultHttpContext() };

        var result = await controller.GetSnapshot(Guid.NewGuid(), CancellationToken.None);

        var fileResult = Assert.IsType<FileContentResult>(result);
        Assert.Equal("image/jpeg", fileResult.ContentType);
        Assert.Equal(bytes, fileResult.FileContents);
    }

    [Fact]
    public async Task GetSnapshot_WhenFound_SetsNoStorePrivateCacheControlHeader()
    {
        var snapshotService = new StubAlertSnapshotRetrievalService
        {
            Handler = _ => SnapshotRetrievalOutcome.Found([0xFF, 0xD8, 0xFF, 0xD9], "image/jpeg"),
        };
        var controller = CreateController(snapshotService: snapshotService);
        var httpContext = new DefaultHttpContext();
        controller.ControllerContext = new ControllerContext { HttpContext = httpContext };

        await controller.GetSnapshot(Guid.NewGuid(), CancellationToken.None);

        Assert.Equal("no-store, private", httpContext.Response.Headers.CacheControl.ToString());
    }
}

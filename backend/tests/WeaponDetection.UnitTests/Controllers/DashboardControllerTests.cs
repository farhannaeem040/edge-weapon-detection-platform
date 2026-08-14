using Microsoft.AspNetCore.Mvc;
using WeaponDetection.Api.Contracts;
using WeaponDetection.Api.Controllers;
using WeaponDetection.Application.Interfaces;

namespace WeaponDetection.UnitTests.Controllers;

// FS-10 §9.1, IP-12 T-198; extended by manual-review Correction 3 (explicit branchId is now required).
// Controller-layer tests against a stub IDashboardSummaryService.
public class DashboardControllerTests
{
    private sealed class StubDashboardSummaryService : IDashboardSummaryService
    {
        public Func<Guid, CancellationToken, Task<DashboardSummaryView?>>? Handler { get; init; }

        public Task<DashboardSummaryView?> GetSummaryAsync(
            Guid branchId, CancellationToken cancellationToken = default) =>
            Handler is null ? throw new NotSupportedException() : Handler(branchId, cancellationToken);
    }

    private static readonly Guid PlaceholderBranchId = Guid.NewGuid();

    private static DashboardSummaryView MakeSummary(Guid? branchId = null) =>
        new(
            branchId ?? PlaceholderBranchId,
            "LJMU Branch",
            "Europe/London",
            "2026-07-30",
            DateTime.UtcNow.AddHours(6),
            12,
            15,
            3,
            DateTime.UtcNow,
            47,
            40,
            7,
            1,
            1);

    [Fact]
    public async Task Summary_WithoutBranchId_Returns400()
    {
        var service = new StubDashboardSummaryService
        {
            Handler = (_, _) => throw new NotSupportedException("Must not be called without a branchId."),
        };
        var controller = new DashboardController(service);

        var result = await controller.Summary(branchId: null, CancellationToken.None);

        var badRequest = Assert.IsType<BadRequestObjectResult>(result);
        var envelope = Assert.IsType<ApiResponse>(badRequest.Value);
        Assert.False(envelope.Success);
        Assert.Equal("VALIDATION_ERROR", envelope.ErrorCode);
    }

    [Fact]
    public async Task Summary_WithEmptyGuidBranchId_Returns400()
    {
        var service = new StubDashboardSummaryService
        {
            Handler = (_, _) => throw new NotSupportedException("Must not be called with an empty guid."),
        };
        var controller = new DashboardController(service);

        var result = await controller.Summary(Guid.Empty, CancellationToken.None);

        var badRequest = Assert.IsType<BadRequestObjectResult>(result);
        var envelope = Assert.IsType<ApiResponse>(badRequest.Value);
        Assert.Equal("VALIDATION_ERROR", envelope.ErrorCode);
    }

    [Fact]
    public async Task Summary_WhenBranchDoesNotExist_Returns404()
    {
        var requestedId = Guid.NewGuid();
        var service = new StubDashboardSummaryService
        {
            Handler = (_, _) => Task.FromResult<DashboardSummaryView?>(null),
        };
        var controller = new DashboardController(service);

        var result = await controller.Summary(requestedId, CancellationToken.None);

        var notFound = Assert.IsType<NotFoundObjectResult>(result);
        var envelope = Assert.IsType<ApiResponse>(notFound.Value);
        Assert.False(envelope.Success);
        Assert.Equal("NOT_FOUND", envelope.ErrorCode);
    }

    [Fact]
    public async Task Summary_PassesTheRequestedBranchIdToTheService()
    {
        var requestedId = Guid.NewGuid();
        Guid? receivedId = null;
        var service = new StubDashboardSummaryService
        {
            Handler = (branchId, _) =>
            {
                receivedId = branchId;
                return Task.FromResult<DashboardSummaryView?>(MakeSummary(branchId));
            },
        };
        var controller = new DashboardController(service);

        await controller.Summary(requestedId, CancellationToken.None);

        Assert.Equal(requestedId, receivedId);
    }

    [Fact]
    public async Task Summary_ReturnsQuotaMaximumAndAcceptedCount()
    {
        var summary = MakeSummary();
        var service = new StubDashboardSummaryService
        {
            Handler = (_, _) => Task.FromResult<DashboardSummaryView?>(summary),
        };
        var controller = new DashboardController(service);

        var result = await controller.Summary(PlaceholderBranchId, CancellationToken.None);

        var ok = Assert.IsType<OkObjectResult>(result);
        var dto = Assert.IsType<DashboardSummaryDto>(ok.Value);
        Assert.Equal(summary.ConfiguredMaximum, dto.Alerts.ConfiguredMaximum);
        Assert.Equal(summary.AlertsToday, dto.Alerts.Today);
        Assert.Equal(summary.Remaining, dto.Alerts.Remaining);
    }

    [Fact]
    public async Task Summary_ReturnsSuppressedTotalAndClassCounts()
    {
        var summary = MakeSummary();
        var service = new StubDashboardSummaryService
        {
            Handler = (_, _) => Task.FromResult<DashboardSummaryView?>(summary),
        };
        var controller = new DashboardController(service);

        var result = await controller.Summary(PlaceholderBranchId, CancellationToken.None);

        var ok = Assert.IsType<OkObjectResult>(result);
        var dto = Assert.IsType<DashboardSummaryDto>(ok.Value);
        Assert.Equal(summary.SuppressedTotal, dto.Suppressions.Total);
        Assert.Equal(summary.SuppressedGun, dto.Suppressions.Gun);
        Assert.Equal(summary.SuppressedKnife, dto.Suppressions.Knife);
    }

    [Fact]
    public async Task Summary_ReturnsBranchLocalDateAndNextResetTimestamp()
    {
        var summary = MakeSummary();
        var service = new StubDashboardSummaryService
        {
            Handler = (_, _) => Task.FromResult<DashboardSummaryView?>(summary),
        };
        var controller = new DashboardController(service);

        var result = await controller.Summary(PlaceholderBranchId, CancellationToken.None);

        var ok = Assert.IsType<OkObjectResult>(result);
        var dto = Assert.IsType<DashboardSummaryDto>(ok.Value);
        Assert.Equal(summary.LocalDate, dto.Branch.LocalDate);
        Assert.Equal(summary.NextQuotaResetAtUtc, dto.Branch.NextQuotaResetAtUtc);
    }

    [Fact]
    public async Task Summary_ReturnsTheSelectedBranchIdentityInTheResponse()
    {
        var requestedId = Guid.NewGuid();
        var summary = MakeSummary(requestedId);
        var service = new StubDashboardSummaryService
        {
            Handler = (_, _) => Task.FromResult<DashboardSummaryView?>(summary),
        };
        var controller = new DashboardController(service);

        var result = await controller.Summary(requestedId, CancellationToken.None);

        var ok = Assert.IsType<OkObjectResult>(result);
        var dto = Assert.IsType<DashboardSummaryDto>(ok.Value);
        Assert.Equal(requestedId, dto.Branch.Id);
    }

    [Fact]
    public void SummaryDto_NeverExposesSecretsOrInternalFields()
    {
        var branchProperties = typeof(DashboardBranchDto).GetProperties().Select(p => p.Name).ToHashSet();
        Assert.DoesNotContain("ProtectedSharedSecret", branchProperties);
        Assert.DoesNotContain("ActivationKey", branchProperties);
    }
}

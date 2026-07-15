using Microsoft.EntityFrameworkCore;
using WeaponDetection.Application.Interfaces;
using WeaponDetection.Infrastructure.Persistence;
using WeaponDetection.Infrastructure.Services;
using Xunit;

namespace WeaponDetection.UnitTests.Services;

// Unit tests for BranchService's request validation (FS-02 §12/§13, IP-01 T-15). These assert the
// "rejected before persistence" guarantee without a database: BranchService validates the request
// and constructs every entity before it opens a transaction, so an invalid request never reaches
// the DbContext. Each test proves that by using a recording IDeviceService stub — provisioning is
// the step immediately before persistence, so if it was never called, nothing was persisted — and
// by backing the service with a DbContext whose connection is never opened (a placeholder
// connection string, exactly as the model-metadata tests use). The transactional persistence of a
// *valid* request is covered separately by BranchServiceTests against a real SQL Server (IP-01 §9).
public class BranchServiceValidationTests
{
    private const string PlaceholderConnectionString =
        "Server=localhost;Database=WeaponDetectionBranchServiceValidationTests;" +
        "Trusted_Connection=True;TrustServerCertificate=True;";

    // Records whether provisioning was reached. Provisioning is pure and precedes the transaction,
    // so "not called" is a proxy for "no database work occurred".
    private sealed class RecordingDeviceService : IDeviceService
    {
        public bool WasCalled { get; private set; }

        public DeviceProvisioning ProvisionForBranch(Guid branchId)
        {
            WasCalled = true;
            throw new InvalidOperationException(
                "Provisioning must not be reached for an invalid branch-creation request.");
        }
    }

    private static WeaponDetectionDbContext CreateContext()
    {
        var options = new DbContextOptionsBuilder<WeaponDetectionDbContext>()
            .UseSqlServer(PlaceholderConnectionString)
            .Options;

        return new WeaponDetectionDbContext(options);
    }

    private static NewCameraRequest ValidCamera() =>
        new("Front Entrance", "rtsp://camera.example.local:554/stream1");

    private static NewBranchRequest ValidRequestWith(params NewCameraRequest[] cameras) =>
        new("Downtown Branch", "1 High Street", "ops@example.local", cameras);

    private static async Task AssertRejectedBeforePersistenceAsync<TException>(NewBranchRequest request)
        where TException : Exception
    {
        var deviceService = new RecordingDeviceService();
        using var context = CreateContext();
        var service = new BranchService(context, deviceService);

        await Assert.ThrowsAsync<TException>(() => service.CreateBranchAsync(request));

        Assert.False(deviceService.WasCalled);
    }

    [Fact]
    public async Task CreateBranchAsync_NullRequest_ThrowsBeforePersistence()
    {
        await AssertRejectedBeforePersistenceAsync<ArgumentNullException>(null!);
    }

    [Fact]
    public async Task CreateBranchAsync_NoCameras_ThrowsBeforePersistence()
    {
        await AssertRejectedBeforePersistenceAsync<ArgumentException>(ValidRequestWith());
    }

    [Fact]
    public async Task CreateBranchAsync_NullCameraCollection_ThrowsBeforePersistence()
    {
        var request = new NewBranchRequest("Downtown Branch", "1 High Street", "ops@example.local", null!);

        await AssertRejectedBeforePersistenceAsync<ArgumentException>(request);
    }

    [Fact]
    public async Task CreateBranchAsync_NullCameraElement_ThrowsBeforePersistence()
    {
        var request = ValidRequestWith(ValidCamera(), null!);

        await AssertRejectedBeforePersistenceAsync<ArgumentException>(request);
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    public async Task CreateBranchAsync_BlankBranchName_ThrowsBeforePersistence(string name)
    {
        var request = new NewBranchRequest(name, "1 High Street", "ops@example.local", new[] { ValidCamera() });

        await AssertRejectedBeforePersistenceAsync<ArgumentException>(request);
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    public async Task CreateBranchAsync_BlankBranchAddress_ThrowsBeforePersistence(string address)
    {
        var request = new NewBranchRequest("Downtown Branch", address, "ops@example.local", new[] { ValidCamera() });

        await AssertRejectedBeforePersistenceAsync<ArgumentException>(request);
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    public async Task CreateBranchAsync_BlankContactDetails_ThrowsBeforePersistence(string contactDetails)
    {
        var request = new NewBranchRequest("Downtown Branch", "1 High Street", contactDetails, new[] { ValidCamera() });

        await AssertRejectedBeforePersistenceAsync<ArgumentException>(request);
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    public async Task CreateBranchAsync_BlankCameraName_ThrowsBeforePersistence(string cameraName)
    {
        var request = ValidRequestWith(new NewCameraRequest(cameraName, "rtsp://camera.example.local/stream"));

        await AssertRejectedBeforePersistenceAsync<ArgumentException>(request);
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    public async Task CreateBranchAsync_BlankCameraRtspUrl_ThrowsBeforePersistence(string rtspUrl)
    {
        var request = ValidRequestWith(new NewCameraRequest("Front Entrance", rtspUrl));

        await AssertRejectedBeforePersistenceAsync<ArgumentException>(request);
    }

    [Theory]
    [InlineData("http://camera.example.local/stream")]     // wrong scheme
    [InlineData("camera.example.local/stream")]            // not absolute, no scheme
    [InlineData("rtsp-camera.example.local")]              // no scheme delimiter
    [InlineData("not a url at all")]
    public async Task CreateBranchAsync_InvalidRtspUrl_ThrowsBeforePersistence(string rtspUrl)
    {
        var request = ValidRequestWith(new NewCameraRequest("Front Entrance", rtspUrl));

        await AssertRejectedBeforePersistenceAsync<ArgumentException>(request);
    }

    [Fact]
    public async Task CreateBranchAsync_OneInvalidCameraAmongValidOnes_ThrowsBeforePersistence()
    {
        var request = ValidRequestWith(
            ValidCamera(),
            new NewCameraRequest("Loading Bay", "http://camera.example.local/stream"));

        await AssertRejectedBeforePersistenceAsync<ArgumentException>(request);
    }
}

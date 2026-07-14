using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.TestHost;
using Microsoft.Extensions.DependencyInjection;
using WeaponDetection.IntegrationTests.Api.TestEndpoints;

namespace WeaponDetection.IntegrationTests.Api;

// The host used by the JWT + session-revocation middleware tests (IP-01 T-10) and by the logout
// tests (T-11), which need a protected route to replay a revoked token against. Identical to the
// production host in every respect that matters to authentication — same Program.cs, same
// pipeline, same real SQL Server database — with the test assembly added as an MVC application
// part so ProtectedTestController's placeholder routes are served alongside the real controllers.
//
// Each test class receives its own instance (xUnit IClassFixture), and therefore its own freshly
// migrated database, so one class's revoked sessions cannot influence another's.
public sealed class ProtectedEndpointApiFactory : SqlServerApiHostFactory
{
    public ProtectedEndpointApiFactory()
        : base("WeaponDetectionProtectedApiTests")
    {
    }

    protected override void ConfigureWebHost(IWebHostBuilder builder)
    {
        base.ConfigureWebHost(builder);

        // ConfigureTestServices runs after the application's own registrations, so this only adds
        // the extra application part — it does not re-register or override any authentication,
        // authorization, or MVC service the tests are meant to be exercising.
        builder.ConfigureTestServices(services =>
            services.AddControllers().AddApplicationPart(typeof(ProtectedTestController).Assembly));
    }
}

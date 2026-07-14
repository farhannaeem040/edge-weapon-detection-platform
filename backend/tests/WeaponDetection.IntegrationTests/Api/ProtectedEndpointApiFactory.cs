using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.TestHost;
using Microsoft.Extensions.DependencyInjection;
using WeaponDetection.IntegrationTests.Api.TestEndpoints;

namespace WeaponDetection.IntegrationTests.Api;

// The host used by the JWT + session-revocation middleware tests (IP-01 T-10). Identical to the
// production host in every respect that matters to authentication — same Program.cs, same
// pipeline, same real SQL Server database — with the test assembly added as an MVC application
// part so ProtectedTestController's placeholder routes are served alongside the real controllers.
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

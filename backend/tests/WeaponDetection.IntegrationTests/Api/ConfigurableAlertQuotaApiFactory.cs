using Microsoft.AspNetCore.Hosting;

namespace WeaponDetection.IntegrationTests.Api;

// A host whose Branch daily Alert quota maximum is set to a deliberately NON-default value, so the
// tests using it prove the limit actually comes from configuration rather than from the C# default
// of 15 (compose.yaml maps it to ALERT_QUOTA_MAXIMUM_PER_BRANCH_PER_DAY).
//
// The value is set here as the AlertQuota__MaximumPerBranchPerDay environment variable — the same
// binding path the deployed container uses — and cleared on dispose so it cannot leak into another
// factory's host in the same test run.
public sealed class ConfigurableAlertQuotaApiFactory : SqlServerApiHostFactory
{
    // Deliberately not 15 (the default) and not 1000 (the current POC value): the assertions must
    // follow configuration, not any hardcoded production number.
    public const int ConfiguredMaximum = 42;

    public ConfigurableAlertQuotaApiFactory()
        : base("WeaponDetectionConfigurableQuotaApiTests")
    {
    }

    protected override void ConfigureWebHost(IWebHostBuilder builder)
    {
        Environment.SetEnvironmentVariable(
            "AlertQuota__MaximumPerBranchPerDay", ConfiguredMaximum.ToString());
        base.ConfigureWebHost(builder);
    }

    protected override void Dispose(bool disposing)
    {
        Environment.SetEnvironmentVariable("AlertQuota__MaximumPerBranchPerDay", null);
        base.Dispose(disposing);
    }
}

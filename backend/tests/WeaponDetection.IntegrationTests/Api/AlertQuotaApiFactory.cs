namespace WeaponDetection.IntegrationTests.Api;

// The host used by the Branch daily Alert quota HTTP-endpoint tests (FS-09, IP-11 T-175/T-187/
// T-188/T-189). All host/database/environment setup lives in SqlServerApiHostFactory; this type
// only names its own database, so this class's Alerts/quota rows cannot influence another test
// class's. AlertQuota:Enabled/MaximumPerBranchPerDay are left at their C# defaults (true/15,
// AlertQuotaOptions) — no environment variable override needed, since that default is exactly the
// production policy this feature ships.
public sealed class AlertQuotaApiFactory : SqlServerApiHostFactory
{
    public AlertQuotaApiFactory()
        : base("WeaponDetectionAlertQuotaApiTests")
    {
    }
}

namespace WeaponDetection.IntegrationTests.Api;

// The host used by the dashboard-summary HTTP-endpoint tests (FS-10, IP-12 T-199). All host/database/
// environment setup lives in SqlServerApiHostFactory; this type only names its own database.
public sealed class DashboardApiFactory : SqlServerApiHostFactory
{
    public DashboardApiFactory()
        : base("WeaponDetectionDashboardApiTests")
    {
    }
}

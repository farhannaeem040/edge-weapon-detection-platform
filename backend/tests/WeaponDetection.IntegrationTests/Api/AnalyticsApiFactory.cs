namespace WeaponDetection.IntegrationTests.Api;

// The host used by the Operational Analytics endpoint tests (FS-15, IP-17 T-9). All host/database/
// environment setup lives in SqlServerApiHostFactory; this type only names its own database.
public sealed class AnalyticsApiFactory : SqlServerApiHostFactory
{
    public AnalyticsApiFactory()
        : base("WeaponDetectionAnalyticsApiTests")
    {
    }
}

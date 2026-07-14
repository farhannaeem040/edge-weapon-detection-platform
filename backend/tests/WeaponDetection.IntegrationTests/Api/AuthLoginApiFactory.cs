namespace WeaponDetection.IntegrationTests.Api;

// The host used by the POST /api/v1/auth/login tests (IP-01 T-09). All host/database/environment
// setup lives in SqlServerApiHostFactory; this type only names its own database.
public sealed class AuthLoginApiFactory : SqlServerApiHostFactory
{
    public AuthLoginApiFactory()
        : base("WeaponDetectionAuthApiTests")
    {
    }
}

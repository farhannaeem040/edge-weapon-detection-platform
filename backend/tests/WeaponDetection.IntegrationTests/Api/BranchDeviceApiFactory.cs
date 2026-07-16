namespace WeaponDetection.IntegrationTests.Api;

// The host used by the branch and device HTTP-endpoint tests (IP-01 T-16). All host/database/
// environment setup lives in SqlServerApiHostFactory; this type only names its own database. Each
// test class that takes it via IClassFixture gets its own instance, and therefore its own freshly
// migrated, empty database, so one class's branches cannot influence another's.
public sealed class BranchDeviceApiFactory : SqlServerApiHostFactory
{
    public BranchDeviceApiFactory()
        : base("WeaponDetectionBranchDeviceApiTests")
    {
    }
}

namespace WeaponDetection.IntegrationTests.Api;

// The host used by GET /api/v1/device/configuration tests (FS-11, IP-13 T-228). Own database, same
// pattern as AlertQuotaApiFactory — this class's Branches/Cameras/Devices cannot influence another
// test class's.
public sealed class DeviceConfigurationApiFactory : SqlServerApiHostFactory
{
    public DeviceConfigurationApiFactory()
        : base("WeaponDetectionDeviceConfigurationApiTests")
    {
    }
}

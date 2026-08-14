namespace WeaponDetection.IntegrationTests.Api;

// The host used by the snapshot-upload HTTP-endpoint tests (FS-08 §9, IP-10 T-156). All host/
// database/environment setup lives in SqlServerApiHostFactory; this type only names its own
// database, so this class's Alerts/snapshots cannot influence another test class's.
public sealed class AlertSnapshotUploadApiFactory : SqlServerApiHostFactory
{
    public AlertSnapshotUploadApiFactory()
        : base("WeaponDetectionAlertSnapshotUploadApiTests")
    {
    }
}

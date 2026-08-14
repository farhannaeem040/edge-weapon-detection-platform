namespace WeaponDetection.IntegrationTests.Api;

// The host used by the snapshot-retrieval HTTP-endpoint tests (FS-08 §12, IP-10 T-161). Its own
// named database, mirroring AlertSnapshotUploadApiFactory, so this class's Alerts/snapshots cannot
// influence another test class's.
public sealed class AlertSnapshotRetrievalApiFactory : SqlServerApiHostFactory
{
    public AlertSnapshotRetrievalApiFactory()
        : base("WeaponDetectionAlertSnapshotRetrievalApiTests")
    {
    }
}

namespace WeaponDetection.Infrastructure.Services;

// Bound from configuration section "AlertQuota" (AlertQuota:Enabled / AlertQuota:MaximumPerBranchPerDay
// — the equivalent AlertQuota__Enabled / AlertQuota__MaximumPerBranchPerDay environment variables),
// mirroring AlertSnapshotStorageOptions' shape (FS-09 §11). Enabled defaults true and
// MaximumPerBranchPerDay defaults 15 so an operator who never sets either env var still gets the
// task's default production policy; explicit configuration only needs to override one or the other.
public class AlertQuotaOptions
{
    public const string SectionName = "AlertQuota";

    public bool Enabled { get; set; } = true;

    public int MaximumPerBranchPerDay { get; set; } = 15;
}

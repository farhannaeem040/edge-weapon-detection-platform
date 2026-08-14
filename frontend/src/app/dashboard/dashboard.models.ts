/**
 * The read-side wire contract of `GET /api/v1/dashboard/summary` (FS-10 §9.1; IP-12 T-199).
 *
 * Transcribed field-for-field from the Backend's `DashboardSummaryDto` and its nested
 * `DashboardBranchDto`/`DashboardAlertsDto`/`DashboardSuppressionsDto`/`DashboardSystemDto`. This is a
 * read-only operational summary: nothing here is ever posted back, and no Device secret, Activation
 * Key, or Data Protection material is a member of any of these types on the Backend, so there is
 * nothing to omit here either.
 */

/** `summary.branch` — the Branch this summary is scoped to (FS-10 §7: single-Branch deployment). */
export interface DashboardBranch {
  id: string;
  name: string;

  /** Null when the Branch has no configured timezone; the Backend falls back to UTC (FS-09 §4). */
  timeZoneId?: string | null;

  /** The Branch-local calendar date (`yyyy-MM-dd`) the quota counters below apply to. */
  localDate: string;

  /** When the quota day rolls over, in UTC — the Backend's own boundary, never computed here. */
  nextQuotaResetAtUtc: string;
}

/** `summary.alerts` — today's Branch-day quota state (FS-09/IP-11). */
export interface DashboardAlerts {
  today: number;
  configuredMaximum: number;
  remaining: number;

  /** Null when no Alert has been recorded yet today. */
  latestAlertAtUtc?: string | null;
}

/** `summary.suppressions` — today's quota-suppressed detection counts (FS-09/IP-11). */
export interface DashboardSuppressions {
  total: number;
  gun: number;
  knife: number;
}

/** `summary.system` — Branch fleet counts. */
export interface DashboardSystem {
  deviceCount: number;
  cameraCount: number;
}

/** `data` of a successful `GET /api/v1/dashboard/summary` (backend `DashboardSummaryDto`). */
export interface DashboardSummary {
  branch: DashboardBranch;
  alerts: DashboardAlerts;
  suppressions: DashboardSuppressions;
  system: DashboardSystem;
}

/**
 * The read-side wire contract of `GET /api/v1/analytics/operational` (FS-15 §6.1; IP-17 T-11).
 *
 * Transcribed field-for-field from the Backend's `OperationalAnalyticsDto` and its nested types. This
 * is a read-only analytical projection: nothing here is ever posted back, and no Device secret,
 * Activation Key, RTSP URL, CameraKey, snapshot reference, or filesystem path is a member of any of
 * these types on the Backend, so there is nothing to omit here either.
 *
 * **Nullable members are `| null` and optional.** The Backend's envelope omits null members
 * (ARCH-001 §14.3 / ADR-009), so an unavailable metric arrives as `undefined` rather than `null`.
 * Every consumer must treat both identically — and must never substitute `0`, which would turn "no
 * data" into a measured claim (FS-15 §5.5, §7).
 *
 * There is deliberately **no** `accuracy`, `precision`, `confirmed`, `falsePositive`, or
 * `responseTime` member: the database contains no operator-validation ground truth and no
 * operator-response timestamp (FS-15 §3.4), so no field may claim either.
 */

/** The date-range presets offered by the filter bar (FS-15 §4.1). */
export type AnalyticsRange = 'last24h' | 'last7d' | 'last30d' | 'last90d';

/** The bucket granularity the Backend derived from the range — never chosen by the client. */
export type AnalyticsBucket = 'hour' | 'day' | 'week';

/** The detection taxonomy this platform actually detects (FS-15 §3.5). */
export type AnalyticsDetectionType = 'gun' | 'knife';

/** `filters` — the Backend's echo of exactly what was queried, so the page never has to trust its
 *  own idea of the window. `branchName` is resolved server-side from `branchId`. */
export interface AnalyticsFilters {
  range?: AnalyticsRange | null;
  fromUtc: string;
  toUtc: string;
  bucket: AnalyticsBucket;
  branchId?: string | null;
  branchName?: string | null;
  detectionType?: AnalyticsDetectionType | null;
}

/** `summary` — the scalar block (FS-15 §5.5). */
export interface AnalyticsSummary {
  /** Accepted, delivered detections persisted as Alerts. Excludes quota-suppressed detections. */
  totalDetections: number;

  /** Quota-suppressed detections in the same window — reported for context, never added above. */
  suppressedDetections: number;

  /** Mean `Alert.Confidence` (0–1). Absent when there are no detections — never 0. */
  meanConfidence?: number | null;

  confidenceHigh: number;
  confidenceMedium: number;
  confidenceLow: number;

  /** The band thresholds travel with the data so the legend cannot drift from the Backend. */
  highConfidenceThreshold: number;
  mediumConfidenceThreshold: number;

  /** Delivery latency = `ReceivedAtUtc − DetectedAtUtc`. Absent when no eligible sample exists. */
  averageDeliveryLatencyMs?: number | null;
  medianDeliveryLatencyMs?: number | null;
  maxDeliveryLatencyMs?: number | null;

  /** How many detections contributed a latency sample, and how many were excluded by FS-15 §5.4's
   *  eligibility rule (clock skew, or a store-and-forward backlog replay). Never hidden. */
  latencySampleCount: number;
  latencySamplesExcluded: number;

  alertsWithSnapshot: number;
  branchCount: number;
  cameraCount: number;
  deviceCount: number;

  /** Always `false` in this increment: no confirm/false-positive workflow exists (FS-15 §3.4). The
   *  confidence card reads this rather than inferring it. */
  validationDataAvailable: boolean;

  generatedAtUtc: string;
}

/** One bucket of the Detections Over Time series. Zero-count buckets are present, not omitted. */
export interface AnalyticsTimeBucket {
  periodStartUtc: string;
  count: number;
}

/** One bar of the Alert Density by Branch series. `branchName` comes from the database. */
export interface AnalyticsBranchCount {
  branchId: string;
  branchName: string;
  count: number;
}

/** One bucket of the delivery-latency series. `averageMs` is absent — never 0 — for a bucket with no
 *  eligible sample, so the chart draws a gap rather than a false "instant delivery". */
export interface AnalyticsLatencyBucket {
  periodStartUtc: string;
  averageMs?: number | null;
  sampleCount: number;
}

/** `data` of a successful `GET /api/v1/analytics/operational`. */
export interface OperationalAnalytics {
  filters: AnalyticsFilters;
  summary: AnalyticsSummary;
  detectionsOverTime: AnalyticsTimeBucket[];
  detectionsByBranch: AnalyticsBranchCount[];
  latencyOverTime: AnalyticsLatencyBucket[];
}

/** The page's shared filter state — the single set of values every card is driven by (FS-15 §4). */
export interface AnalyticsFilterState {
  range: AnalyticsRange;
  branchId?: string;
  detectionType?: AnalyticsDetectionType;
}

/** FS-15 §4.1: Last 30 days / All Branches / All detections. */
export function defaultAnalyticsFilterState(): AnalyticsFilterState {
  return { range: 'last30d' };
}

/** The presets offered by the filter bar, with their display labels. */
export const ANALYTICS_RANGE_OPTIONS: readonly { readonly value: AnalyticsRange; readonly label: string }[] = [
  { value: 'last24h', label: 'Last 24 hours' },
  { value: 'last7d', label: 'Last 7 days' },
  { value: 'last30d', label: 'Last 30 days' },
  { value: 'last90d', label: 'Last 90 days' },
];

/** The detection types offered by the filter bar — the platform's real taxonomy, nothing else. */
export const ANALYTICS_DETECTION_TYPE_OPTIONS: readonly {
  readonly value: AnalyticsDetectionType;
  readonly label: string;
}[] = [
  { value: 'gun', label: 'Gun' },
  { value: 'knife', label: 'Knife' },
];

/** Narrows an arbitrary query-param string to a supported range, falling back to the default. */
export function toAnalyticsRange(value: string | null): AnalyticsRange {
  return ANALYTICS_RANGE_OPTIONS.some((option) => option.value === value)
    ? (value as AnalyticsRange)
    : defaultAnalyticsFilterState().range;
}

/** Narrows an arbitrary query-param string to a supported detection type, or `undefined` for all. */
export function toAnalyticsDetectionType(value: string | null): AnalyticsDetectionType | undefined {
  return ANALYTICS_DETECTION_TYPE_OPTIONS.some((option) => option.value === value)
    ? (value as AnalyticsDetectionType)
    : undefined;
}

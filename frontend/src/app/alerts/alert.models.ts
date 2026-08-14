/**
 * The read-side wire contract of the Alert endpoints (FS-10 §9.2/§9.3; IP-12 T-199).
 *
 * Transcribed field-for-field from the Backend's `AlertListItemDto`/`AlertListResponseDto`/
 * `AlertDetailDto`. Deliberately absent: the raw `SnapshotReference` storage key (FS-08 §8) — only
 * the boolean `snapshotAvailable` ever reaches the browser — and any Device secret, `DeviceRecordId`,
 * or Data Protection material, none of which is a member of any of these Backend types either.
 */

/** The one Alert status this platform currently produces (`AlertStatus.New`, backend `Alert.cs`). */
export type AlertStatusValue = 'New';

/** One row in the paginated Alert list (`AlertListItemDto`). */
export interface AlertListItem {
  alertId: string;
  detectedAtUtc: string;
  receivedAtUtc: string;
  className: string;
  confidence: number;
  branchId: string;
  branchName: string;
  cameraId: string;
  cameraName: string;
  deviceId: string;
  status: string;
  snapshotAvailable: boolean;
}

/** `data` of a successful `GET /api/v1/alerts` (backend `AlertListResponseDto`). */
export interface AlertListResponse {
  items: AlertListItem[];
  page: number;
  pageSize: number;
  totalCount: number;
  totalPages: number;
}

/** `data` of a successful `GET /api/v1/alerts/{id}` (backend `AlertDetailDto`). */
export interface AlertDetail {
  alertId: string;
  eventId: string;
  detectedAtUtc: string;
  receivedAtUtc: string;
  deliveryLatencySeconds: number;
  classId: number;
  className: string;
  confidence: number;
  branchId: string;
  branchName: string;
  cameraId: string;
  cameraName: string;
  deviceId: string;
  status: string;
  snapshotAvailable: boolean;
}

/**
 * The two whitelisted sort fields (`AlertController`'s own whitelist — mirrored, not reinvented).
 * Any other value is rejected by the Backend, so the filter UI only ever offers these.
 */
export type AlertSortBy = 'detectedAtUtc' | 'receivedAtUtc';

/**
 * The list's filter/pagination state, round-tripped through the URL's query params (FS-10 §6) so a
 * refresh or the back button restores exactly what was on screen. Every field mirrors
 * `AlertListRequestDto`'s query-string contract; `page`/`pageSize`/`sortBy`/`sortDescending` always
 * have a value (the Backend's own defaults, mirrored here so the URL is always complete), while the
 * remaining filters are optional and, when absent, apply no filter at all.
 */
export interface AlertListFilter {
  page: number;
  pageSize: number;
  sortBy: AlertSortBy;
  sortDescending: boolean;
  fromUtc?: string;
  toUtc?: string;
  className?: string;
  branchId?: string;
  cameraId?: string;
  status?: string;
  snapshotAvailable?: boolean;
}

/** The Backend's own default page size (`AlertListQuery.DefaultPageSize`), mirrored for the URL. */
export const DEFAULT_PAGE_SIZE = 25;

/** The Backend's own page-size ceiling (`AlertListQuery.MaxPageSize`). */
export const MAX_PAGE_SIZE = 100;

/** The default filter: newest-first by detection time, page 1, no filters applied (FS-10 §6). */
export function defaultAlertListFilter(): AlertListFilter {
  return {
    page: 1,
    pageSize: DEFAULT_PAGE_SIZE,
    sortBy: 'detectedAtUtc',
    sortDescending: true,
  };
}

/** The Operational Analytics route path (FS-15 §7, IP-17 T-10), defined once so the shell's
 *  navigation and `app.routes.ts` agree — mirrors `monitoring.routes.ts`'s pattern. */
export const ANALYTICS_ROUTE = '/analytics';

/**
 * The query-param names carrying the page's shared filter state.
 *
 * Filter state lives in the URL, not in component fields, for the same reason the Alert list's does
 * (FS-10 §6): a refresh, a bookmark, or the back button then restores exactly what was on screen, and
 * there is one source of truth for "what is currently being shown". A Branch is always identified by
 * its `BranchId` GUID here, never by its display name (FS-15 §4.2).
 */
export const ANALYTICS_RANGE_QUERY_PARAM = 'range';
export const ANALYTICS_BRANCH_ID_QUERY_PARAM = 'branchId';
export const ANALYTICS_DETECTION_TYPE_QUERY_PARAM = 'detectionType';

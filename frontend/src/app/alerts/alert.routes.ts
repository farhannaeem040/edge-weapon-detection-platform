/**
 * The Alert route paths, defined once so the shell's navigation and the list view's per-row links
 * cannot drift apart from the routes themselves (FS-10 §5; IP-12 T-212). Mirrors `branch.routes.ts`.
 */

/** The Alert list (FS-10 §6). */
export const ALERTS_ROUTE = '/alerts';

/** The route parameter naming the Alert on the detail route (`/alerts/:alertId`). */
export const ALERT_ID_PARAM = 'alertId';

/** The detail route for one Alert. */
export const alertDetailRoute = (alertId: string): string => `${ALERTS_ROUTE}/${alertId}`;

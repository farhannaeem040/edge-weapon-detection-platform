/** The global Monitoring route path (FS-14 §5, IP-16 UI enhancement), defined once so the shell's
 *  navigation and `app.routes.ts` agree — mirrors `dashboard.routes.ts`'s pattern. */
export const MONITORING_ROUTE = '/monitoring';

/** Query param naming the selected Branch, so a future feature (e.g. Alert navigation) could target
 *  this page pre-selected without inventing a second param name — mirrors
 *  `dashboard.routes.ts`'s `BRANCH_ID_QUERY_PARAM`. Not currently written to by anything; the
 *  existing Alert → Branch Live Monitoring tab navigation is left exactly as it already works
 *  (FS-14 §5, IP-16 T-13), per this task's explicit instruction not to touch it. */
export const MONITORING_BRANCH_ID_QUERY_PARAM = 'branchId';

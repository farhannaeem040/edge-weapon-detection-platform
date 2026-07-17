/**
 * The branch route paths, defined once so the login landing (T-23), the dashboard's navigation, and
 * the list view's per-branch links cannot drift apart from the routes themselves.
 */

/** The branch list (FS-02 §10.3). */
export const BRANCHES_ROUTE = '/branches';

/** The route parameter naming the branch on the detail route (`/branches/:branchId`). */
export const BRANCH_ID_PARAM = 'branchId';

/**
 * The branch-creation route (FS-02 §10.1, IP-01 T-27).
 *
 * `new` is a literal segment sharing a prefix with `/branches/:branchId`, so its route must be
 * declared first — the router matches in order and `:branchId` would otherwise swallow it.
 */
export const BRANCH_CREATE_ROUTE = `${BRANCHES_ROUTE}/new`;

/** The detail route for one branch. */
export const branchDetailRoute = (branchId: string): string => `${BRANCHES_ROUTE}/${branchId}`;

/**
 * The edit route for one branch (FS-03 §10.1, IP-03 T-45): `/branches/:branchId/edit`.
 *
 * Its `edit` segment sits below `:branchId`, so — unlike the `new` literal — it cannot be swallowed
 * by the detail route (that route matches exactly two path segments; this one has three).
 */
export const branchEditRoute = (branchId: string): string => `${BRANCHES_ROUTE}/${branchId}/edit`;

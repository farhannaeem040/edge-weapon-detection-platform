/**
 * The branch route paths, defined once so the login landing (T-23), the dashboard's navigation, and
 * the list view's per-branch links cannot drift apart from the routes themselves.
 */

/** The branch list (FS-02 §10.3). */
export const BRANCHES_ROUTE = '/branches';

/** The route parameter naming the branch on the detail route (`/branches/:branchId`). */
export const BRANCH_ID_PARAM = 'branchId';

/** The detail route for one branch. */
export const branchDetailRoute = (branchId: string): string => `${BRANCHES_ROUTE}/${branchId}`;

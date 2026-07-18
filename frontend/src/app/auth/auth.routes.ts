/**
 * The two route paths the authentication flow depends on, defined once so the login view, the
 * route guard (T-24), and the session-expiry handler (T-25) cannot drift apart.
 */

/** The public login view (FS-01 §7). */
export const LOGIN_ROUTE = '/login';

/**
 * Where a successful login lands: the branch list (IP-01 T-26). It pointed at the placeholder
 * protected shell until the branch views existed; branch management is what the Dashboard is for
 * (FS-02 §10.3), so it is now the destination an Admin is dropped at.
 */
export const PROTECTED_LANDING_ROUTE = '/branches';

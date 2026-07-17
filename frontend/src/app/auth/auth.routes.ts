/**
 * The two route paths the authentication flow depends on, defined once so the login view, the
 * route guard (T-24), and the session-expiry handler (T-25) cannot drift apart.
 */

/** The public login view (FS-01 §7). */
export const LOGIN_ROUTE = '/login';

/**
 * Where a successful login lands. It currently points at the placeholder protected shell; T-26
 * introduces the branch views that become the real destination.
 */
export const PROTECTED_LANDING_ROUTE = '/dashboard';

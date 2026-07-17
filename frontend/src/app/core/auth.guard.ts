import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';

import { AuthService } from '../auth/auth.service';
import { LOGIN_ROUTE } from '../auth/auth.routes';

/**
 * Keeps protected views from rendering when no local session is held (FS-01 §7, IP-01 T-24).
 *
 * **This is a user-experience control only — it is not the security boundary** (FS-01 §10, §13
 * AC-3). It inspects browser storage, nothing more. A copied, expired, forged, or revoked token
 * still satisfies it, and a user can bypass it outright by calling the API directly. Protected
 * data is safe because the Backend independently validates the JWT *and* the corresponding
 * non-revoked `AdminSession` on every request (FS-01 §5.3, §12) — never because of this guard.
 */
export const authGuard: CanActivateFn = () => {
  const router = inject(Router);

  if (inject(AuthService).isAuthenticated()) {
    return true;
  }

  // A UrlTree, rather than an imperative navigate() call, so the router treats the redirect as
  // part of resolving this navigation instead of racing a second one against it.
  return router.parseUrl(LOGIN_ROUTE);
};

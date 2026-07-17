import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';

import { AuthService } from '../auth/auth.service';
import { LOGIN_ROUTE } from '../auth/auth.routes';
import { environment } from '../../environments/environment';

/**
 * Treats a 401 from a protected Backend call as a logged-out state (FS-01 §5.5, §7, IP-01 T-25).
 *
 * The Backend rejects an expired or revoked session with a 401 before any business logic runs.
 * Since the Dashboard cannot know a session died until it is told, this is what closes the loop:
 * it discards the dead token and returns the Admin to the login view.
 *
 * Only 401 does this. A 403, 404, 409, or 500 means the session was accepted and something else
 * went wrong, so the token is left untouched — logging a user out over an unrelated server fault
 * would destroy a session that is still perfectly valid.
 */
export const sessionExpiryInterceptor: HttpInterceptorFn = (request, next) => {
  const authService = inject(AuthService);
  const router = inject(Router);

  return next(request).pipe(
    catchError((error: unknown) => {
      if (
        error instanceof HttpErrorResponse &&
        error.status === 401 &&
        isSessionBearingRequest(request.url)
      ) {
        // Clearing is unconditional and idempotent, so concurrent 401s from several in-flight
        // requests converge on the same logged-out state rather than fighting each other.
        authService.clearToken();

        // No logout request is issued from here. The session is already gone — that is what the
        // 401 means — so calling logout would earn another 401 and, from this same handler, spiral.
        if (!isOnLoginView(router)) {
          // Guarded so that repeated 401s cannot queue redundant navigations to a view already open.
          void router.navigateByUrl(LOGIN_ROUTE);
        }
      }

      // Rethrown either way: the caller still needs to handle its own failure, and this
      // interceptor deliberately surfaces no Backend error detail of its own.
      return throwError(() => error);
    }),
  );
};

/**
 * Whether a 401 from this URL means *the Admin's session* died.
 *
 * Login's own 401 is an invalid-credentials answer, not an expired session — there is no session
 * to lose, and treating it as one would clear state and redirect while `LoginComponent` is already
 * showing the failure. Activation is the Jetson Agent's anonymous endpoint (FS-01 §6): its 401
 * concerns an Activation Key, and must never destroy an unrelated Admin session. A non-Backend
 * origin's 401 says nothing about this application at all.
 */
function isSessionBearingRequest(url: string): boolean {
  const base = new URL(environment.apiBaseUrl, document.baseURI);
  const target = new URL(url, document.baseURI);

  if (target.origin !== base.origin || !target.pathname.startsWith(base.pathname)) {
    return false;
  }

  const path = target.pathname.slice(base.pathname.length);

  return !['/auth/login', '/activate'].some(
    (endpoint) => path === endpoint || path.startsWith(`${endpoint}/`),
  );
}

function isOnLoginView(router: Router): boolean {
  return router.url.split('?')[0] === LOGIN_ROUTE;
}

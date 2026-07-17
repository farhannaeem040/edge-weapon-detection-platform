import { HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';

import { AuthService } from '../auth/auth.service';
import { environment } from '../../environments/environment';

/**
 * Attaches the Admin session's Bearer token to Backend API requests (FS-01 §7, IP-01 T-24).
 *
 * The token is only ever attached to this application's own Backend, never to an unrelated
 * third-party origin — sending it elsewhere would disclose the session to whoever answers.
 * Nothing here logs the token or its presence.
 */
export const authInterceptor: HttpInterceptorFn = (request, next) => {
  const token = inject(AuthService).getToken();

  if (
    !token ||
    !isBackendApiRequest(request.url) ||
    isAnonymousEndpoint(request.url) ||
    request.headers.has('Authorization')
  ) {
    // No token, a non-Backend origin, an endpoint that authenticates no Admin session, or a
    // caller that set its own Authorization header: the request goes out exactly as it was built.
    return next(request);
  }

  return next(
    request.clone({
      setHeaders: { Authorization: `Bearer ${token}` },
    }),
  );
};

/**
 * Whether a URL addresses this application's Backend API.
 *
 * `environment.apiBaseUrl` is host-relative (`/api/v1`) in both environments — the co-located
 * production deployment and the development server's proxy (ARCH-001 ARCH-ASM-001) — so a Backend
 * call is a same-origin request under that path. An absolute URL is resolved against the current
 * document and matched on its origin *and* path, so that a third-party host cannot collect the
 * token merely by exposing a lookalike `/api/v1` path.
 */
function isBackendApiRequest(url: string): boolean {
  const base = new URL(environment.apiBaseUrl, document.baseURI);
  const target = new URL(url, document.baseURI);

  return target.origin === base.origin && target.pathname.startsWith(base.pathname);
}

/**
 * Backend endpoints that are exempt from Admin JWT authentication (FS-01 §6, IP-01 §10 step 2):
 * `login` is what issues a session, and `activate` is the Jetson Agent's own Activation Key flow.
 * Neither authenticates an Admin session, so neither carries the Bearer header.
 */
const ANONYMOUS_ENDPOINT_PATHS = ['/auth/login', '/activate'];

function isAnonymousEndpoint(url: string): boolean {
  const base = new URL(environment.apiBaseUrl, document.baseURI);
  const path = new URL(url, document.baseURI).pathname.slice(base.pathname.length);

  return ANONYMOUS_ENDPOINT_PATHS.some(
    (endpoint) => path === endpoint || path.startsWith(`${endpoint}/`),
  );
}
